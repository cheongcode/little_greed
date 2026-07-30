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
import numpy as np
from src.ibkr_client import IBKRClient
from src.sp500_tickers import SP500_TICKERS
from src.notify import notify
from src.risk import compute_atr, compute_stop, compute_shares, compute_chandelier_stop
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
                    "state": "initial",
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
    """Append decision to safety log. Always stamp ts if missing."""
    if "ts" not in decision:
        decision["ts"] = datetime.now(ET).isoformat()
    path = Path("safety-check-log.json")
    with open(path, "a") as f:
        f.write(json.dumps(decision) + "\n")


def _log_trade_csv(symbol: str, side: str, qty: int, fill_price: float,
                   order_id, status: str,
                   portfolio_value: float = 0.0, risk_dollars: float = 0.0,
                   entry_price: float = 0.0, stop_price: float = 0.0,
                   R_per_share: float = 0.0, computed_shares: int = 0,
                   slippage_pct: float = 0.0):
    """Append a fill to trades.csv with full sizing data."""
    trades_file = Path("trades.csv")
    # Write header if file is new
    if not trades_file.exists():
        with open(trades_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp_iso", "symbol", "side", "size", "fill_price",
                "order_id", "status", "portfolio_value", "risk_dollars",
                "entry_price", "stop_price", "R_per_share",
                "computed_shares", "actual_fill_shares", "slippage_pct"
            ])
    now = datetime.now(ET).isoformat()
    try:
        with open(trades_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                now, symbol, side, qty, round(fill_price, 4),
                order_id, status,
                round(portfolio_value, 2), round(risk_dollars, 2),
                round(entry_price, 4), round(stop_price, 4),
                round(R_per_share, 4), computed_shares, qty,
                round(slippage_pct, 6),
            ])
    except Exception as e:
        print(f"trades.csv write error: {e}", file=sys.stderr)


