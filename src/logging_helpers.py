"""Shared CSV logging helpers for signals, positions, exits, and rejected signals."""
import csv
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

_SIGNALS_COLS = [
    "timestamp", "symbol", "strategy", "filter_name",
    "filter_value", "threshold", "pass_fail"
]
_POSITION_COLS = [
    "timestamp", "symbol", "current_price", "unrealized_R",
    "stop_level", "action_taken"
]
_REJECTED_COLS = [
    "timestamp", "symbol", "winning_strategy", "rejected_strategy",
    "winning_expectancy", "rejected_expectancy", "reason"
]
_EXIT_COLS = [
    "timestamp", "symbol", "strategy", "exit_trigger", "qty_sold",
    "fill_price", "entry_price", "r_multiple", "pnl", "remaining_qty"
]


def _ensure_header(path: Path, cols: list[str]) -> None:
    """Write CSV header if file is new or empty."""
    if not path.exists() or path.stat().st_size == 0:
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(cols)


def log_signal_csv(ts: str, symbol: str, strategy: str,
                   filters: dict, thresholds: dict, bar_values: dict) -> None:
    """Append one row per filter to signals.csv."""
    path = Path("signals.csv")
    _ensure_header(path, _SIGNALS_COLS)
    try:
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            for filter_name, passed in filters.items():
                threshold = thresholds.get(filter_name, "")
                value = bar_values.get(filter_name, "")
                w.writerow([ts, symbol, strategy, filter_name,
                             value, threshold, "pass" if passed else "fail"])
    except Exception as e:
        print(f"log_signal_csv error: {e}", file=sys.stderr)


def log_position_update_csv(ts: str, symbol: str, current_price: float,
                             unrealized_R: float, stop_level: float,
                             action_taken: str) -> None:
    """Append one row to position_updates.csv."""
    path = Path("position_updates.csv")
    _ensure_header(path, _POSITION_COLS)
    try:
        with open(path, "a", newline="") as f:
            csv.writer(f).writerow([ts, symbol, round(current_price, 4),
                                     round(unrealized_R, 4), round(stop_level, 4),
                                     action_taken])
    except Exception as e:
        print(f"log_position_update_csv error: {e}", file=sys.stderr)


def log_rejected_signal_csv(ts: str, symbol: str, winning_strategy: str,
                             rejected_strategy: str, winning_expectancy: float,
                             rejected_expectancy: float, reason: str) -> None:
    """Append one row to rejected_signals.csv."""
    path = Path("rejected_signals.csv")
    _ensure_header(path, _REJECTED_COLS)
    try:
        with open(path, "a", newline="") as f:
            csv.writer(f).writerow([ts, symbol, winning_strategy, rejected_strategy,
                                     round(winning_expectancy, 4),
                                     round(rejected_expectancy, 4), reason])
    except Exception as e:
        print(f"log_rejected_signal_csv error: {e}", file=sys.stderr)


def log_exit_csv(ts: str, symbol: str, strategy: str, exit_trigger: str,
                 qty_sold: int, fill_price: float, entry_price: float,
                 r_multiple: float, pnl: float, remaining_qty: int) -> None:
    """Append one row to exits.csv."""
    path = Path("exits.csv")
    _ensure_header(path, _EXIT_COLS)
    try:
        with open(path, "a", newline="") as f:
            csv.writer(f).writerow([ts, symbol, strategy, exit_trigger,
                                     qty_sold, round(fill_price, 4),
                                     round(entry_price, 4), round(r_multiple, 4),
                                     round(pnl, 2), remaining_qty])
    except Exception as e:
        print(f"log_exit_csv error: {e}", file=sys.stderr)
