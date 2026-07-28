import asyncio
import csv
import json
import os
import re
import shutil
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

load_dotenv()

ET = ZoneInfo("America/New_York")
_executor = ThreadPoolExecutor(max_workers=4)

app = FastAPI(title="little_greed", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _read_positions() -> list:
    path = Path("open_positions.json")
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()) or []
    except Exception:
        return []


def _read_last_cycle_time() -> str | None:
    path = Path("safety-check-log.json")
    if not path.exists():
        return None
    try:
        for line in reversed(path.read_text().strip().splitlines()):
            obj = json.loads(line)
            ts = obj.get("ts") or obj.get("timestamp")
            if ts:
                return ts
    except Exception:
        pass
    return None


def _read_today_pnl() -> float:
    today = datetime.now(ET).date()
    path = Path("trades.csv")
    if not path.exists():
        return 0.0
    total = 0.0
    try:
        with open(path) as f:
            for row in csv.DictReader(f):
                if not row.get("timestamp_iso", "").startswith(str(today)):
                    continue
                side = row.get("side", "").upper()
                fill = float(row.get("fill_price", 0) or 0)
                size = int(row.get("size", 0) or 0)
                if side == "SELL":
                    total += fill * size
                elif side == "BUY":
                    total -= fill * size
    except Exception:
        pass
    return total


def _fetch_prices_sync(symbols: list, client_id: int, timeout_s: int = 3) -> tuple:
    if not symbols:
        return {}, True
    try:
        from ib_async import IB, Stock
        ib = IB()
        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "7497"))
        ib.connect(host, port, clientId=client_id)
        prices = {}
        try:
            contracts = [Stock(s, "SMART", "USD") for s in symbols]
            ib.qualifyContracts(*contracts)
            tickers = ib.reqMktData(contracts)
            if not isinstance(tickers, list):
                tickers = [tickers]
            time.sleep(1)
            for sym, ticker in zip(symbols, tickers):
                price = ticker.last or ticker.bid or ticker.close or 0.0
                prices[sym] = float(price)
        finally:
            ib.disconnect()
        return prices, True
    except Exception:
        return {}, False


def _get_yf_prices(symbols: list) -> dict:
    """Fetch last prices via yfinance (market hours = last trade, AH = close)."""
    if not symbols:
        return {}
    prices = {}
    try:
        import yfinance as yf
        yahoo_syms = [s.replace(" B", "-B").replace(" A", "-A") for s in symbols]
        data = yf.download(
            tickers=" ".join(yahoo_syms), period="1d", interval="5m",
            group_by="ticker", progress=False, auto_adjust=True, threads=False,
        )
        for sym, yahoo in zip(symbols, yahoo_syms):
            try:
                bars = data[yahoo] if yahoo in data.columns.get_level_values(0) else data
                prices[sym] = float(bars["Close"].dropna().iloc[-1])
            except Exception:
                pass
    except Exception:
        pass
    return prices


def _build_positions_data() -> dict:
    positions = _read_positions()
    now_et = datetime.now(ET)
    symbols = [p["symbol"] for p in positions]

    # Try IBKR first, fall back to yfinance
    prices, ibkr_ok = _fetch_prices_sync(symbols, client_id=90)
    if not prices and symbols:
        prices = _get_yf_prices(symbols)
        ibkr_ok = False

    enriched = []
    for pos in positions:
        sym = pos["symbol"]
        entry = pos.get("entry_price", 0)
        qty = pos.get("qty", 0)
        stop = pos.get("initial_stop", 0)
        current = prices.get(sym, 0)
        unreal_usd = (current - entry) * qty if current else 0
        unreal_pct = (current - entry) / entry * 100 if entry and current else 0
        dist_stop_pct = (current - stop) / current * 100 if current and stop else 0
        hold_mins = 0
        try:
            entry_dt = datetime.fromisoformat(pos.get("entry_time_iso", ""))
            hold_mins = int((now_et - entry_dt).total_seconds() / 60)
        except Exception:
            pass
        R = pos.get("R", 0)
        r_multiple = round((current - entry) / R, 2) if R and current else 0
        enriched.append({
            "symbol": sym, "qty": qty, "entry": entry, "current": current,
            "unreal_usd": unreal_usd, "unreal_pct": unreal_pct,
            "stop": stop, "dist_stop_pct": dist_stop_pct,
            "hold_mins": hold_mins, "near_stop": 0 <= dist_stop_pct < 0.5,
            "r_multiple": r_multiple,
        })
    return {"positions": enriched, "ibkr_ok": ibkr_ok, "now": now_et}


