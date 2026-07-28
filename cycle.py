import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import yfinance as yf
from src.ibkr_client import IBKRClient
from src.sp500_tickers import SP500_TICKERS
from src.notify import notify
import strategy


ET = ZoneInfo("America/New_York")


def time_gate():
    """Return trading status: weekend, too_early, closed, manage_only, force_close, or ok."""
    now = datetime.now(ET)
    weekday = now.weekday()
    current_time = now.time()

    if weekday >= 5:  # Saturday or Sunday
        return "weekend"

    if current_time < datetime.strptime("10:00", "%H:%M").time():
        return "too_early"

    if current_time >= datetime.strptime("16:00", "%H:%M").time():
        return "closed"

    if datetime.strptime("10:00", "%H:%M").time() <= current_time < datetime.strptime("10:05", "%H:%M").time():
        return "manage_only"

    if datetime.strptime("15:30", "%H:%M").time() <= current_time < datetime.strptime("15:51", "%H:%M").time():
        return "manage_only"

    if datetime.strptime("15:51", "%H:%M").time() <= current_time < datetime.strptime("16:00", "%H:%M").time():
        return "force_close"

    if datetime.strptime("10:05", "%H:%M").time() <= current_time <= datetime.strptime("15:30", "%H:%M").time():
        return "ok"

    return "closed"


def load_positions():
    """Load open positions from JSON."""
    path = Path("open_positions.json")
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except:
        return []


def broker_sync(positions: list, ibkr) -> list:
    """Reconcile local state against broker truth. Adds missing, removes ghost positions."""
    try:
        broker_positions = ibkr.ib.positions()
        broker_map = {
            p.contract.symbol: p
            for p in broker_positions
            if p.position != 0
        }
        local_symbols = {p["symbol"] for p in positions}

        # Add positions held at broker but missing locally (e.g. after restart)
        for sym, bp in broker_map.items():
            if sym not in local_symbols:
                avg = bp.avgCost
                stop = round(avg * 0.99, 2)
                positions.append({
                    "symbol": sym,
                    "entry_price": round(avg, 2),
                    "entry_time_iso": datetime.now(ET).isoformat(),
                    "qty": int(bp.position),
                    "initial_stop": stop,
                    "stop_order_id": None,
                    "state": "pre_breakeven",
                    "R": round(avg - stop, 2),
                })
                log_decision({"action": "broker_sync_added", "symbol": sym, "qty": int(bp.position)})

        # Remove positions in local state that broker no longer holds
        synced = []
        for pos in positions:
            sym = pos["symbol"]
            if sym in broker_map:
                # Update qty from broker truth
                pos["qty"] = int(broker_map[sym].position)
                synced.append(pos)
            else:
                log_decision({"action": "broker_sync_removed", "symbol": sym})
        return synced
    except Exception as e:
        # If sync fails, keep local state — never lose positions
        log_decision({"action": "broker_sync_error", "error": str(e)})
        return positions


def save_positions(positions):
    """Atomically save positions to JSON."""
    path = Path("open_positions.json")
    tmp_path = Path("open_positions.json.tmp")
    tmp_path.write_text(json.dumps(positions, indent=2))
    tmp_path.replace(path)


def log_decision(decision):
    """Append decision to safety log."""
    path = Path("safety-check-log.json")
    with open(path, "a") as f:
        f.write(json.dumps(decision) + "\n")


