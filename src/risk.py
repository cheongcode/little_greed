"""Risk management helpers: ATR computation, stop prices, position sizing."""
import math
import pandas as pd


def compute_atr(bars: pd.DataFrame, period: int = 14) -> float:
    """Compute ATR on bars DataFrame with Open/High/Low/Close columns."""
    if len(bars) < period + 1:
        return 0.0
    highs  = bars["High"].values
    lows   = bars["Low"].values
    closes = bars["Close"].values
    tr_vals = []
    for i in range(1, len(bars)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_vals.append(tr)
    if len(tr_vals) < period:
        return float(sum(tr_vals) / len(tr_vals)) if tr_vals else 0.0
    # Wilder's smoothing (same as EMA with alpha=1/period)
    atr = sum(tr_vals[:period]) / period
    for tr in tr_vals[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 6)


def compute_stop(entry_price: float, atr: float, rules: dict) -> tuple[float, float]:
    """Compute ATR-based stop price.

    Returns (stop_price, gap_used) where gap_used is the actual distance applied.
    stop = entry - clamp(max(multiplier*atr, min_pct*entry), min_pct*entry, max_pct*entry)
    """
    multiplier = float(rules.get("atr_stop_multiplier", 1.2))
    min_pct    = float(rules.get("min_stop_pct", 0.005))
    max_pct    = float(rules.get("max_stop_pct", 0.025))

    raw_gap = atr * multiplier
    min_gap = entry_price * min_pct
    max_gap = entry_price * max_pct

    gap = max(raw_gap, min_gap)  # at least min_pct
    gap = min(gap, max_gap)      # at most max_pct
    stop = round(entry_price - gap, 4)
    return stop, round(gap, 4)


def compute_shares(portfolio_value: float, risk_dollars: float,
                   entry_price: float, stop_price: float) -> tuple[int, float]:
    """Risk-based position sizing.

    Returns (shares, R_per_share). Raises ValueError if stop >= entry.
    """
    R_per_share = entry_price - stop_price
    if R_per_share <= 0:
        raise ValueError(f"stop_price {stop_price} >= entry_price {entry_price}")
    shares = math.floor(risk_dollars / R_per_share)
    return max(shares, 0), round(R_per_share, 4)


def compute_chandelier_stop(bars: pd.DataFrame, entry_time,
                             atr: float, multiplier: float = 3.0) -> float:
    """Highest high since entry_time minus multiplier * ATR."""
    if bars.empty or atr <= 0:
        return 0.0
    try:
        since_entry = bars[bars.index >= entry_time]
        if since_entry.empty:
            since_entry = bars
        highest_high = float(since_entry["High"].max())
        return round(highest_high - multiplier * atr, 4)
    except Exception:
        return 0.0