def _build_strategy_panel() -> dict:
    """Read last signal evaluations from safety_log.jsonl and return filter breakdown."""
    path = Path("logs/safety_log.jsonl")
    if not path.exists():
        return {"signals": [], "strategy": "gap_and_go", "thresholds": {}}

    rules = {}
    try:
        rules = json.loads(Path("rules.json").read_text())
    except Exception:
        pass

    strategy_name = rules.get("strategy_name_key", "gap_and_go")
    thresholds = {
        "gap_pct":   rules.get("daily_filters", {}).get("D3_min_gap_pct_from_prior_close", 3.0),
        "rvol":      rules.get("intraday_filters", {}).get("I3_rvol_min", 2.0),
        "sma200_ok": True,
    }

    signals = []
    try:
        lines = path.read_text().strip().splitlines()
        for line in reversed(lines[-50:]):
            try:
                obj = json.loads(line)
                if obj.get("strategy") == strategy_name:
                    signals.append(obj)
                    if len(signals) >= 10:
                        break
            except Exception:
                pass
    except Exception:
        pass

    return {"signals": signals, "strategy": strategy_name, "thresholds": thresholds}


def _flatten_sync(client_id: int = 91) -> dict:
    from ib_async import IB, MarketOrder
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "7497"))
    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id)
        open_orders = ib.reqOpenOrders()
        cancelled = 0
        for order in open_orders:
            try:
                ib.cancelOrder(order)
                cancelled += 1
            except Exception:
                pass
        positions = ib.positions()
        killed = 0
        for pos in positions:
            if pos.position == 0:
                continue
            side = "SELL" if pos.position > 0 else "BUY"
            order = MarketOrder(side, abs(int(pos.position)))
            order.outsideRth = True
            ib.placeOrder(pos.contract, order)
            killed += 1
        if killed > 0:
            deadline = time.time() + 10
            while time.time() < deadline:
                time.sleep(0.5)
        tmp = Path("open_positions.json.tmp")
        tmp.write_text("[]")
        os.replace(tmp, Path("open_positions.json"))
        event = {"event": "manual_kill", "count": killed, "ts": datetime.now(timezone.utc).isoformat()}
        with open("safety-check-log.json", "a") as f:
            f.write(json.dumps(event) + "\n")
        try:
            from src.notify import notify
            notify("MANUAL KILL", f"flattened {killed} positions", "high")
        except Exception:
            pass
        return {"killed": killed, "orders_cancelled": cancelled, "ok": True}
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _parse_env_file() -> dict:
    path = Path(".env")
    data = {}
    if not path.exists():
        return data
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, val = stripped.partition("=")
            data[key.strip()] = val.strip()
    return data


def _write_env_file(data: dict):
    lines = [f"{k}={v}" for k, v in data.items()]
    tmp = Path(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, Path(".env"))


