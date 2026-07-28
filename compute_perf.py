import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from src.notify import notify

load_dotenv()

ET = ZoneInfo("America/New_York")


def main():
    today = datetime.now(ET).date()
    trades_file = Path("trades.csv")

    buys = defaultdict(list)
    sells = defaultdict(list)

    if trades_file.exists():
        with open(trades_file, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = row.get("timestamp_iso", "")
                if not ts.startswith(str(today)):
                    continue
                symbol = row.get("symbol", "")
                side = row.get("side", "").upper()
                fill_price = float(row.get("fill_price", 0) or 0)
                size = int(row.get("size", 0) or 0)
                if side == "BUY":
                    buys[symbol].append({"price": fill_price, "qty": size})
                elif side == "SELL":
                    sells[symbol].append({"price": fill_price, "qty": size})

    # FIFO pairing
    closed_trades = []
    for symbol in buys:
        buy_queue = list(buys[symbol])
        sell_queue = list(sells.get(symbol, []))
        bi = si = 0
        while bi < len(buy_queue) and si < len(sell_queue):
            b = buy_queue[bi]
            s = sell_queue[si]
            qty = min(b["qty"], s["qty"])
            pnl = (s["price"] - b["price"]) * qty
            pnl_pct = (s["price"] - b["price"]) / b["price"] * 100 if b["price"] else 0
            closed_trades.append({"symbol": symbol, "pnl": pnl, "pnl_pct": pnl_pct, "qty": qty})
            b["qty"] -= qty
            s["qty"] -= qty
            if b["qty"] == 0:
                bi += 1
            if s["qty"] == 0:
                si += 1

    if not closed_trades:
        result = {
            "date": str(today),
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0,
            "gross_pnl_usd": 0,
            "largest_winner": None,
            "largest_loser": None,
            "avg_winner": 0,
            "avg_loser": 0,
            "profit_factor": "n/a",
        }
        print(json.dumps(result))
        notify(f"Daily Summary {today}", "No closed trades today.")
        return

    wins = [t for t in closed_trades if t["pnl"] > 0]
    losses = [t for t in closed_trades if t["pnl"] <= 0]
    gross_pnl = sum(t["pnl"] for t in closed_trades)
    win_sum = sum(t["pnl"] for t in wins)
    loss_sum = sum(t["pnl"] for t in losses)

    if losses:
        pf = f"{win_sum / abs(loss_sum):.2f}" if loss_sum != 0 else "inf"
    else:
        pf = "inf"

    largest_winner = max(wins, key=lambda t: t["pnl"]) if wins else None
    largest_loser = min(losses, key=lambda t: t["pnl"]) if losses else None
    avg_winner = win_sum / len(wins) if wins else 0
    avg_loser = loss_sum / len(losses) if losses else 0
    win_rate = len(wins) / len(closed_trades) * 100

    result = {
        "date": str(today),
        "total_trades": len(closed_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 1),
        "gross_pnl_usd": round(gross_pnl, 2),
        "largest_winner": {"symbol": largest_winner["symbol"], "pnl": round(largest_winner["pnl"], 2)} if largest_winner else None,
        "largest_loser": {"symbol": largest_loser["symbol"], "pnl": round(largest_loser["pnl"], 2)} if largest_loser else None,
        "avg_winner": round(avg_winner, 2),
        "avg_loser": round(avg_loser, 2),
        "profit_factor": pf,
    }

    print(json.dumps(result))

    best_sym = largest_winner["symbol"] if largest_winner else "-"
    best_amt = largest_winner["pnl"] if largest_winner else 0
    worst_sym = largest_loser["symbol"] if largest_loser else "-"
    worst_amt = largest_loser["pnl"] if largest_loser else 0

    notify(
        f"Daily Summary {today}",
        f"Trades: {len(closed_trades)} ({len(wins)}W / {len(losses)}L, {win_rate:.0f}%)\n"
        f"P&L: ${gross_pnl:+.2f}\n"
        f"Best: {best_sym} ${best_amt:+.2f}\n"
        f"Worst: {worst_sym} ${worst_amt:+.2f}\n"
        f"PF: {pf}",
    )


if __name__ == "__main__":
    main()
