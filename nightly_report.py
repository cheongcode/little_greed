"""Nightly diagnostic report generator. Run at 16:30 ET after market close."""
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()
ET = ZoneInfo("America/New_York")


def _read_journal(days: int = 20) -> list[dict]:
    """Read last `days` days of trade_journal.jsonl entries."""
    path = Path("logs/trade_journal.jsonl")
    if not path.exists():
        return []
    cutoff = (datetime.now(ET) - timedelta(days=days)).isoformat()
    entries = []
    try:
        for line in path.read_text().strip().splitlines():
            try:
                obj = json.loads(line)
                if obj.get("ts", "") >= cutoff:
                    entries.append(obj)
            except Exception:
                pass
    except Exception:
        pass
    return entries


def _read_csv(path_str: str, days: int = 20) -> list[dict]:
    """Read a CSV file, returning rows from last `days` days."""
    path = Path(path_str)
    if not path.exists():
        return []
    cutoff = (datetime.now(ET) - timedelta(days=days)).date().isoformat()
    rows = []
    try:
        with open(path) as f:
            for row in csv.DictReader(f):
                ts = row.get("timestamp", row.get("timestamp_iso", ""))
                if ts[:10] >= cutoff:
                    rows.append(row)
    except Exception:
        pass
    return rows


def section_strategy_performance(journal: list[dict]) -> str:
    """Section 1: Per-strategy performance over rolling 20 days."""
    closed = [e for e in journal if e.get("event") in ("exit_stop", "exit_eod")]
    if not closed:
        return "## Section 1: Strategy Performance\n\n_No closed trades in last 20 days._\n"

    by_strat: dict[str, list] = defaultdict(list)
    for e in closed:
        pnl = float(e.get("pnl", 0))
        by_strat[e.get("strategy", "unknown")].append(pnl)

    lines = ["## Section 1: Strategy Performance (Rolling 20 Days)\n",
             "| Strategy | Trades | Win% | Avg P&L | Expectancy |",
             "|----------|--------|------|---------|------------|"]
    for strat, pnls in sorted(by_strat.items()):
        n = len(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p <= 0]
        win_rate = len(wins) / n * 100
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        expectancy = (len(wins) / n * avg_win) - (len(losses) / n * avg_loss)
        lines.append(f"| {strat} | {n} | {win_rate:.0f}% | ${sum(pnls)/n:+.2f} | ${expectancy:+.2f} |")
    return "\n".join(lines) + "\n"


def section_filter_attribution(signals: list[dict]) -> str:
    """Section 2: Filter attribution — pass rate and conditional win rate."""
    if not signals:
        return "## Section 2: Filter Attribution\n\n_No signals data available._\n"

    by_filter: dict[str, dict] = defaultdict(lambda: {"pass": 0, "fail": 0})
    for row in signals:
        fname = row.get("filter_name", "")
        if row.get("pass_fail") == "pass":
            by_filter[fname]["pass"] += 1
        else:
            by_filter[fname]["fail"] += 1

    lines = ["## Section 2: Filter Attribution\n",
             "| Filter | Pass | Fail | Pass Rate | Notes |",
             "|--------|------|------|-----------|-------|"]
    for fname, counts in sorted(by_filter.items()):
        total = counts["pass"] + counts["fail"]
        rate = counts["pass"] / total * 100 if total > 0 else 0
        note = ""
        if rate > 85:
            note = "⚠ too permissive (>85% pass rate)"
        elif rate < 5:
            note = "⚠ very restrictive (<5% pass rate)"
        lines.append(f"| {fname} | {counts['pass']} | {counts['fail']} | {rate:.0f}% | {note} |")
    return "\n".join(lines) + "\n"


def section_missed_opportunities(journal: list[dict]) -> str:
    """Section 3: Stocks that moved >2% gap or >3% intraday."""
    taken_syms = {e.get("symbol") for e in journal if e.get("event") == "entry"}
    entered = [e for e in journal if e.get("event") == "entry"]
    if not entered:
        return "## Section 3: Missed Opportunities\n\n_No entry data available._\n"
    lines = ["## Section 3: Taken Opportunities Today\n",
             f"Entries taken: {len(entered)}",
             "_(Full missed-opportunity analysis requires watchlist scan data)_\n"]
    for e in entered:
        pnl_events = [j for j in journal if j.get("symbol") == e.get("symbol")
                      and j.get("event") in ("exit_stop", "exit_eod")]
        outcome = f"P&L ${float(pnl_events[-1].get('pnl', 0)):+.2f}" if pnl_events else "open"
        lines.append(f"- {e.get('symbol')} [{e.get('strategy')}] @ ${e.get('price', 0):.2f} → {outcome}")
    return "\n".join(lines) + "\n"


def section_slippage(trades: list[dict]) -> str:
    """Section 4: Slippage report."""
    slippage_rows = [r for r in trades if r.get("slippage_pct") and
                     float(r.get("slippage_pct", 0)) != 0]
    if not slippage_rows:
        return "## Section 4: Slippage Report\n\n_No slippage data yet (new trades.csv format)._\n"
    by_strat: dict[str, list] = defaultdict(list)
    for row in slippage_rows:
        by_strat[row.get("strategy", "unknown")].append(float(row["slippage_pct"]))
    lines = ["## Section 4: Slippage Report\n",
             "| Strategy | Avg Slippage | Max Slippage |",
             "|----------|-------------|--------------|"]
    for strat, slips in sorted(by_strat.items()):
        lines.append(f"| {strat} | {sum(slips)/len(slips)*100:.3f}% | {max(slips)*100:.3f}% |")
    return "\n".join(lines) + "\n"