def _write_rules_file(data: dict):
    tmp = Path("rules.json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, Path("rules.json"))


# ---------------------------------------------------------------------------
# Signals helpers
# ---------------------------------------------------------------------------

def _tail_signals(n: int = 1000) -> tuple:
    path = Path("logs/signals.jsonl")
    if not path.exists():
        return [], 0
    lines = deque(maxlen=n)
    malformed = 0
    try:
        with open(path, "r") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    lines.append(json.loads(raw))
                except Exception:
                    malformed += 1
    except Exception:
        pass
    return list(lines), malformed


def _filter_signals(signals: list, date: str, symbol: str, outcome: str) -> list:
    result = []
    for s in reversed(signals):
        ts = s.get("ts", "")
        if date and not ts.startswith(date):
            continue
        sym = s.get("symbol", "")
        if symbol and symbol.upper() not in sym.upper():
            continue
        if outcome and outcome != "all" and s.get("outcome") != outcome:
            continue
        result.append(s)
        if len(result) >= 500:
            break
    return result


# ---------------------------------------------------------------------------
# Preflight helpers
# ---------------------------------------------------------------------------

def _run_check_with_timeout(fn, timeout_s: int = 5) -> dict:
    future = _executor.submit(fn)
    try:
        return future.result(timeout=timeout_s)
    except FuturesTimeout:
        return {"status": "fail", "detail": "timed out", "fix_hint": ""}
    except Exception as exc:
        return {"status": "fail", "detail": str(exc)[:200], "fix_hint": ""}


def _check_python_version():
    ok = sys.version_info >= (3, 12)
    return {
        "name": "Python version",
        "status": "pass" if ok else "fail",
        "detail": f"Python {sys.version.split()[0]}",
        "fix_hint": "Install Python 3.12 or newer from python.org",
    }


def _check_packages():
    missing = []
    for pkg in ["ib_async", "dotenv", "fastapi", "pandas", "yfinance", "apscheduler"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return {
        "name": "Required packages",
        "status": "pass" if not missing else "fail",
        "detail": f"Missing: {', '.join(missing)}" if missing else "All packages installed",
        "fix_hint": "Run: pip install -r requirements.txt",
    }


def _check_files():
    required = ["bot.py", "cycle.py", "run.py", "rules.json", ".env"]
    warn_files = ["watchlist.txt"]
    missing = [f for f in required if not Path(f).exists()]
    warns = []
    for wf in warn_files:
        p = Path(wf)
        if not p.exists():
            warns.append(f"{wf} missing")
        else:
            age_h = (time.time() - p.stat().st_mtime) / 3600
            if age_h > 24:
                warns.append(f"{wf} is {age_h:.0f}h old")
    if missing:
        return {"name": "Required files", "status": "fail",
                "detail": f"Missing: {', '.join(missing)}", "fix_hint": "Re-run setup steps"}
    if warns:
        return {"name": "Required files", "status": "warn",
                "detail": "; ".join(warns), "fix_hint": "Run: python morning_prefilter.py"}
    return {"name": "Required files", "status": "pass", "detail": "All files present", "fix_hint": ""}


def _check_env_sanity():
    env = _parse_env_file()
    paper = env.get("PAPER_TRADING", "false").lower() == "true"
    port = int(env.get("IBKR_PORT", "7497"))
    if paper and port in {7496, 4001}:
        return {"name": ".env sanity", "status": "fail",
                "detail": "PAPER_TRADING=true but port is a live port",
                "fix_hint": "Open Settings and align port with paper flag"}
    if not paper and port in {7497, 11002}:
        return {"name": ".env sanity", "status": "warn",
                "detail": "PAPER_TRADING=false but port looks like paper",
                "fix_hint": "Open Settings and align port with paper flag"}
    return {"name": ".env sanity", "status": "pass", "detail": f"port={port}, paper={paper}", "fix_hint": ""}


def _check_tws_reachable():
    try:
        from ib_async import IB
        ib = IB()
        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "7497"))
        ib.connect(host, port, clientId=92)
        ib.disconnect()
        return {"name": "TWS reachable", "status": "pass",
                "detail": f"Connected to {host}:{port}", "fix_hint": ""}
    except Exception as exc:
        return {"name": "TWS reachable", "status": "fail",
                "detail": str(exc)[:100],
                "fix_hint": "Start TWS or IB Gateway and log in"}


