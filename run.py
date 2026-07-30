import logging
import logging.handlers
import os
import signal
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.events import EVENT_JOB_ERROR
from dotenv import load_dotenv

load_dotenv()

ET = ZoneInfo("America/New_York")
VERSION = "0.1.0"

logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)

# Configure runner log with rotation
handler = logging.handlers.RotatingFileHandler(
    logs_dir / "runner.log", maxBytes=5 * 1024 * 1024, backupCount=5
)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger = logging.getLogger("runner")
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# Sanity check: paper flag vs live port
PAPER_TRADING = os.getenv("PAPER_TRADING", "false").lower() == "true"
IBKR_PORT = int(os.getenv("IBKR_PORT", "7497"))
if PAPER_TRADING and IBKR_PORT in {7496, 4001}:
    print("ERROR: PAPER_TRADING=true but IBKR_PORT is a live port. Fix .env and restart.")
    sys.exit(1)
if not PAPER_TRADING and IBKR_PORT in {7497, 11002}:
    print("ERROR: PAPER_TRADING=false but IBKR_PORT is a paper port. Fix .env and restart.")
    sys.exit(1)


def _safe_job(name, fn):
    """Wrap a job function to catch all exceptions."""
    def wrapper():
        try:
            fn()
        except Exception as exc:
            tb = traceback.format_exc()
            logger.error(f"Job {name} crashed:\n{tb}")
            try:
                from src.notify import notify
                notify(f"Job crashed", f"{name}: {str(exc)[:500]}", "high")
            except Exception:
                pass
    wrapper.__name__ = name
    return wrapper


def _start_webui():
    """Start uvicorn in a background thread."""
    try:
        import uvicorn
        config = uvicorn.Config(
            "webui:app",
            host="127.0.0.1",
            port=8000,
            reload=False,
            workers=1,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True, name="webui")
        thread.start()
        logger.info("Web UI started at http://127.0.0.1:8000")
    except ImportError:
        logger.warning("fastapi/uvicorn not installed — web UI disabled")
    except Exception as exc:
        logger.error(f"Web UI failed to start: {exc}")


def main():
    import morning_prefilter
    import cycle
    import compute_perf
    import rotate_logs
    import nightly_report

    scheduler = BlockingScheduler(timezone=ET)

    # run_prefilter: mon-fri at 09:25, 09:55, 10:25, 10:55, 11:25, 11:55, 12:25, 12:55
    scheduler.add_job(
        _safe_job("run_prefilter", morning_prefilter.main),
        trigger="cron",
        day_of_week="mon-fri",
        hour="9,10,11,12",
        minute="25,55",
        misfire_grace_time=60,
        max_instances=1,
        id="run_prefilter",
    )

    # run_cycle: every 5 minutes
    scheduler.add_job(
        _safe_job("run_cycle", cycle.main),
        trigger="interval",
        minutes=5,
        misfire_grace_time=30,
        max_instances=1,
        id="run_cycle",
    )

    # run_dashboard: mon-fri at 16:05 ET
    scheduler.add_job(
        _safe_job("run_dashboard", compute_perf.main),
        trigger="cron",
        day_of_week="mon-fri",
        hour=16,
        minute=5,
        misfire_grace_time=60,
        max_instances=1,
        id="run_dashboard",
    )

    # run_nightly_report: mon-fri at 16:30 ET
    scheduler.add_job(
        _safe_job("run_nightly_report", nightly_report.main),
        trigger="cron",
        day_of_week="mon-fri",
        hour=16,
        minute=30,
        misfire_grace_time=120,
        max_instances=1,
        id="run_nightly_report",
    )

    # run_rotate: daily at 09:25 ET
    scheduler.add_job(
        _safe_job("run_rotate", rotate_logs.main),
        trigger="cron",
        hour=9,
        minute=25,
        misfire_grace_time=60,
        max_instances=1,
        id="run_rotate",
    )

    # Graceful shutdown on SIGINT/SIGTERM
    def _shutdown(signum, frame):
        logger.info("runner stopped")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Ensure reports directory exists
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)

    # Print banner (Windows-safe: fallback to ASCII if Unicode fails)
    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    watchlist_status = "PRESENT" if Path("watchlist.txt").exists() else "MISSING (run morning_prefilter first)"
    try:
        print(f"╔══════════════════════════════════════╗")
        print(f"║  little_greed runner v{VERSION}           ║")
        print(f"║  {now}         ║")
        print(f"║  watchlist: {watchlist_status[:24]:<24} ║")
        print(f"╚══════════════════════════════════════╝")
    except UnicodeEncodeError:
        print("=" * 40)
        print(f"little_greed runner v{VERSION}")
        print(f"{now}")
        print(f"watchlist: {watchlist_status[:24]}")
        print("=" * 40)
    print("Press Ctrl+C to stop.")

    logger.info(f"runner started v{VERSION}")

    _start_webui()
    scheduler.start()


if __name__ == "__main__":
    main()
