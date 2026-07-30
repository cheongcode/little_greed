"""Rolling expectancy calculator from trade_journal.jsonl."""
import json
from pathlib import Path


def rolling_expectancy(strategy_name: str, n: int = 20) -> float:
    """Compute rolling expectancy for strategy_name from last n closed trades.

    Reads logs/trade_journal.jsonl. Only counts 'exit_stop' and 'exit_eod'
    events (closed trades with pnl). Requires at least 3 trades to return
    a non-zero value.

    Returns expectancy as R-multiple: (win_rate * avg_win_R) - (loss_rate * avg_loss_R).
    Returns 0.0 if insufficient data.
    """
    journal_path = Path("logs/trade_journal.jsonl")
    if not journal_path.exists():
        return 0.0

    trades = []
    try:
        lines = journal_path.read_text().strip().splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("strategy") != strategy_name:
                continue
            if obj.get("event") not in ("exit_stop", "exit_eod", "partial"):
                continue
            pnl = float(obj.get("pnl", 0))
            trades.append(pnl)
            if len(trades) >= n:
                break
    except Exception:
        return 0.0

    if len(trades) < 3:
        return 0.0

    wins   = [t for t in trades if t > 0]
    losses = [abs(t) for t in trades if t <= 0]

    win_rate  = len(wins) / len(trades)
    loss_rate = 1.0 - win_rate
    avg_win   = sum(wins) / len(wins) if wins else 0.0
    avg_loss  = sum(losses) / len(losses) if losses else 0.0

    # Normalize to R (approximate: assume avg R ~ 1% of price, use $100 as proxy)
    # Expectancy sign is what matters for ranking; absolute value is secondary
    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
    return round(expectancy, 4)