def _check_ibkr_api():
    try:
        from ib_async import IB
        ib = IB()
        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "7497"))
        ib.connect(host, port, clientId=92)
        accounts = ib.managedAccounts()
        ib.disconnect()
        if accounts:
            return {"name": "IBKR API enabled", "status": "pass",
                    "detail": f"Accounts: {', '.join(accounts)}", "fix_hint": ""}
        return {"name": "IBKR API enabled", "status": "fail",
                "detail": "No managed accounts returned",
                "fix_hint": "In TWS: Global Config > API > Settings, enable ActiveX and Socket Clients"}
    except Exception as exc:
        return {"name": "IBKR API enabled", "status": "fail",
                "detail": str(exc)[:100],
                "fix_hint": "In TWS: Global Config > API > Settings, enable ActiveX and Socket Clients"}


def _check_paper_account():
    try:
        from ib_async import IB
        ib = IB()
        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "7497"))
        ib.connect(host, port, clientId=92)
        accounts = ib.managedAccounts()
        ib.disconnect()
        if accounts and accounts[0].startswith("DU"):
            return {"name": "Paper account", "status": "pass",
                    "detail": f"Account {accounts[0]} looks like paper (DU prefix)", "fix_hint": ""}
        return {"name": "Paper account", "status": "warn",
                "detail": f"Account {accounts[0] if accounts else '?'} — may be live account",
                "fix_hint": "Verify you are connected to a paper trading account"}
    except Exception as exc:
        return {"name": "Paper account", "status": "fail",
                "detail": str(exc)[:100], "fix_hint": ""}


def _check_telegram_configured():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        return {"name": "Telegram configured", "status": "pass",
                "detail": "Token and chat ID are set", "fix_hint": ""}
    missing = []
    if not token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    return {"name": "Telegram configured", "status": "warn",
            "detail": f"Missing: {', '.join(missing)}",
            "fix_hint": "Open Settings and add your Telegram credentials"}


def _check_telegram_working():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return {"name": "Telegram working", "status": "warn",
                "detail": "Skipped — not configured", "fix_hint": "Configure Telegram first"}
    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "little_greed preflight test ✓"},
            timeout=5,
        )
        if resp.status_code == 200:
            return {"name": "Telegram working", "status": "pass",
                    "detail": "Test message sent", "fix_hint": ""}
        return {"name": "Telegram working", "status": "fail",
                "detail": f"HTTP {resp.status_code}",
                "fix_hint": "Check bot token and chat ID"}
    except Exception as exc:
        return {"name": "Telegram working", "status": "fail",
                "detail": str(exc)[:100],
                "fix_hint": "Check bot token and chat ID"}


def _check_disk_space():
    try:
        usage = shutil.disk_usage(".")
        free_mb = usage.free / 1024 / 1024
        if free_mb >= 500:
            return {"name": "Disk space", "status": "pass",
                    "detail": f"{free_mb:.0f} MB free", "fix_hint": ""}
        return {"name": "Disk space", "status": "fail",
                "detail": f"Only {free_mb:.0f} MB free",
                "fix_hint": "Free up disk space"}
    except Exception as exc:
        return {"name": "Disk space", "status": "fail",
                "detail": str(exc), "fix_hint": ""}


def _check_runner_alive():
    try:
        import psutil
        for proc in psutil.process_iter(["cmdline"]):
            try:
                cmdline = " ".join(proc.info["cmdline"] or [])
                if "run.py" in cmdline:
                    return {"name": "Runner alive", "status": "pass",
                            "detail": f"run.py process found (pid {proc.pid})", "fix_hint": ""}
            except Exception:
                pass
        return {"name": "Runner alive", "status": "warn",
                "detail": "run.py not found in process list",
                "fix_hint": "Start the bot: python run.py"}
    except ImportError:
        return {"name": "Runner alive", "status": "warn",
                "detail": "psutil not installed — cannot check",
                "fix_hint": "pip install psutil"}