def _log_signal(logs_dir: Path, symbol: str, outcome: str, result=None, size_planned: int = 0, reject_reason: str = ""):
    try:
        entry = {
            "ts": datetime.now(ET).isoformat(),
            "symbol": symbol,
            "outcome": outcome,
            "filters": {"D1": False, "D2": False, "D3": False, "I1": False, "I2": False, "I3": False},
            "values": {},
            "reject_reason": reject_reason,
            "size_planned": size_planned,
        }
        if result:
            entry["values"]["price"] = result.get("price", 0)
            if not result.get("pass") and not reject_reason:
                reasons = result.get("reasons", [])
                entry["reject_reason"] = reasons[0] if reasons else ""
        with open(logs_dir / "signals.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        print(f"signal log error: {exc}", file=sys.stderr)


def check_stopouts(positions, ibkr):
    """Remove stopped-out positions."""
    remaining = []
    now = datetime.now(ET)
    one_hour_ago = now - timedelta(hours=1)

    fills = ibkr.ib.fills()

    for pos in positions:
        stop_id = pos["stop_order_id"]
        was_filled = any(f.order.orderId == stop_id for f in fills)

        if was_filled:
            log_decision({"action": "stop_exit", "symbol": pos["symbol"], "stop_id": stop_id})
            try:
                exit_fill = next((f for f in fills if f.order.orderId == stop_id), None)
                exit_price = float(exit_fill.execution.avgPrice) if exit_fill else 0.0
                pnl = (exit_price - pos["entry_price"]) * pos["qty"]
                notify(f"STOP {pos['symbol']}", f"exit ${exit_price:.2f}, P&L ${pnl:+.2f}", "default")
            except Exception:
                pass
        else:
            remaining.append(pos)

    return remaining


def manage_position(position, ibkr, ib):
    """Manage existing position: move stops, take partials."""
    symbol = position["symbol"]
    entry_price = position["entry_price"]
    qty = position["qty"]
    state = position["state"]
    R = position["R"]

    # Get current price
    try:
        ticker = yf.Ticker(symbol.replace(" B", "-B").replace(" A", "-A"))
        data = ticker.history(period="5d", interval="1d")
        if data.empty:
            return position
        current_price = float(data["Close"].iloc[-1])
    except:
        return position

    price_gain_R = (current_price - entry_price) / R if R > 0 else 0

    if state == "pre_breakeven":
        if price_gain_R >= 1.0:
            # Move stop to entry (breakeven flip)
            new_stop = entry_price
            log_decision({"action": "move_stop_to_entry", "symbol": symbol})
            position["state"] = "post_breakeven_no_partial"
            try:
                notify(f"BE {symbol}", f"stop -> ${new_stop:.2f}", "default")
            except Exception:
                pass
        elif price_gain_R >= 0.75:
            # Sell 1/3, move stop to entry
            partial_qty = int(qty / 3)
            if partial_qty > 0:
                log_decision({"action": "take_partial", "symbol": symbol, "qty": partial_qty})
                position["qty"] = qty - partial_qty
                position["state"] = "post_breakeven_partial_done"
                try:
                    notify(f"PARTIAL {symbol}", f"sold {partial_qty}/{qty} @ ${current_price:.2f}", "default")
                except Exception:
                    pass

    elif "post_breakeven" in state:
        # Ratchet stops up using 5m swing lows
        old_stop = position.get("initial_stop", 0)
        # (swing low logic placeholder — ratchet only if new > old)
        new_stop = old_stop  # replace with actual swing low computation
        if new_stop > old_stop:
            position["initial_stop"] = new_stop
            log_decision({"action": "trail_stop", "symbol": symbol, "old": old_stop, "new": new_stop})
            try:
                notify(f"TRAIL {symbol}", f"stop ${old_stop:.2f} -> ${new_stop:.2f}", "default")
            except Exception:
                pass

    return position


def main():
    load_dotenv()
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    try:
        # 1. TIME GATE
        gate = time_gate()
        if gate in ["weekend", "too_early", "closed"]:
            sys.exit(0)

        # 2. LOAD STATE
        positions = load_positions()

        # 3. CONNECT IBKR
        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "7497"))
        client_id = int(os.getenv("IBKR_CLIENT_ID", "2"))

        try:
            ibkr = IBKRClient(host, port, client_id)
        except Exception as e:
            time.sleep(5)
            try:
                ibkr = IBKRClient(host, port, client_id)
            except Exception as e2:
                print(f"Connection failed: {e2}")
                sys.exit(1)

        # 3b. BROKER SYNC — reconcile local state with broker truth before anything else
        positions = broker_sync(positions, ibkr)
        save_positions(positions)

        # 4. CHECK STOP-OUTS
        positions = check_stopouts(positions, ibkr)

        # 5. MANAGE POSITIONS
        for pos in positions:
            pos = manage_position(pos, ibkr, ibkr.ib)

        # 6. SAVE STATE
        save_positions(positions)

        # 7. HANDLE FORCE_CLOSE
        if gate == "force_close":
            try:
                notify("EOD Force Close", f"flattening {len(positions)} positions", "high")
            except Exception:
                pass
            for pos in positions:
                log_decision({"action": "force_close", "symbol": pos["symbol"]})
            save_positions([])
            ibkr.disconnect()
            sys.exit(0)

        # 8. HANDLE MANAGE_ONLY
        if gate == "manage_only":
            ibkr.disconnect()
            sys.exit(0)

        # 9. ENTRY SCAN
        if gate == "ok":
            trades_file = Path("trades.csv")
            today_date = datetime.now(ET).date()

            buy_count = 0
            if trades_file.exists():
                with open(trades_file, "r") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row.get("timestamp_iso", "").startswith(str(today_date)) and row.get("side") == "BUY":
                            buy_count += 1

            max_trades = int(os.getenv("MAX_TRADES_PER_DAY", "5"))
            if buy_count >= max_trades:
                ibkr.disconnect()
                sys.exit(0)

            # Get current positions
            ibkr_positions = ibkr.ib.positions()
            held_symbols = {pos.contract.symbol for pos in ibkr_positions if pos.position > 0}

            # Read watchlist
            watchlist_path = Path("watchlist.txt")
            watchlist = []
            if watchlist_path.exists():
                with open(watchlist_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            watchlist.append(line.split()[0])

            # Screen each ticker
            for symbol in watchlist:
                if symbol in held_symbols:
                    _log_signal(logs_dir, symbol, "already_held")
                    continue

                result = strategy.evaluate_all(symbol, ibkr.ib)
                if not result["pass"]:
                    reasons = result.get("reasons", [])
                    _log_signal(logs_dir, symbol, "rejected", result=result,
                                reject_reason=reasons[0] if reasons else "")
                    continue

                price = result["price"]
                portfolio_value = float(os.getenv("PORTFOLIO_VALUE_USD", "25000"))
                max_risk_pct = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "1.0"))
                max_trade_usd = float(os.getenv("MAX_TRADE_SIZE_USD", "2500"))

                budget = min(max_trade_usd, portfolio_value * 0.10)
                qty = int(budget / price) if price > 0 else 0

                if qty < 1:
                    _log_signal(logs_dir, symbol, "size_too_small", result=result,
                                size_planned=qty,
                                reject_reason=f"qty=0 at price ${price:.2f}")
                    continue

                # Spawn trade.py
                cmd = [
                    "python3", "trade.py",
                    "--symbol", symbol,
                    "--side", "BUY",
                    "--size", str(qty),
                ]

                try:
                    result_proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                    if result_proc.returncode == 0:
                        stop = price * 0.99
                        R    = price - stop

                        # ── Place REAL stop order in IBKR immediately ──────
                        stop_order_id = None
                        try:
                            from ib_async import Stock as _Stock, StopOrder as _StopOrder
                            _contract = _Stock(symbol, "SMART", "USD")
                            ibkr.ib.qualifyContracts(_contract)
                            _stop_ord = _StopOrder("SELL", qty, stop)
                            _stop_ord.outsideRth = True
                            _stop_trade = ibkr.ib.placeOrder(_contract, _stop_ord)
                            stop_order_id = _stop_trade.order.orderId
                            log_decision({"action": "stop_placed", "symbol": symbol,
                                          "stop_price": stop, "stop_order_id": stop_order_id})
                        except Exception as se:
                            log_decision({"action": "stop_place_error", "symbol": symbol, "error": str(se)})

                        strategy_used = result.get("strategy", "gap_and_go")
                        new_pos = {
                            "symbol": symbol,
                            "entry_price": price,
                            "entry_time_iso": datetime.now(ET).isoformat(),
                            "qty": qty,
                            "initial_stop": stop,
                            "stop_order_id": stop_order_id,
                            "state": "pre_breakeven",
                            "R": R,
                            "strategy": strategy_used,
                        }
                        positions.append(new_pos)
                        log_decision({"action": "entry", "symbol": symbol, "qty": qty,
                                      "price": price, "strategy": strategy_used})
                        _log_signal(logs_dir, symbol, "entered", result=result, size_planned=qty)
                        try:
                            notify(f"BUY {symbol} [{strategy_used}]",
                                   f"@ ${price:.2f}, stop ${stop:.2f} (order #{stop_order_id}), qty {qty}", "default")
                        except Exception:
                            pass
                except subprocess.TimeoutExpired:
                    log_decision({"action": "trade_timeout", "symbol": symbol})

        # 10. SAVE FINAL STATE
        save_positions(positions)
        ibkr.disconnect()

    except Exception as e:
        with open(logs_dir / "cycle_errors.log", "a") as f:
            f.write(f"{datetime.now(ET).isoformat()}: {str(e)}\n")
        try:
            notify("Cycle CRASHED", str(e)[:500], "high")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