def _log_trade_journal(logs_dir: Path, event: str, symbol: str, strategy: str,
                       side: str, qty: int, price: float, stop: float = 0,
                       pnl: float = 0, order_id=None):
    """Append to trade_journal.jsonl — full history with strategy tags."""
    entry = {
        "ts":       datetime.now(ET).isoformat(),
        "event":    event,          # "entry" | "exit_stop" | "exit_eod" | "partial"
        "symbol":   symbol,
        "strategy": strategy,
        "side":     side,
        "qty":      qty,
        "price":    round(price, 2),
        "stop":     round(stop, 2),
        "pnl":      round(pnl, 2),
        "order_id": order_id,
    }
    try:
        with open(logs_dir / "trade_journal.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"journal write error: {e}", file=sys.stderr)


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


def _log_exit_csv(symbol: str, strategy: str, exit_trigger: str,
                  qty_sold: int, fill_price: float, entry_price: float,
                  r_multiple: float, pnl: float, remaining_qty: int):
    """Log exit event to exits.csv."""
    try:
        from src.logging_helpers import log_exit_csv
        log_exit_csv(
            ts=datetime.now(ET).isoformat(),
            symbol=symbol, strategy=strategy,
            exit_trigger=exit_trigger, qty_sold=qty_sold,
            fill_price=fill_price, entry_price=entry_price,
            r_multiple=r_multiple, pnl=pnl, remaining_qty=remaining_qty,
        )
    except Exception:
        pass


def _get_5min_bars(symbol: str, ib, n_bars: int = 100) -> "pd.DataFrame":
    """Fetch 5-min bars via IBKR data shim for ATR computation."""
    try:
        from src.data_shim import get_intraday_bars
        return get_intraday_bars(symbol, n_bars, ib)
    except Exception:
        import pandas as pd
        return pd.DataFrame()


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
                logs_dir = Path("logs")
                _log_trade_journal(logs_dir, "exit_stop", pos["symbol"],
                                   pos.get("strategy", "unknown"), "SELL",
                                   pos["qty"], exit_price, pnl=pnl, order_id=stop_id)
                notify(f"STOP {pos['symbol']}", f"exit ${exit_price:.2f}, P&L ${pnl:+.2f}", "default")
            except Exception:
                pass
        else:
            remaining.append(pos)

    return remaining


def _cancel_stop_order(ib, stop_order_id):
    """Cancel an open stop order by ID. Swallows errors."""
    if not stop_order_id:
        return
    try:
        for trade in ib.openTrades():
            if trade.order.orderId == stop_order_id:
                ib.cancelOrder(trade.order)
                time.sleep(0.5)
                return
    except Exception:
        pass


def _place_stop(ib, symbol: str, qty: int, stop_price: float):
    """Cancel nothing — just place a new stop order. Returns order ID or None."""
    try:
        from ib_async import Stock as _Stock, StopOrder as _StopOrder
        contract = _Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(contract)
        stop_ord = _StopOrder("SELL", qty, round(stop_price, 2))
        stop_ord.outsideRth = True
        trade = ib.placeOrder(contract, stop_ord)
        return trade.order.orderId
    except Exception as e:
        log_decision({"action": "stop_place_error", "symbol": symbol, "error": str(e)})
        return None


def _place_market_sell(ib, symbol: str, qty: int) -> tuple:
    """Place a market SELL and wait up to 3s for fill. Returns (fill_price, order_id, status)."""
    try:
        from ib_async import Stock as _Stock, MarketOrder as _MktOrder
        contract = _Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(contract)
        sell_ord = _MktOrder("SELL", qty)
        sell_ord.outsideRth = True
        trade = ib.placeOrder(contract, sell_ord)
        time.sleep(2)
        fill = trade.orderStatus.avgFillPrice or 0.0
        return fill, trade.order.orderId, trade.orderStatus.status
    except Exception as e:
        log_decision({"action": "market_sell_error", "symbol": symbol, "error": str(e)})
        return 0.0, None, "Error"


def manage_position(position: dict, ibkr) -> dict:
    """Manage existing position with layered R-based exits and ATR trailing."""
    symbol     = position["symbol"]
    entry_price = position["entry_price"]
    qty        = position["qty"]
    state      = position["state"]
    R          = position.get("R", 0)
    ib         = ibkr.ib
    logs_dir   = Path("logs")

    # ── 1. Get current price via IBKR ──────────────────────────────
    current_price = 0.0
    try:
        from ib_async import Stock as _Stock
        contract = _Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(contract)
        ticker = ib.reqMktData(contract)
        time.sleep(1)
        current_price = float(ticker.last or ticker.bid or ticker.close or 0)
    except Exception:
        pass
    if current_price <= 0:
        return position

    # ── 2. Get 5-min bars for ATR ──────────────────────────────────
    bars_5m = _get_5min_bars(symbol, ib, n_bars=100)

    # Compute live ATR and R for this cycle
    rules = {}
    try:
        rules = json.loads(Path("rules.json").read_text())
    except Exception:
        pass

    atr = compute_atr(bars_5m, 14) if not bars_5m.empty else 0.0
    if R <= 0 and atr > 0:
        # Reconstruct R from ATR if not set (broker_sync add case)
        stop, _ = compute_stop(entry_price, atr, rules)
        R = entry_price - stop
        position["R"] = round(R, 4)

    price_gain_R = (current_price - entry_price) / R if R > 0 else 0

    # ── 3. Log position update ─────────────────────────────────────
    try:
        from src.logging_helpers import log_position_update_csv
        log_position_update_csv(
            ts=datetime.now(ET).isoformat(),
            symbol=symbol,
            current_price=current_price,
            unrealized_R=round(price_gain_R, 3),
            stop_level=position.get("initial_stop", 0),
            action_taken="check",
        )
    except Exception:
        pass

    # ── 4. Additional exit triggers (checked regardless of state) ──
    now = datetime.now(ET)

    # 4a. Gap reversal: bar drops -1.5 × ATR from prior close → sell 100%
    if atr > 0 and not bars_5m.empty and len(bars_5m) >= 2:
        prev_close = float(bars_5m["Close"].iloc[-2])
        if current_price < prev_close - 1.5 * atr:
            fill_price, oid, status = _place_market_sell(ib, symbol, qty)
            if fill_price <= 0:
                fill_price = current_price
            pnl = (fill_price - entry_price) * qty
            r_mult = (fill_price - entry_price) / R if R > 0 else 0
            _log_trade_csv(symbol, "SELL", qty, fill_price, oid, status,
                           entry_price=entry_price)
            _log_trade_journal(logs_dir, "exit_stop", symbol,
                               position.get("strategy", "unknown"),
                               "SELL", qty, fill_price, pnl=pnl, order_id=oid)
            _log_exit_csv(symbol, position.get("strategy", "unknown"),
                          "gap_reversal_1.5atr", qty, fill_price,
                          entry_price, r_mult, pnl, 0)
            _cancel_stop_order(ib, position.get("stop_order_id"))
            try:
                notify(f"EXIT {symbol}", f"gap reversal −1.5×ATR @ ${fill_price:.2f}, P&L ${pnl:+.2f}", "high")
            except Exception:
                pass
            return None  # signal to caller: position closed

    # 4b. VWAP reversal: after 30+ min above VWAP, close below VWAP → sell 50%
    if not bars_5m.empty and qty > 1:
        try:
            tp = (bars_5m["High"] + bars_5m["Low"] + bars_5m["Close"]) / 3
            vwap = float((tp * bars_5m["Volume"]).cumsum().iloc[-1] /
                         bars_5m["Volume"].cumsum().iloc[-1])
            # Check entry time to see if we've been above VWAP for 30+ min
            entry_dt = datetime.fromisoformat(position.get("entry_time_iso", now.isoformat()))
            mins_held = (now - entry_dt).total_seconds() / 60
            prev_close_bar = float(bars_5m["Close"].iloc[-2]) if len(bars_5m) >= 2 else current_price
            if (mins_held >= 30 and current_price < vwap and
                    prev_close_bar > vwap and
                    state not in ("vwap_reversal_triggered",)):
                partial_qty = max(1, qty // 2)
                fill_price, oid, status = _place_market_sell(ib, symbol, partial_qty)
                if fill_price <= 0:
                    fill_price = current_price
                pnl = (fill_price - entry_price) * partial_qty
                r_mult = (fill_price - entry_price) / R if R > 0 else 0
                _log_trade_csv(symbol, "SELL", partial_qty, fill_price, oid, status)
                _log_exit_csv(symbol, position.get("strategy", "unknown"),
                              "vwap_reversal_50pct", partial_qty, fill_price,
                              entry_price, r_mult, pnl, qty - partial_qty)
                remaining = qty - partial_qty
                if remaining > 0:
                    _cancel_stop_order(ib, position.get("stop_order_id"))
                    new_stop = position.get("initial_stop", entry_price * 0.99)
                    new_stop_id = _place_stop(ib, symbol, remaining, new_stop)
                    position["qty"] = remaining
                    position["stop_order_id"] = new_stop_id
                    position["state"] = "vwap_reversal_triggered"
                    log_decision({"action": "vwap_reversal_partial", "symbol": symbol,
                                  "qty_sold": partial_qty, "remaining": remaining})
                    try:
                        notify(f"VWAP REVERSAL {symbol}",
                               f"sold {partial_qty} @ ${fill_price:.2f}, P&L ${pnl:+.2f}", "default")
                    except Exception:
                        pass
                else:
                    return None  # fully closed
        except Exception:
            pass

    # 4c. Time decay: at 14:00, if unrealized < 0.25R → reduce to 50%
    if (now.hour == 14 and now.minute < 5 and
            price_gain_R < 0.25 and qty > 1 and
            "time_decay_applied" not in state):
        partial_qty = max(1, qty // 2)
        fill_price, oid, status = _place_market_sell(ib, symbol, partial_qty)
        if fill_price <= 0:
            fill_price = current_price
        pnl = (fill_price - entry_price) * partial_qty
        r_mult = (fill_price - entry_price) / R if R > 0 else 0
        _log_trade_csv(symbol, "SELL", partial_qty, fill_price, oid, status)
        _log_exit_csv(symbol, position.get("strategy", "unknown"),
                      "time_decay_14h", partial_qty, fill_price,
                      entry_price, r_mult, pnl, qty - partial_qty)
        remaining = qty - partial_qty
        position["qty"] = remaining
        position["state"] = state + "_time_decay_applied"
        log_decision({"action": "time_decay_reduce", "symbol": symbol,
                      "qty_sold": partial_qty, "unrealized_R": round(price_gain_R, 3)})
        qty = remaining  # update local variable

    # ── 5. Main state machine ──────────────────────────────────────

    if state == "initial" or state == "pre_breakeven":
        # Scale 1: At +0.75R, sell 25%, move stop to entry - 0.25R
        if price_gain_R >= 0.75:
            partial_qty = max(1, int(qty * 0.25))
            if partial_qty >= qty:
                partial_qty = qty - 1
            if partial_qty > 0:
                fill_price, oid, status = _place_market_sell(ib, symbol, partial_qty)
                if fill_price <= 0:
                    fill_price = current_price
                pnl = (fill_price - entry_price) * partial_qty
                r_mult = (fill_price - entry_price) / R if R > 0 else 0
                new_stop = round(entry_price - 0.25 * R, 4)
                remaining = qty - partial_qty
                _cancel_stop_order(ib, position.get("stop_order_id"))
                new_stop_id = _place_stop(ib, symbol, remaining, new_stop)
                _log_trade_csv(symbol, "SELL", partial_qty, fill_price, oid, status)
                _log_trade_journal(logs_dir, "partial", symbol,
                                   position.get("strategy", "unknown"),
                                   "SELL", partial_qty, fill_price,
                                   stop=new_stop, pnl=pnl, order_id=oid)
                _log_exit_csv(symbol, position.get("strategy", "unknown"),
                              "scale1_0.75R", partial_qty, fill_price,
                              entry_price, r_mult, pnl, remaining)
                position.update({
                    "qty": remaining, "initial_stop": new_stop,
                    "stop_order_id": new_stop_id, "state": "scale1",
                })
                log_decision({"action": "scale1_partial", "symbol": symbol,
                              "qty": partial_qty, "fill": fill_price,
                              "new_stop": new_stop, "atr": round(atr, 4)})
                try:
                    notify(f"SCALE1 {symbol}",
                           f"sold {partial_qty} @ ${fill_price:.2f}, "
                           f"stop→${new_stop:.2f}, P&L ${pnl:+.2f}", "default")
                except Exception:
                    pass

    elif state == "scale1":
        # Scale 2: At +1.5R, sell 25% more, stop to breakeven
        if price_gain_R >= 1.5:
            partial_qty = max(1, int(qty * 0.33))  # ~25% of original
            if partial_qty >= qty:
                partial_qty = qty - 1
            if partial_qty > 0:
                fill_price, oid, status = _place_market_sell(ib, symbol, partial_qty)
                if fill_price <= 0:
                    fill_price = current_price
                pnl = (fill_price - entry_price) * partial_qty
                r_mult = (fill_price - entry_price) / R if R > 0 else 0
                new_stop = entry_price  # breakeven
                remaining = qty - partial_qty
                _cancel_stop_order(ib, position.get("stop_order_id"))
                new_stop_id = _place_stop(ib, symbol, remaining, new_stop)
                _log_trade_csv(symbol, "SELL", partial_qty, fill_price, oid, status)
                _log_trade_journal(logs_dir, "partial", symbol,
                                   position.get("strategy", "unknown"),
                                   "SELL", partial_qty, fill_price,
                                   stop=new_stop, pnl=pnl, order_id=oid)
                _log_exit_csv(symbol, position.get("strategy", "unknown"),
                              "scale2_1.5R", partial_qty, fill_price,
                              entry_price, r_mult, pnl, remaining)
                position.update({
                    "qty": remaining, "initial_stop": new_stop,
                    "stop_order_id": new_stop_id, "state": "scale2",
                })
                log_decision({"action": "scale2_partial", "symbol": symbol,
                              "qty": partial_qty, "stop": "breakeven"})
                try:
                    notify(f"SCALE2 {symbol}",
                           f"sold {partial_qty} @ ${fill_price:.2f}, "
                           f"stop→breakeven, P&L ${pnl:+.2f}", "default")
                except Exception:
                    pass

    elif state == "scale2":
        # Scale 3: At +2.5R, sell 25% more, start Chandelier trail
        if price_gain_R >= 2.5:
            partial_qty = max(1, int(qty * 0.5))  # half of remaining
            if partial_qty >= qty:
                partial_qty = qty - 1
            if partial_qty > 0:
                fill_price, oid, status = _place_market_sell(ib, symbol, partial_qty)
                if fill_price <= 0:
                    fill_price = current_price
                pnl = (fill_price - entry_price) * partial_qty
                r_mult = (fill_price - entry_price) / R if R > 0 else 0
                remaining = qty - partial_qty
                # Compute chandelier stop
                entry_dt = datetime.fromisoformat(position.get("entry_time_iso",
                                                                datetime.now(ET).isoformat()))
                chd_mult = float(rules.get("chandelier_atr_multiplier", 3.0))
                chandelier_stop = compute_chandelier_stop(bars_5m, entry_dt, atr, chd_mult)
                new_stop = max(chandelier_stop, entry_price)  # never below breakeven
                _cancel_stop_order(ib, position.get("stop_order_id"))
                new_stop_id = _place_stop(ib, symbol, remaining, new_stop)
                _log_trade_csv(symbol, "SELL", partial_qty, fill_price, oid, status)
                _log_trade_journal(logs_dir, "partial", symbol,
                                   position.get("strategy", "unknown"),
                                   "SELL", partial_qty, fill_price,
                                   stop=new_stop, pnl=pnl, order_id=oid)
                _log_exit_csv(symbol, position.get("strategy", "unknown"),
                              "scale3_2.5R", partial_qty, fill_price,
                              entry_price, r_mult, pnl, remaining)
                position.update({
                    "qty": remaining, "initial_stop": new_stop,
                    "stop_order_id": new_stop_id, "state": "runner",
                    "entry_time_iso": position.get("entry_time_iso",
                                                    datetime.now(ET).isoformat()),
                })
                log_decision({"action": "scale3_partial", "symbol": symbol,
                              "qty": partial_qty, "chandelier_stop": new_stop,
                              "atr": round(atr, 4)})
                try:
                    notify(f"SCALE3 {symbol}",
                           f"sold {partial_qty} @ ${fill_price:.2f}, "
                           f"chandelier stop ${new_stop:.2f}", "default")
                except Exception:
                    pass

    elif state == "runner":
        # Trail with Chandelier — ratchet up only
        old_stop = position.get("initial_stop", 0)
        entry_dt = datetime.fromisoformat(position.get("entry_time_iso",
                                                        datetime.now(ET).isoformat()))
        chd_mult = float(rules.get("chandelier_atr_multiplier", 3.0))
        chandelier_stop = compute_chandelier_stop(bars_5m, entry_dt, atr, chd_mult)
        new_stop = max(chandelier_stop, entry_price)  # never below breakeven
        if new_stop > old_stop + 0.01:  # only move if meaningfully higher
            _cancel_stop_order(ib, position.get("stop_order_id"))
            new_stop_id = _place_stop(ib, symbol, qty, new_stop)
            position["initial_stop"] = new_stop
            position["stop_order_id"] = new_stop_id
            log_decision({"action": "chandelier_trail", "symbol": symbol,
                          "old": old_stop, "new": new_stop, "atr": round(atr, 4)})
            try:
                notify(f"TRAIL {symbol}",
                       f"chandelier ${old_stop:.2f}→${new_stop:.2f}", "default")
            except Exception:
                pass

    return position


def main():
    load_dotenv()
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    try:
        # 0. HEARTBEAT — write timestamp so dashboard shows "Bot running"
        log_decision({"action": "cycle_heartbeat", "ts": datetime.now(ET).isoformat(),
                      "gate": time_gate()})

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
        updated_positions = []
        for pos in positions:
            result = manage_position(pos, ibkr)
            if result is not None:
                updated_positions.append(result)
        positions = updated_positions

        # 6. SAVE STATE
        save_positions(positions)

        # 7. HANDLE FORCE_CLOSE
        if gate == "force_close":
            try:
                notify("EOD Force Close", f"flattening {len(positions)} positions", "high")
            except Exception:
                pass
            from ib_async import Stock as _Stock, MarketOrder as _MktOrder
            for pos in positions:
                log_decision({"action": "force_close", "symbol": pos["symbol"]})
                try:
                    # Cancel existing stop so it doesn't race with our market order
                    _cancel_stop_order(ibkr.ib, pos.get("stop_order_id"))
                    fill_price, oid, status = _place_market_sell(ibkr.ib, pos["symbol"], pos["qty"])
                    if fill_price <= 0:
                        fill_price = pos["entry_price"]
                    pnl = (fill_price - pos["entry_price"]) * pos["qty"]
                    _log_trade_csv(pos["symbol"], "SELL", pos["qty"], fill_price, oid, status)
                    _log_trade_journal(logs_dir, "exit_eod", pos["symbol"],
                                       pos.get("strategy", "unknown"), "SELL",
                                       pos["qty"], fill_price, pnl=pnl, order_id=oid)
                    try:
                        notify(f"EOD SELL {pos['symbol']}",
                               f"@ ${fill_price:.2f}, P&L ${pnl:+.2f}", "default")
                    except Exception:
                        pass
                except Exception as e:
                    log_decision({"action": "force_close_error",
                                  "symbol": pos["symbol"], "error": str(e)})
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

            # Load rules config for ATR sizing
            rules_config = {}
            try:
                rules_config = json.loads(Path("rules.json").read_text())
            except Exception:
                pass

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

                # Get 5-min bars for ATR
                bars_5m_entry = _get_5min_bars(symbol, ibkr.ib, n_bars=100)
                atr_entry = compute_atr(bars_5m_entry, 14) if not bars_5m_entry.empty else 0.0

                if atr_entry <= 0:
                    # Fallback: use 1% of price as ATR estimate
                    atr_entry = price * 0.01

                stop_price, stop_gap = compute_stop(price, atr_entry, rules_config)

                risk_pct = float(os.getenv('MAX_RISK_PER_TRADE_PCT', '1.0')) / 100
                risk_dollars = portfolio_value * risk_pct

                try:
                    qty, R_per_share = compute_shares(portfolio_value, risk_dollars, price, stop_price)
                except ValueError as ve:
                    _log_signal(logs_dir, symbol, "rejected", reject_reason=f"sizing error: {ve}")
                    continue

                if qty < 1:
                    _log_signal(logs_dir, symbol, "size_too_small", result=result,
                                size_planned=qty,
                                reject_reason=f"qty=0 at price ${price:.2f}, risk ${risk_dollars:.0f}, R ${R_per_share:.4f}")
                    continue

                # Sizing assertion: computed risk vs target
                computed_risk = qty * R_per_share
                if risk_dollars > 0 and abs(computed_risk - risk_dollars) / risk_dollars > 0.05:
                    log_decision({"action": "sizing_alert", "symbol": symbol,
                                  "computed": round(computed_risk, 2), "target": round(risk_dollars, 2),
                                  "deviation_pct": round(abs(computed_risk - risk_dollars) / risk_dollars * 100, 1)})
                    try:
                        notify("SIZING ALERT", f"{symbol}: ${computed_risk:.0f} vs target ${risk_dollars:.0f}", "high")
                    except Exception:
                        pass

                log_decision({"action": "entry_sizing", "symbol": symbol,
                              "atr": round(atr_entry, 4), "stop_gap": round(stop_gap, 4),
                              "stop_price": stop_price, "R_per_share": R_per_share,
                              "qty": qty, "risk_dollars": round(risk_dollars, 2)})

                stop = stop_price  # use ATR-based stop
                R    = R_per_share  # use actual R from sizing

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
                        # ── Place REAL stop order in IBKR immediately ──────
                        stop_order_id = None
                        try:
                            from ib_async import Stock as _Stock, StopOrder as _StopOrder
                            _contract = _Stock(symbol, "SMART", "USD")
                            ibkr.ib.qualifyContracts(_contract)
                            _stop_ord = _StopOrder("SELL", qty, round(stop, 2))
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
                            "state": "initial",
                            "R": R,
                            "strategy": strategy_used,
                        }
                        positions.append(new_pos)
                        log_decision({"action": "entry", "symbol": symbol, "qty": qty,
                                      "price": price, "strategy": strategy_used})
                        _log_trade_journal(logs_dir, "entry", symbol, strategy_used,
                                           "BUY", qty, price, stop=stop, order_id=stop_order_id)
                        _log_trade_csv(symbol, "BUY", qty, price, stop_order_id, "Submitted",
                                       portfolio_value=portfolio_value,
                                       risk_dollars=risk_dollars,
                                       entry_price=price,
                                       stop_price=stop,
                                       R_per_share=R_per_share,
                                       computed_shares=qty,
                                       slippage_pct=0.0)
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