def _run_all_preflight_checks() -> list:
    checks = [
        _check_python_version,
        _check_packages,
        _check_files,
        _check_env_sanity,
        _check_tws_reachable,
        _check_ibkr_api,
        _check_paper_account,
        _check_telegram_configured,
        _check_telegram_working,
        _check_disk_space,
        _check_runner_alive,
    ]
    results = []
    for check_fn in checks:
        result = _run_check_with_timeout(check_fn, timeout_s=5)
        if "name" not in result:
            result["name"] = check_fn.__name__.replace("_check_", "").replace("_", " ").title()
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Routes — root
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard")


@app.get("/time", response_class=PlainTextResponse)
async def current_time():
    return datetime.now(ET).strftime("%H:%M:%S ET")


# ---------------------------------------------------------------------------
# Routes — dashboard
# ---------------------------------------------------------------------------

@app.get("/dashboard")
async def dashboard(request: Request):
    last_cycle = _read_last_cycle_time()
    today_pnl = _read_today_pnl()
    positions = _read_positions()
    last_cycle_age_min = None
    if last_cycle:
        try:
            lc_dt = datetime.fromisoformat(last_cycle)
            if lc_dt.tzinfo is None:
                lc_dt = lc_dt.replace(tzinfo=ET)
            last_cycle_age_min = int((datetime.now(ET) - lc_dt).total_seconds() / 60)
        except Exception:
            pass
    rules = {}
    try:
        rules = json.loads(Path("rules.json").read_text())
    except Exception:
        pass
    active_strategies = rules.get("active_strategies", ["gap_and_go"])
    return templates.TemplateResponse(request, "dashboard.html", {
        "last_cycle": last_cycle,
        "last_cycle_age_min": last_cycle_age_min,
        "today_pnl": today_pnl,
        "open_count": len(positions),
        "active_strategies": active_strategies,
    })


@app.get("/dashboard/strategy-panel", response_class=HTMLResponse)
async def dashboard_strategy_panel(request: Request):
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(_executor, _build_strategy_panel)
    return templates.TemplateResponse(request, "_strategy_panel.html", data)


def _live_scan() -> dict:
    """Real-time strategy scan of the current watchlist via IBKR."""
    watchlist = []
    try:
        for line in Path("watchlist.txt").read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                watchlist.append(line.split()[0])
    except Exception:
        return {"signals": [], "strategy": "gap_and_go", "thresholds": {}, "error": "watchlist.txt missing"}

    rules = {}
    try:
        rules = json.loads(Path("rules.json").read_text())
    except Exception:
        pass

    thresholds = {
        "gap_pct": rules.get("daily_filters", {}).get("D3_min_gap_pct_from_prior_close", 1.5),
        "rvol":    rules.get("intraday_filters", {}).get("I3_rvol_min", 1.2),
    }

    signals = []
    try:
        from ib_async import IB
        import strategy as strat
        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "7497"))
        ib = IB()
        ib.connect(host, port, clientId=93)
        try:
            for sym in watchlist[:20]:
                try:
                    r = strat.evaluate(sym, ib)
                    signals.append(r)
                except Exception as e:
                    signals.append({"symbol": sym, "pass": False, "reasons": [str(e)],
                                    "filters": {}, "values": {}, "strategy": "gap_and_go"})
        finally:
            ib.disconnect()
    except Exception as e:
        return {"signals": [], "strategy": "gap_and_go", "thresholds": thresholds,
                "error": f"IBKR connection failed: {e}"}

    return {"signals": signals, "strategy": rules.get("strategy_name_key", "gap_and_go"),
            "thresholds": thresholds, "error": None}


@app.get("/dashboard/live-scan", response_class=HTMLResponse)
async def dashboard_live_scan(request: Request):
    loop = asyncio.get_event_loop()
    data = await asyncio.wait_for(
        loop.run_in_executor(_executor, _live_scan), timeout=120
    )
    return templates.TemplateResponse(request, "_strategy_panel.html", {
        "request": request, **data
    })