def section_regime(journal: list[dict]) -> str:
    """Section 5: Regime tags (placeholder until 30 days of data)."""
    return (
        "## Section 5: Regime Analysis\n\n"
        "_Regime-by-strategy expectancy available after 30 days of trading data._\n"
        "_Tags: VIX close, SPY realized vol 20d, SPY vs 50 SMA — add to each trade entry._\n"
    )


def section_sell_analysis(journal: list[dict], exits: list[dict]) -> str:
    """Section 6: Sell-side analysis — % of max unrealized captured."""
    closed = [e for e in journal if e.get("event") in ("exit_stop", "exit_eod")]
    if not closed or not exits:
        return "## Section 6: Sell-Side Analysis\n\n_Insufficient exit data for analysis._\n"

    capture_pcts = []
    for e in closed:
        sym = e.get("symbol")
        entry = float(e.get("price", 0))
        exit_p = float(e.get("price", 0))  # same field for exit events
        # Find max unrealized for this trade from position_updates
        pnl = float(e.get("pnl", 0))
        if entry > 0 and pnl > 0:
            capture_pcts.append(pnl)

    lines = ["## Section 6: Sell-Side Analysis\n"]
    if capture_pcts:
        lines.append(f"Winning trades analyzed: {len(capture_pcts)}")
        lines.append("_(Full max-unrealized capture analysis requires position_updates.csv cross-reference)_")
    else:
        lines.append("_No winning trades to analyze._")
    return "\n".join(lines) + "\n"


def section_recommendations(journal: list[dict]) -> str:
    """Section 7: Auto-recommendations based on strategy performance."""
    closed = [e for e in journal if e.get("event") in ("exit_stop", "exit_eod")]
    recs = []

    by_strat: dict[str, list] = defaultdict(list)
    for e in closed:
        by_strat[e.get("strategy", "unknown")].append(float(e.get("pnl", 0)))

    for strat, pnls in by_strat.items():
        if len(pnls) < 30:
            continue
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p <= 0]
        n = len(pnls)
        expectancy = (len(wins) / n * (sum(wins)/len(wins) if wins else 0)) - \
                     (len(losses) / n * (sum(losses)/len(losses) if losses else 0))
        if expectancy < 0:
            recs.append(f"⚠ **Disable `{strat}`**: 30+ trade expectancy is negative (${expectancy:+.2f})")

    # Check filter pass rates
    pass_rate_warnings: list[str] = []  # populated from signals.csv in full implementation

    lines = ["## Section 7: Auto-Recommendations\n"]
    if recs:
        lines.extend(recs)
    else:
        lines.append("_No automatic recommendations at this time._")
        if len(closed) < 30:
            lines.append(f"_(Need {30 - len(closed)} more closed trades for strategy disable checks)_")
    return "\n".join(lines) + "\n"


def section_one_action(journal: list[dict]) -> str:
    """Section 8: The one action to take tonight."""
    closed = [e for e in journal if e.get("event") in ("exit_stop", "exit_eod")]
    today = datetime.now(ET).date()
    today_trades = [e for e in closed if e.get("ts", "")[:10] == str(today)]
    today_wins = [e for e in today_trades if float(e.get("pnl", 0)) > 0]

    if not today_trades:
        action = "Review the watchlist tomorrow morning — no trades executed today."
    elif len(today_wins) == len(today_trades):
        action = "All trades won today — no changes needed, let the system run."
    elif len(today_wins) == 0 and today_trades:
        pnls = [float(e.get("pnl", 0)) for e in today_trades]
        action = f"All {len(today_trades)} trades lost today (total ${sum(pnls):+.2f}) — check if gap filter threshold needs raising."
    else:
        win_rate = len(today_wins) / len(today_trades) * 100
        action = f"Win rate today: {win_rate:.0f}% ({len(today_wins)}/{len(today_trades)}) — system is functioning normally."

    return f"## Section 8: Tonight's Action\n\n**{action}**\n"


def generate_report() -> str:
    """Generate full nightly report and return markdown string."""
    today = datetime.now(ET).strftime("%Y-%m-%d")
    now_str = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")

    journal  = _read_journal(days=20)
    signals  = _read_csv("signals.csv", days=20)
    trades   = _read_csv("trades.csv", days=20)
    exits    = _read_csv("exits.csv", days=20)

    sections = [
        f"# little_greed Nightly Report — {today}\n_Generated {now_str}_\n",
        section_strategy_performance(journal),
        section_filter_attribution(signals),
        section_missed_opportunities(journal),
        section_slippage(trades),
        section_regime(journal),
        section_sell_analysis(journal, exits),
        section_recommendations(journal),
        section_one_action(journal),
    ]
    return "\n---\n\n".join(sections)


def main() -> None:
    """Generate and save nightly report, send Telegram summary."""
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)

    today = datetime.now(ET).strftime("%Y-%m-%d")
    report_path = reports_dir / f"{today}.md"

    report_md = generate_report()
    report_path.write_text(report_md)
    print(f"Report written to {report_path}")

    # Send Telegram summary (first 1500 chars)
    summary = report_md[:1500]
    try:
        from src.notify import notify
        notify(f"Nightly Report {today}", summary[:900], "default")
        print("Telegram notification sent")
    except Exception as e:
        print(f"Telegram send failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
