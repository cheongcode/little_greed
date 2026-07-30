"""IBKR data abstraction layer — never imports yfinance."""
import time

import pandas as pd
from ib_async import Stock


def _duration_str(bar_size: str, n_bars: int) -> str:
    """Compute IBKR durationStr from bar_size and n_bars."""
    if bar_size == "1 day":
        return f"{n_bars} D"
    # Intraday: estimate trading days needed
    if bar_size == "5 mins":
        bars_per_day = 78
    elif bar_size == "15 mins":
        bars_per_day = 26
    elif bar_size == "1 min":
        bars_per_day = 390
    else:
        bars_per_day = 78  # fallback
    days_needed = max(1, -(-n_bars // bars_per_day))  # ceiling division
    return f"{days_needed} D"


def _bars_to_df(bars) -> pd.DataFrame:
    """Convert ib_async BarData list to DataFrame with ET timezone index."""
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame([
        dict(Open=b.open, High=b.high, Low=b.low, Close=b.close, Volume=b.volume, date=b.date)
        for b in bars
    ]).set_index("date")
    idx = pd.to_datetime(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("America/New_York")
    else:
        idx = idx.tz_convert("America/New_York")
    df.index = idx
    return df


def get_bars(symbol: str, bar_size: str, n_bars: int, ib) -> pd.DataFrame:
    """Fetch n_bars of bar_size bars from IBKR; falls back to delayed-frozen."""
    contract = Stock(symbol, "SMART", "USD")
    duration = _duration_str(bar_size, n_bars)

    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=duration,
        barSizeSetting=bar_size,
        whatToShow="TRADES",
        useRTH=False,
        keepUpToDate=False,
    )
    time.sleep(0.1)

    if not bars:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="DELAYED_FROZEN_LAST",
            useRTH=False,
            keepUpToDate=False,
        )
        time.sleep(0.1)

    return _bars_to_df(bars)


def get_daily_bars(symbol: str, n_days: int, ib) -> pd.DataFrame:
    """Fetch n_days of daily bars via get_bars."""
    return get_bars(symbol, "1 day", n_days, ib)


def get_intraday_bars(symbol: str, n_bars: int, ib, bar_size: str = "5 mins") -> pd.DataFrame:
    """Fetch n_bars of intraday bars via get_bars."""
    return get_bars(symbol, bar_size, n_bars, ib)