@app.get("/dashboard/activity", response_class=HTMLResponse)
async def dashboard_activity(request: Request):
    """Live activity feed from safety-check-log.json + trades.csv."""
    events = []

    # Read safety-check-log.json
    log = Path("safety-check-log.json")
    if log.exists():
        try:
            for line in log.read_text().strip().splitlines()[-50:]:
                try:
                    obj = json.loads(line)
                    action = obj.get("action", obj.get("event", ""))
                    ts = obj.get("ts", "")
                    if ts:
                        events.append({"ts": ts, "type": action, "data": obj})
                except Exception:
                    pass
        except Exception:
            pass

    # Read trades.csv for recent fills
    trades_path = Path("trades.csv")
    if trades_path.exists():
        try:
            with open(trades_path) as f:
                for row in csv.DictReader(f):
                    ts = row.get("timestamp_iso", "")
                    if ts and row.get("fill_price") and float(row.get("fill_price", 0)) > 0:
                        events.append({"ts": ts, "type": f"fill_{row['side'].lower()}",
                                       "data": row})
        except Exception:
            pass

    # Sort by timestamp descending, cap at 40
    events.sort(key=lambda x: x["ts"], reverse=True)
    events = events[:40]

    return templates.TemplateResponse(request, "_activity_feed.html", {
        "events": events,
    })


@app.get("/dashboard/position-chart")
async def dashboard_position_chart():
    """Per-position P&L data for bar chart."""
    positions = _read_positions()
    if not positions:
        return JSONResponse({"labels": [], "pnl": [], "entries": [], "stops": [], "colors": []})

    symbols = [p["symbol"] for p in positions]
    prices = _get_yf_prices(symbols)

    labels, pnl_vals, entry_vals, stop_vals, colors, r_vals = [], [], [], [], [], []
    for p in positions:
        sym = p["symbol"]
        entry = p.get("entry_price", 0)
        stop = p.get("initial_stop", 0)
        R = p.get("R", 0)
        qty = p.get("qty", 0)
        current = prices.get(sym, entry)
        pnl = (current - entry) * qty
        r_mult = (current - entry) / R if R else 0

        labels.append(sym)
        pnl_vals.append(round(pnl, 2))
        entry_vals.append(entry)
        stop_vals.append(stop)
        r_vals.append(round(r_mult, 2))
        colors.append("#10b981" if pnl >= 0 else "#ef4444")

    return JSONResponse({
        "labels": labels, "pnl": pnl_vals, "entries": entry_vals,
        "stops": stop_vals, "colors": colors, "r_multiples": r_vals
    })


@app.get("/dashboard/chart-data")
async def dashboard_chart_data():
    """Return cumulative P&L by day for Chart.js."""
    path = Path("trades.csv")
    if not path.exists():
        return JSONResponse({"labels": [], "values": []})
    daily = {}
    try:
        with open(path) as f:
            for row in csv.DictReader(f):
                ts = row.get("timestamp_iso", "")[:10]
                if not ts:
                    continue
                side = row.get("side", "").upper()
                fill = float(row.get("fill_price", 0) or 0)
                size = int(row.get("size", 0) or 0)
                delta = fill * size if side == "SELL" else -fill * size
                daily[ts] = daily.get(ts, 0) + delta
    except Exception:
        pass
    sorted_days = sorted(daily)
    cumulative, running = [], 0.0
    for d in sorted_days:
        running += daily[d]
        cumulative.append(round(running, 2))
    return JSONResponse({"labels": sorted_days, "values": cumulative})


@app.get("/dashboard/trades")
async def dashboard_trades():
    """Return last 50 trade rows as JSON."""
    path = Path("trades.csv")
    if not path.exists():
        return JSONResponse([])
    rows = []
    try:
        with open(path) as f:
            for row in csv.DictReader(f):
                rows.append(row)
    except Exception:
        pass
    return JSONResponse(rows[-50:])


