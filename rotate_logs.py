import csv
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

ET = ZoneInfo("America/New_York")
TRADES_RETENTION_DAYS = 90
SAFETY_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


def main():
    today = datetime.now(ET).date()
    today_str = today.strftime("%Y-%m-%d")
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    archive_dir = logs_dir / "archive" / today_str
    rotated = 0

    # Rotate .log and .jsonl files whose mtime date is before today
    for ext in ("*.log", "*.jsonl"):
        for log_file in logs_dir.glob(ext):
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime, tz=ET).date()
            if mtime < today:
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = archive_dir / log_file.name
                os.replace(log_file, dest)
                rotated += 1

    # Rotate trades.csv: keep last 90 days, archive older rows
    trades_file = Path("trades.csv")
    if trades_file.exists():
        cutoff = today - timedelta(days=TRADES_RETENTION_DAYS)
        keep_rows = []
        archive_rows = []
        fieldnames = None

        with open(trades_file, "r") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            for row in reader:
                ts = row.get("timestamp_iso", "")
                try:
                    row_date = datetime.fromisoformat(ts[:10]).date()
                except ValueError:
                    keep_rows.append(row)
                    continue
                if row_date >= cutoff:
                    keep_rows.append(row)
                else:
                    archive_rows.append(row)

        if archive_rows:
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_trades = logs_dir / "archive" / f"trades_{today_str.replace('-', '')}.csv"
            with open(archive_trades, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(archive_rows)

            tmp = Path("trades.csv.tmp")
            with open(tmp, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(keep_rows)
            os.replace(tmp, trades_file)
            rotated += 1

    # Rotate safety-check-log.json if > 5 MB
    safety_log = Path("safety-check-log.json")
    if safety_log.exists() and safety_log.stat().st_size > SAFETY_LOG_MAX_BYTES:
        archive_dir.mkdir(parents=True, exist_ok=True)
        dest = archive_dir / f"{today_str}-safety-check-log.json"
        os.replace(safety_log, dest)
        rotated += 1

    print(f"Rotated {rotated} files to logs/archive/")


if __name__ == "__main__":
    main()
