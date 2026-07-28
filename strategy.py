"""
Strategy engine. Each strategy is a dict with an evaluate(symbol, ib) function.
Active strategy is set by STRATEGY_NAME in rules.json.
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

ET = ZoneInfo("America/New_York")
LOGS_DIR = Path("logs")


def _load_rules():
    try:
        return json.loads(Path("rules.json").read_text())
    except Exception:
        return {}


def _log_result(result: dict):
    LOGS_DIR.mkdir(exist_ok=True)
    with open(LOGS_DIR / "safety_log.jsonl", "a") as f:
        f.write(json.dumps(result) + "\n")


def _get_price(ib, symbol: str) -> float:
    """Get live price via IBKR, fall back to yfinance last close."""
    try:
        from ib_async import Stock
        contract = Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(contract)
        ticker = ib.reqMktData(contract)
        time.sleep(1)
        price = ticker.last or ticker.bid or ticker.close or 0.0
        if price and price > 0:
            return float(price)
    except Exception:
        pass
    # yfinance fallback
    try:
        hist = yf.Ticker(symbol.replace(" B", "-B").replace(" A", "-A")).history(period="2d", interval="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0


def _get_daily_bars(symbol: str, days: int = 60):
    yahoo_sym = symbol.replace(" B", "-B").replace(" A", "-A")
    return yf.Ticker(yahoo_sym).history(period=f"{days}d", interval="1d")


def _get_intraday_bars(symbol: str, period: str = "1d", interval: str = "5m"):
    yahoo_sym = symbol.replace(" B", "-B").replace(" A", "-A")
    return yf.Ticker(yahoo_sym).history(period=period, interval=interval)


def _in_time_window(rules: dict) -> tuple[bool, str]:
    tf = rules.get("time_filter", {})
    now = datetime.now(ET).time()
    try:
        earliest = datetime.strptime(tf.get("earliest_entry_et", "10:05"), "%H:%M").time()
        latest   = datetime.strptime(tf.get("latest_entry_et",  "15:30"), "%H:%M").time()
    except ValueError:
        return False, "invalid time config"
    if earliest <= now <= latest:
        return True, ""
    return False, f"outside entry window {tf.get('earliest_entry_et','10:05')}-{tf.get('latest_entry_et','15:30')}"


def _already_held(symbol: str, ib) -> bool:
    try:
        for pos in ib.positions():
            if pos.contract.symbol == symbol and pos.position > 0:
                return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────────
# STRATEGY 1: Gap and Go (original)
# ─────────────────────────────────────────────────────────────────

def _gap_and_go(symbol: str, ib) -> dict:
    rules = _load_rules()
    df = rules.get("daily_filters", {})
    inf = rules.get("intraday_filters", {})

    result = {"strategy": "gap_and_go", "symbol": symbol,
              "pass": False, "reasons": [], "price": 0.0,
              "filters": {}}

    if _already_held(symbol, ib):
        result["reasons"] = ["already in position"]
        _log_result(result)
        return result

    in_window, reason = _in_time_window(rules)
    if not in_window:
        result["reasons"] = [reason]
        _log_result(result)
        return result

    # Fetch daily bars
    try:
        bars = _get_daily_bars(symbol, days=220)
        if len(bars) < 2:
            result["reasons"] = ["insufficient price history"]
            _log_result(result)
            return result

        today_open  = float(bars["Open"].iloc[-1])
        today_close = float(bars["Close"].iloc[-1])
        today_high  = float(bars["High"].iloc[-1])
        today_low   = float(bars["Low"].iloc[-1])
        prev_close  = float(bars["Close"].iloc[-2])
        prev_high   = float(bars["High"].iloc[-2])

        # Use SMA50 (more responsive) when D2_use_sma50_not_200 is set
        use_sma50 = df.get("D2_use_sma50_not_200", False)
        sma_period = 50 if use_sma50 else 200
        sma_label  = "SMA50" if use_sma50 else "SMA200"
        sma_val    = float(bars["Close"].tail(sma_period).mean()) if len(bars) >= sma_period else float(bars["Close"].mean())

        # Use opening gap (open vs prev close) — more stable than intraday drift
        gap_pct = (today_open - prev_close) / prev_close * 100

        # D1: Today traded above prior day high at some point
        d1 = bool(today_high > prev_high)
        result["filters"]["D1_above_prior_high"] = d1

        # D2: Prior close above SMA (50 or 200, configurable)
        d2_enabled = df.get("D2_enabled", True)
        d2 = bool(prev_close > sma_val) if d2_enabled else True
        result["filters"][f"D2_above_{sma_label}"] = d2

        # D3: Gap %
        min_gap = df.get("D3_min_gap_pct_from_prior_close", 3.0)
        d3 = bool(gap_pct >= min_gap)
        result["filters"]["D3_gap_pct"] = d3
        result["values"] = {
            "gap_pct": round(gap_pct, 2),
            f"{sma_label.lower()}": round(sma_val, 2),
            "prev_close": round(prev_close, 2),
            "today_open": round(today_open, 2),
        }

        reasons = []
        if not d1:
            reasons.append(f"D1 failed: high {today_high:.2f} <= prior high {prev_high:.2f}")
        if not d2 and d2_enabled:
            reasons.append(f"D2 failed: prev_close {prev_close:.2f} below {sma_label} {sma_val:.2f}")
        if not d3:
            reasons.append(f"D3 failed: gap {gap_pct:.1f}% < min {min_gap}%")
        if reasons:
            result["reasons"] = reasons
            _log_result(result)
            return result
    except Exception as e:
        result["reasons"] = [f"daily data error: {e}"]
        _log_result(result)
        return result

    # Fetch intraday bars
    try:
        intra = _get_intraday_bars(symbol, period="1d", interval="5m")
        if len(intra) < 3:
            result["reasons"] = ["insufficient intraday bars"]
            _log_result(result)
            return result

        # Premarket high = max high before 09:30 ET
        premkt = intra[intra.index.tz_convert(ET).time < datetime.strptime("09:30", "%H:%M").time()]
        premkt_high = float(premkt["High"].max()) if not premkt.empty else 0.0

        current_hod = float(intra["High"].max())
        current_price = float(intra["Close"].iloc[-1])

        # RVOL — time-normalized: compare today's volume so far to same elapsed
        # bars on prior days (avoids the afternoon drop from morning RVOL)
        rvol_lookback = inf.get("I3_rvol_lookback_days", 14)
        time_normalize = inf.get("I3_time_normalize", True)

        # Count how many 5m bars have elapsed today
        bars_today = len(intra)

        if time_normalize and bars_today > 0:
            # Get multi-day 5m bars to compare same elapsed period
            try:
                intra_hist = _get_intraday_bars(symbol, period=f"{rvol_lookback}d", interval="5m")
                intra_hist_et = intra_hist.copy()
                intra_hist_et.index = intra_hist_et.index.tz_convert(ET)
                today_date = datetime.now(ET).date()

                # Sum volume for first N bars of each prior day
                prior_vols = []
                for day_offset in range(1, rvol_lookback + 1):
                    from datetime import timedelta
                    check_date = today_date - timedelta(days=day_offset)
                    day_bars = intra_hist_et[intra_hist_et.index.date == check_date]
                    if len(day_bars) >= bars_today:
                        prior_vols.append(float(day_bars["Volume"].iloc[:bars_today].sum()))
                if prior_vols:
                    avg_same_period_vol = sum(prior_vols) / len(prior_vols)
                    today_vol_so_far = float(intra["Volume"].sum())
                    rvol = today_vol_so_far / avg_same_period_vol if avg_same_period_vol > 0 else 0.0
                else:
                    raise ValueError("no prior bars")
            except Exception:
                avg_daily_vol = float(bars["Volume"].tail(rvol_lookback).mean())
                today_vol = float(bars["Volume"].iloc[-1])
                rvol = today_vol / avg_daily_vol if avg_daily_vol > 0 else 0.0
        else:
            avg_daily_vol = float(bars["Volume"].tail(rvol_lookback).mean())
            today_vol = float(bars["Volume"].iloc[-1])
            rvol = today_vol / avg_daily_vol if avg_daily_vol > 0 else 0.0

        # I1: Above premarket high
        i1 = bool(current_price > premkt_high) if premkt_high > 0 else True
        result["filters"]["I1_above_premkt_high"] = i1

        # I2: Near today HOD (configurable proximity)
        hod_proximity = inf.get("I2_hod_proximity_pct", 0.97)
        i2 = bool(current_price >= current_hod * hod_proximity)
        result["filters"]["I2_near_hod"] = i2

        # I3: RVOL
        rvol_min = inf.get("I3_rvol_min", 2.0)
        i3 = bool(rvol >= rvol_min)
        result["filters"]["I3_rvol"] = i3
        result["values"].update({"rvol": round(rvol, 2), "premkt_high": round(premkt_high, 2), "price": round(current_price, 2)})

        reasons = []
        if not i1:
            reasons.append(f"I1 failed: price {current_price:.2f} <= premkt high {premkt_high:.2f}")
        if not i2:
            reasons.append(f"I2 failed: price {current_price:.2f} not near HOD {current_hod:.2f}")
        if not i3:
            reasons.append(f"I3 failed: RVOL {rvol:.1f}x < min {rvol_min}x")
        if reasons:
            result["reasons"] = reasons
            _log_result(result)
            return result

        result["pass"] = bool(True)
        result["price"] = current_price
        result["reasons"] = ["all filters passed"]
        _log_result(result)
        return result

    except Exception as e:
        result["reasons"] = [f"intraday data error: {e}"]
        _log_result(result)
        return result


# ─────────────────────────────────────────────────────────────────
# STRATEGY 2: Opening Range Breakout (ORB 15-min)
# ─────────────────────────────────────────────────────────────────

def _orb_15(symbol: str, ib) -> dict:
    rules = _load_rules()
    result = {"strategy": "orb_15", "symbol": symbol,
              "pass": False, "reasons": [], "price": 0.0, "filters": {}}

    if _already_held(symbol, ib):
        result["reasons"] = ["already in position"]
        _log_result(result)
        return result

    in_window, reason = _in_time_window(rules)
    if not in_window:
        result["reasons"] = [reason]
        _log_result(result)
        return result

    try:
        intra = _get_intraday_bars(symbol, period="1d", interval="5m")
        if intra.empty:
            result["reasons"] = ["no intraday data"]
            _log_result(result)
            return result

        intra_et = intra.copy()
        intra_et.index = intra_et.index.tz_convert(ET)

        open_time  = datetime.now(ET).replace(hour=9, minute=30, second=0, microsecond=0)
        range_end  = open_time + timedelta(minutes=15)

        or_bars = intra_et[(intra_et.index >= open_time) & (intra_et.index < range_end)]
        if len(or_bars) < 3:
            result["reasons"] = ["opening range not yet established"]
            _log_result(result)
            return result

        or_high = float(or_bars["High"].max())
        or_low  = float(or_bars["Low"].min())
        current = float(intra_et["Close"].iloc[-1])

        # Daily bars for trend filter
        daily = _get_daily_bars(symbol, days=210)
        sma200 = float(daily["Close"].tail(200).mean()) if len(daily) >= 200 else 0.0
        prev_close = float(daily["Close"].iloc[-2]) if len(daily) >= 2 else 0.0

        d1 = bool(prev_close > sma200) if sma200 > 0 else True
        i1 = bool(current > or_high)
        i2 = bool(or_high - or_low < or_high * 0.02)  # tight range < 2%

        result["filters"] = {"D1_above_sma200": d1, "I1_breakout_above_or": i1, "I2_tight_range": i2}
        result["values"]  = {"or_high": round(or_high, 2), "or_low": round(or_low, 2), "price": round(current, 2)}

        reasons = []
        if not d1: reasons.append(f"D1: below SMA200")
        if not i1: reasons.append(f"I1: no ORB breakout ({current:.2f} vs {or_high:.2f})")
        if not i2: reasons.append(f"I2: range too wide")

        if reasons:
            result["reasons"] = reasons
        else:
            result["pass"] = bool(True)
            result["price"] = current
            result["reasons"] = ["ORB breakout confirmed"]

        _log_result(result)
        return result

    except Exception as e:
        result["reasons"] = [f"error: {e}"]
        _log_result(result)
        return result


# ─────────────────────────────────────────────────────────────────
# STRATEGY 3: VWAP Reclaim
# ─────────────────────────────────────────────────────────────────

def _vwap_reclaim(symbol: str, ib) -> dict:
    rules = _load_rules()
    result = {"strategy": "vwap_reclaim", "symbol": symbol,
              "pass": False, "reasons": [], "price": 0.0, "filters": {}}

    if _already_held(symbol, ib):
        result["reasons"] = ["already in position"]
        _log_result(result)
        return result

    in_window, reason = _in_time_window(rules)
    if not in_window:
        result["reasons"] = [reason]
        _log_result(result)
        return result

    try:
        intra = _get_intraday_bars(symbol, period="1d", interval="5m")
        if len(intra) < 10:
            result["reasons"] = ["insufficient intraday data"]
            _log_result(result)
            return result

        # Compute VWAP
        tp = (intra["High"] + intra["Low"] + intra["Close"]) / 3
        vwap = float((tp * intra["Volume"]).cumsum().iloc[-1] / intra["Volume"].cumsum().iloc[-1])
        current = float(intra["Close"].iloc[-1])
        prev    = float(intra["Close"].iloc[-2])

        # Reclaim: prior bar was below VWAP, current bar is above
        prev_tp  = float(tp.iloc[-2])
        i1 = bool(prev < vwap and current > vwap)

        # Volume on reclaim bar > 1.5x prior bar
        i2 = bool(float(intra["Volume"].iloc[-1]) > float(intra["Volume"].iloc[-2]) * 1.5)

        # Daily trend
        daily  = _get_daily_bars(symbol, days=210)
        sma200 = float(daily["Close"].tail(200).mean()) if len(daily) >= 200 else 0.0
        d1 = bool(float(daily["Close"].iloc[-1]) > sma200) if sma200 > 0 else True

        result["filters"] = {"D1_above_sma200": d1, "I1_vwap_reclaim": i1, "I2_volume_surge": i2}
        result["values"]  = {"vwap": round(vwap, 2), "price": round(current, 2)}

        reasons = []
        if not d1: reasons.append("D1: below SMA200")
        if not i1: reasons.append(f"I1: no VWAP reclaim (price={current:.2f}, vwap={vwap:.2f})")
        if not i2: reasons.append("I2: weak volume on reclaim")

        if reasons:
            result["reasons"] = reasons
        else:
            result["pass"] = bool(True)
            result["price"] = current
            result["reasons"] = ["VWAP reclaim confirmed"]

        _log_result(result)
        return result

    except Exception as e:
        result["reasons"] = [f"error: {e}"]
        _log_result(result)
        return result


# ─────────────────────────────────────────────────────────────────
# STRATEGY 4: HOD Break
# ─────────────────────────────────────────────────────────────────

def _hod_break(symbol: str, ib) -> dict:
    rules = _load_rules()
    result = {"strategy": "hod_break", "symbol": symbol,
              "pass": False, "reasons": [], "price": 0.0, "filters": {}}

    if _already_held(symbol, ib):
        result["reasons"] = ["already in position"]
        _log_result(result)
        return result

    in_window, reason = _in_time_window(rules)
    if not in_window:
        result["reasons"] = [reason]
        _log_result(result)
        return result

    try:
        intra = _get_intraday_bars(symbol, period="1d", interval="5m")
        if len(intra) < 5:
            result["reasons"] = ["insufficient intraday data"]
            _log_result(result)
            return result

        current  = float(intra["Close"].iloc[-1])
        hod      = float(intra["High"].max())
        prev_hod = float(intra["High"].iloc[:-1].max())

        # New HOD on current bar
        i1 = bool(current >= hod * 0.998 and hod > prev_hod)

        # Volume on breakout bar > 2x recent avg
        avg_vol = float(intra["Volume"].iloc[:-1].mean())
        i2 = bool(float(intra["Volume"].iloc[-1]) > avg_vol * 2.0)

        daily  = _get_daily_bars(symbol, days=210)
        sma200 = float(daily["Close"].tail(200).mean()) if len(daily) >= 200 else 0.0
        prev_day_high = float(daily["High"].iloc[-2]) if len(daily) >= 2 else 0.0
        d1 = bool(current > prev_day_high)
        d2 = bool(float(daily["Close"].iloc[-1]) > sma200) if sma200 > 0 else True

        result["filters"] = {"D1_above_prev_high": d1, "D2_above_sma200": d2, "I1_new_hod": i1, "I2_volume_surge": i2}
        result["values"]  = {"hod": round(hod, 2), "price": round(current, 2)}

        reasons = []
        if not d1: reasons.append(f"D1: below prior day high {prev_day_high:.2f}")
        if not d2: reasons.append("D2: below SMA200")
        if not i1: reasons.append(f"I1: not at HOD ({current:.2f} vs {hod:.2f})")
        if not i2: reasons.append("I2: insufficient volume")

        if reasons:
            result["reasons"] = reasons
        else:
            result["pass"] = bool(True)
            result["price"] = current
            result["reasons"] = ["HOD break with volume confirmed"]

        _log_result(result)
        return result

    except Exception as e:
        result["reasons"] = [f"error: {e}"]
        _log_result(result)
        return result


# ─────────────────────────────────────────────────────────────────
# STRATEGY 5: Volume Spike (aggressive — fires anytime)
# Any stock with sudden 3x+ RVOL, price within 3% of HOD
# ─────────────────────────────────────────────────────────────────

def _volume_spike(symbol: str, ib) -> dict:
    rules = _load_rules()
    result = {"strategy": "volume_spike", "symbol": symbol,
              "pass": False, "reasons": [], "price": 0.0, "filters": {}}

    if _already_held(symbol, ib):
        result["reasons"] = ["already in position"]
        _log_result(result); return result

    in_window, reason = _in_time_window(rules)
    if not in_window:
        result["reasons"] = [reason]
        _log_result(result); return result

    try:
        intra = _get_intraday_bars(symbol, period="5d", interval="5m")
        if len(intra) < 10:
            result["reasons"] = ["insufficient bars"]
            _log_result(result); return result

        intra_et = intra.copy()
        intra_et.index = intra_et.index.tz_convert(ET)
        today = datetime.now(ET).date()
        today_bars = intra_et[intra_et.index.date == today]

        if len(today_bars) < 3:
            result["reasons"] = ["market too early"]
            _log_result(result); return result

        current      = float(today_bars["Close"].iloc[-1])
        current_vol  = float(today_bars["Volume"].iloc[-1])
        hod          = float(today_bars["High"].max())
        avg_5m_vol   = float(intra["Volume"].iloc[:-len(today_bars)].tail(100).mean())

        # I1: Current bar RVOL > 3x (very aggressive filter)
        rvol = current_vol / avg_5m_vol if avg_5m_vol > 0 else 0
        i1 = bool(rvol >= 3.0)

        # I2: Price within 3% of HOD
        i2 = bool(current >= hod * 0.97)

        # I3: Positive price action (current > open of the spike bar)
        i3 = bool(float(today_bars["Close"].iloc[-1]) > float(today_bars["Open"].iloc[-1]))

        # D1: Price > $5 (avoid penny stocks)
        d1 = bool(current > 5.0)

        result["filters"] = {"D1_min_price": d1, "I1_rvol_3x": i1, "I2_near_hod": i2, "I3_bullish_bar": i3}
        result["values"]  = {"rvol": round(rvol, 1), "price": round(current, 2), "hod": round(hod, 2)}

        reasons = []
        if not d1: reasons.append(f"D1: price ${current:.2f} < $5")
        if not i1: reasons.append(f"I1: RVOL {rvol:.1f}x < 3.0x")
        if not i2: reasons.append(f"I2: price {current:.2f} not near HOD {hod:.2f}")
        if not i3: reasons.append("I3: bearish bar")

        if reasons:
            result["reasons"] = reasons
        else:
            result["pass"]  = bool(True)
            result["price"] = current
            result["reasons"] = ["volume spike confirmed — 3x+ RVOL near HOD"]
        _log_result(result); return result

    except Exception as e:
        result["reasons"] = [f"error: {e}"]
        _log_result(result); return result


# ─────────────────────────────────────────────────────────────────
# STRATEGY 6: Momentum 15 (aggressive — any 15-min surge)
# Up 1.5%+ in last 15 min + RVOL > 1.5x + above VWAP
# ─────────────────────────────────────────────────────────────────

def _momentum_15(symbol: str, ib) -> dict:
    rules = _load_rules()
    result = {"strategy": "momentum_15", "symbol": symbol,
              "pass": False, "reasons": [], "price": 0.0, "filters": {}}

    if _already_held(symbol, ib):
        result["reasons"] = ["already in position"]
        _log_result(result); return result

    in_window, reason = _in_time_window(rules)
    if not in_window:
        result["reasons"] = [reason]
        _log_result(result); return result

    try:
        intra = _get_intraday_bars(symbol, period="5d", interval="5m")
        if len(intra) < 10:
            result["reasons"] = ["insufficient bars"]
            _log_result(result); return result

        intra_et = intra.copy()
        intra_et.index = intra_et.index.tz_convert(ET)
        today = datetime.now(ET).date()
        today_bars = intra_et[intra_et.index.date == today]

        if len(today_bars) < 3:
            result["reasons"] = ["market too early"]
            _log_result(result); return result

        current     = float(today_bars["Close"].iloc[-1])
        price_3ago  = float(today_bars["Close"].iloc[-4]) if len(today_bars) >= 4 else float(today_bars["Close"].iloc[0])
        move_15m    = (current - price_3ago) / price_3ago * 100

        # VWAP
        tp   = (today_bars["High"] + today_bars["Low"] + today_bars["Close"]) / 3
        vwap = float((tp * today_bars["Volume"]).cumsum().iloc[-1] / today_bars["Volume"].cumsum().iloc[-1])

        # RVOL vs prior days same period
        bars_today = len(today_bars)
        avg_5m_vol = float(intra["Volume"].iloc[:-bars_today].tail(100).mean())
        today_vol  = float(today_bars["Volume"].sum())
        rvol       = today_vol / (avg_5m_vol * bars_today) if avg_5m_vol > 0 else 0

        i1 = bool(move_15m >= 1.5)          # 1.5%+ in 15 min
        i2 = bool(current > vwap)            # above VWAP
        i3 = bool(rvol >= 1.5)              # volume confirming
        d1 = bool(current > 5.0)             # not a penny stock

        result["filters"] = {"D1_min_price": d1, "I1_15m_surge": i1, "I2_above_vwap": i2, "I3_rvol": i3}
        result["values"]  = {"move_15m_pct": round(move_15m, 2), "vwap": round(vwap, 2),
                             "price": round(current, 2), "rvol": round(rvol, 2)}

        reasons = []
        if not d1: reasons.append(f"D1: price ${current:.2f} < $5")
        if not i1: reasons.append(f"I1: 15-min move {move_15m:.1f}% < 1.5%")
        if not i2: reasons.append(f"I2: price {current:.2f} below VWAP {vwap:.2f}")
        if not i3: reasons.append(f"I3: RVOL {rvol:.1f}x < 1.5x")

        if reasons:
            result["reasons"] = reasons
        else:
            result["pass"]  = bool(True)
            result["price"] = current
            result["reasons"] = [f"15-min surge {move_15m:+.1f}% above VWAP with RVOL {rvol:.1f}x"]
        _log_result(result); return result

    except Exception as e:
        result["reasons"] = [f"error: {e}"]
        _log_result(result); return result


# ─────────────────────────────────────────────────────────────────
# STRATEGY 7: Trend Rider (continuous — runs all day)
# Price > 9EMA > 20EMA on 5-min, above VWAP, RVOL > 1.2x
# Most permissive — designed to find trend continuation all day
# ─────────────────────────────────────────────────────────────────

def _trend_rider(symbol: str, ib) -> dict:
    rules = _load_rules()
    result = {"strategy": "trend_rider", "symbol": symbol,
              "pass": False, "reasons": [], "price": 0.0, "filters": {}}

    if _already_held(symbol, ib):
        result["reasons"] = ["already in position"]
        _log_result(result); return result

    in_window, reason = _in_time_window(rules)
    if not in_window:
        result["reasons"] = [reason]
        _log_result(result); return result

    try:
        intra = _get_intraday_bars(symbol, period="5d", interval="5m")
        if len(intra) < 25:
            result["reasons"] = ["need 25+ bars for EMAs"]
            _log_result(result); return result

        intra_et = intra.copy()
        intra_et.index = intra_et.index.tz_convert(ET)
        today = datetime.now(ET).date()
        today_bars = intra_et[intra_et.index.date == today]

        if len(today_bars) < 5:
            result["reasons"] = ["not enough today bars"]
            _log_result(result); return result

        closes   = intra["Close"]
        ema9     = float(closes.ewm(span=9,  adjust=False).mean().iloc[-1])
        ema20    = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
        current  = float(closes.iloc[-1])

        tp   = (today_bars["High"] + today_bars["Low"] + today_bars["Close"]) / 3
        vwap = float((tp * today_bars["Volume"]).cumsum().iloc[-1] / today_bars["Volume"].cumsum().iloc[-1])

        bars_today = len(today_bars)
        avg_5m_vol = float(intra["Volume"].iloc[:-bars_today].tail(100).mean())
        today_vol  = float(today_bars["Volume"].sum())
        rvol       = today_vol / (avg_5m_vol * bars_today) if avg_5m_vol > 0 else 0

        # Daily gap for context
        daily = _get_daily_bars(symbol, days=5)
        gap_pct = 0.0
        if len(daily) >= 2:
            gap_pct = (float(daily["Open"].iloc[-1]) - float(daily["Close"].iloc[-2])) / float(daily["Close"].iloc[-2]) * 100

        i1 = bool(current > ema9)            # above 9 EMA
        i2 = bool(ema9 > ema20)              # 9 EMA > 20 EMA (uptrend)
        i3 = bool(current > vwap)            # above VWAP
        i4 = bool(rvol >= 1.2)              # any above-avg volume
        d1 = bool(current > 5.0)             # not penny stock
        d2 = bool(gap_pct >= 0)             # gapped up or flat (not gapped down)

        result["filters"] = {"D1_min_price": d1, "D2_no_gap_down": d2,
                             "I1_above_9ema": i1, "I2_9ema_above_20ema": i2,
                             "I3_above_vwap": i3, "I4_rvol": i4}
        result["values"]  = {"ema9": round(ema9, 2), "ema20": round(ema20, 2),
                             "vwap": round(vwap, 2), "price": round(current, 2),
                             "rvol": round(rvol, 2), "gap_pct": round(gap_pct, 2)}

        reasons = []
        if not d1: reasons.append(f"D1: price ${current:.2f} < $5")
        if not d2: reasons.append(f"D2: gap down {gap_pct:.1f}%")
        if not i1: reasons.append(f"I1: price {current:.2f} below 9EMA {ema9:.2f}")
        if not i2: reasons.append(f"I2: 9EMA {ema9:.2f} < 20EMA {ema20:.2f}")
        if not i3: reasons.append(f"I3: price below VWAP {vwap:.2f}")
        if not i4: reasons.append(f"I4: RVOL {rvol:.1f}x < 1.2x")

        if reasons:
            result["reasons"] = reasons
        else:
            result["pass"]  = bool(True)
            result["price"] = current
            result["reasons"] = [f"trend rider: 9EMA>{ema9:.2f} > 20EMA>{ema20:.2f}, above VWAP, RVOL {rvol:.1f}x"]
        _log_result(result); return result

    except Exception as e:
        result["reasons"] = [f"error: {e}"]
        _log_result(result); return result


# ─────────────────────────────────────────────────────────────────
# Registry + multi-strategy concurrent dispatcher
# ─────────────────────────────────────────────────────────────────

STRATEGIES = {
    "gap_and_go":   _gap_and_go,
    "orb_15":       _orb_15,
    "vwap_reclaim": _vwap_reclaim,
    "hod_break":    _hod_break,
    "volume_spike": _volume_spike,
    "momentum_15":  _momentum_15,
    "trend_rider":  _trend_rider,
}

STRATEGY_DESCRIPTIONS = {
    "gap_and_go":   "Gap >1.5% at open, SMA50 trend, RVOL >1.2x, near HOD — best 10:05–12:00 ET",
    "orb_15":       "Break above 15-min opening range with tight range — best 9:45–10:30 ET",
    "vwap_reclaim": "Price dips below VWAP then reclaims with volume surge — best 10:30–14:00 ET",
    "hod_break":    "New high of day with 2x volume above prior day high — best 11:00–14:00 ET",
    "volume_spike": "⚡ AGGRESSIVE: 3x+ RVOL on any bar near HOD — fires all day",
    "momentum_15":  "⚡ AGGRESSIVE: 1.5%+ surge in 15 min above VWAP — fires all day",
    "trend_rider":  "⚡ AGGRESSIVE: Price > 9EMA > 20EMA above VWAP with volume — continuous",
}


def evaluate(symbol: str, ib) -> dict:
    """Single-strategy evaluation (used by bot.py --check-only)."""
    rules = _load_rules()
    strategy_name = rules.get("strategy_name_key", "gap_and_go")
    fn = STRATEGIES.get(strategy_name, _gap_and_go)
    return fn(symbol, ib)


def evaluate_all(symbol: str, ib) -> dict:
    """Multi-strategy concurrent evaluation. Returns first passing strategy result,
    or the result with the most filters passed if none pass."""
    rules = _load_rules()
    active = rules.get("active_strategies", ["gap_and_go"])

    best = None
    best_score = -1

    for strat_name in active:
        fn = STRATEGIES.get(strat_name)
        if not fn:
            continue
        r = fn(symbol, ib)
        if r["pass"]:
            return r  # first pass wins — enter immediately
        score = sum(1 for v in r.get("filters", {}).values() if v)
        if score > best_score:
            best_score = score
            best = r

    return best or {"strategy": "none", "symbol": symbol, "pass": False,
                    "reasons": ["no active strategies"], "price": 0.0, "filters": {}}


def list_strategies() -> dict:
    return {k: v for k, v in STRATEGY_DESCRIPTIONS.items()}