@app.get("/dashboard/data", response_class=HTMLResponse)
async def dashboard_data(request: Request):
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(_executor, _build_positions_data)
    return templates.TemplateResponse(request, "_positions_table.html", {**data})


# ---------------------------------------------------------------------------
# Routes — kill switch
# ---------------------------------------------------------------------------

@app.get("/kill")
async def kill_get(request: Request):
    return templates.TemplateResponse(request, "kill.html", {
        "open_count": len(_read_positions()),
    })


@app.post("/kill")
async def kill_post():
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_executor, _flatten_sync), timeout=10)
        return JSONResponse(result)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "error": "IBKR timeout"}, status_code=504)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Routes — settings
# ---------------------------------------------------------------------------

@app.get("/api/strategies")
async def api_strategies():
    try:
        from strategy import list_strategies
        return JSONResponse(list_strategies())
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/settings")
async def settings_get(request: Request):
    env = _parse_env_file()
    rules = {}
    try:
        rules = json.loads(Path("rules.json").read_text())
    except Exception:
        pass
    from strategy import list_strategies
    return templates.TemplateResponse(request, "settings.html", {
        "env": env,
        "rules": rules,
        "strategies": list_strategies(),
        "error": None,
        "error_fields": [],
        "success": False,
    })


@app.post("/settings")
async def settings_post(
    request: Request,
    ibkr_host: str = Form(default="127.0.0.1"),
    ibkr_port: int = Form(default=7497),
    paper_trading: str = Form(default="false"),
    portfolio_value: float = Form(default=25000),
    max_trade_size: float = Form(default=2500),
    max_trades_per_day: int = Form(default=5),
    max_risk_pct: float = Form(default=1.0),
    d3_min_gap: float = Form(default=3.0),
    i3_rvol: float = Form(default=2.0),
    earliest_entry: str = Form(default="10:05"),
    latest_entry: str = Form(default="15:30"),
    force_close: str = Form(default="15:51"),
    telegram_bot_token: str = Form(default=""),
    telegram_chat_id: str = Form(default=""),
    strategy_name_key: str = Form(default="gap_and_go"),
):
    # active_strategies is a multi-value checkbox field — read from raw form
    form_data = await request.form()
    active_strategies = form_data.getlist("active_strategies") or ["gap_and_go"]
    env = _parse_env_file()
    rules = {}
    try:
        rules = json.loads(Path("rules.json").read_text())
    except Exception:
        pass

    errors = []
    error_fields = []

    paper = paper_trading.lower() in ("on", "true", "1", "yes")

    # Port vs paper validation
    if paper and ibkr_port in {7496, 4001}:
        errors.append("PAPER_TRADING is on but port is a live port (7496/4001).")
        error_fields += ["ibkr_port", "paper_trading"]
    if not paper and ibkr_port in {7497, 11002}:
        errors.append("PAPER_TRADING is off but port looks like paper (7497/11002).")
        error_fields += ["ibkr_port", "paper_trading"]

    if not (0.1 <= max_risk_pct <= 5.0):
        errors.append("MAX_RISK_PER_TRADE_PCT must be between 0.1 and 5.0.")
        error_fields.append("max_risk_pct")
    if not (0.5 <= d3_min_gap <= 20.0):
        errors.append("D3 min gap must be between 0.5 and 20.0.")
        error_fields.append("d3_min_gap")

    def _t(s):
        h, m = s.split(":")
        return int(h) * 60 + int(m)

    if _t(force_close) <= _t(latest_entry):
        errors.append("Force close time must be after latest entry time.")
        error_fields += ["force_close", "latest_entry"]

    from strategy import list_strategies
    if errors:
        return templates.TemplateResponse(request, "settings.html", {
            "env": env,
            "rules": rules,
            "strategies": list_strategies(),
            "error": " ".join(errors),
            "error_fields": error_fields,
            "success": False,
        })

    # Update env dict preserving unknown keys
    env.update({
        "IBKR_HOST": ibkr_host,
        "IBKR_PORT": str(ibkr_port),
        "PAPER_TRADING": str(paper).lower(),
        "PORTFOLIO_VALUE_USD": str(int(portfolio_value)),
        "MAX_TRADE_SIZE_USD": str(int(max_trade_size)),
        "MAX_TRADES_PER_DAY": str(max_trades_per_day),
        "MAX_RISK_PER_TRADE_PCT": str(max_risk_pct),
    })
    if telegram_bot_token:
        env["TELEGRAM_BOT_TOKEN"] = telegram_bot_token
    if telegram_chat_id:
        env["TELEGRAM_CHAT_ID"] = telegram_chat_id

    _write_env_file(env)

    # Update rules.json
    rules.setdefault("daily_filters", {})["D3_min_gap_pct_from_prior_close"] = d3_min_gap
    rules.setdefault("intraday_filters", {})["I3_rvol_min"] = i3_rvol
    rules.setdefault("time_filter", {}).update({
        "earliest_entry_et": earliest_entry,
        "latest_entry_et": latest_entry,
        "force_close_et": force_close,
    })
    rules["strategy_name_key"] = strategy_name_key
    rules["active_strategies"] = active_strategies
    _write_rules_file(rules)

    # Re-read fresh
    env = _parse_env_file()
    rules = json.loads(Path("rules.json").read_text())
    from strategy import list_strategies
    return templates.TemplateResponse(request, "settings.html", {
        "env": env,
        "rules": rules,
        "strategies": list_strategies(),
        "error": None,
        "error_fields": [],
        "success": True,
    })


@app.post("/settings/test-telegram")
async def settings_test_telegram():
    try:
        from src.notify import notify
        notify("Test", "little_greed settings test ✓")
        return JSONResponse({"ok": True, "message": "Test message sent"})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Routes — signals
# ---------------------------------------------------------------------------

@app.get("/signals")
async def signals_get(request: Request):
    today = datetime.now(ET).date().isoformat()
    signals, malformed = _tail_signals(100)
    filtered = _filter_signals(signals, date=today, symbol="", outcome="all")
    today_all = [s for s in signals if s.get("ts", "").startswith(today)]
    return templates.TemplateResponse(request, "signals.html", {
        "signals": filtered,
        "malformed": malformed,
        "today_considered": len(today_all),
        "today_entered": sum(1 for s in today_all if s.get("outcome") == "entered"),
        "today_rejected": sum(1 for s in today_all if s.get("outcome") == "rejected"),
        "default_date": today,
    })


@app.get("/signals/data", response_class=HTMLResponse)
async def signals_data(
    request: Request,
    date: str = "",
    symbol: str = "",
    outcome: str = "all",
):
    signals, malformed = _tail_signals(1000)
    filtered = _filter_signals(signals, date=date, symbol=symbol, outcome=outcome)
    today = datetime.now(ET).date().isoformat()
    today_all = [s for s in signals if s.get("ts", "").startswith(today)]
    return templates.TemplateResponse(request, "_signals_table.html", {
        "signals": filtered,
        "malformed": malformed,
        "today_considered": len(today_all),
        "today_entered": sum(1 for s in today_all if s.get("outcome") == "entered"),
        "today_rejected": sum(1 for s in today_all if s.get("outcome") == "rejected"),
    })


# ---------------------------------------------------------------------------
# Routes — preflight
# ---------------------------------------------------------------------------

@app.get("/preflight")
async def preflight_get(request: Request):
    return templates.TemplateResponse(request, "preflight.html", {"results": None})


@app.post("/preflight/run", response_class=HTMLResponse)
async def preflight_run(request: Request):
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(_executor, _run_all_preflight_checks)
    passed = sum(1 for r in results if r["status"] == "pass")
    return templates.TemplateResponse(request, "_preflight_results.html", {
        "results": results,
        "passed": passed,
        "total": len(results),
    })
