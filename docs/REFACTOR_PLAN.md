# little_greed Refactor Plan
**Status: IN PROGRESS**
**Lead**: Claude Code orchestrator  
**Date**: 2026-07-31

---

## Overview

10-objective overhaul of the little_greed paper-trading bot. Core problems being fixed:
1. yfinance in hot paths → replace with IBKR real-time 5-min bars
2. "First pass wins" dispatcher → expectancy-ranked dispatcher
3. Fixed 1% stops → ATR-based stops
4. Broken position sizing → risk-dollar-based sizing
5. No systematic analysis → nightly diagnostic report
6. Universe too narrow → add Russell 2000 with liquidity filter
7. No layered exits → full R-based partial exit + Chandelier trail system
8. New Noise Area strategy for SPY/QQQ/IWM (published academic edge)
9. Expanded rules.json config + Settings UI
10. Comprehensive CSV logging for diagnostic reporting

---

## File Ownership (no conflicts between parallel agents)

| Agent | Files Owned |
|-------|-------------|
| A — Data | `src/ibkr_client.py`, `src/data_shim.py` (new), `src/universe.py` (new), `morning_prefilter.py` |
| B — Strategy | `strategy.py` |
| C — Risk/Exits | `cycle.py`, `src/risk.py` (new) |
| D — Diagnostics | `nightly_report.py` (new), `run.py`, `src/logging_helpers.py` (new) |
| E — Config/UI | `rules.json`, `webui.py`, `templates/settings.html` |
| F — Verification | Read-only; runs `python bot.py --symbol SPY --check-only` |

---

## API Contracts (all agents code against these specs)

### `src/data_shim.py` (created by Agent A)

```python
def get_bars(symbol: str, bar_size: str, n_bars: int, ib) -> pd.DataFrame:
    """
    Fetch bars from IBKR via reqHistoricalData(keepUpToDate=True).
    bar_size: '5 mins' | '1 day' | '15 mins'
    Falls back to IBKR delayed-frozen if real-time not available.
    Never calls yfinance.
    Returns DataFrame with columns: Open, High, Low, Close, Volume
    Index: DatetimeTzDtype(tz='America/New_York')
    """

def get_daily_bars(symbol: str, n_days: int, ib) -> pd.DataFrame:
    """n_days of '1 day' bars. Convenience wrapper over get_bars()."""

def get_intraday_bars(symbol: str, n_bars: int, ib, bar_size: str = '5 mins') -> pd.DataFrame:
    """n_bars of intraday bars. Convenience wrapper over get_bars()."""
```

**Strategy.py must replace:**
- `_get_daily_bars(symbol, days)` → `get_daily_bars(symbol, days, ib)`  
- `_get_intraday_bars(symbol, period, interval)` → `get_intraday_bars(symbol, n_bars, ib)`  
- `_get_price(ib, symbol)` remains IBKR-only (already uses IBKR first, yfinance fallback — remove fallback)

### `src/risk.py` (created by Agent C)

```python
def compute_atr(bars: pd.DataFrame, period: int = 14) -> float:
    """Compute ATR on 5-min bars. True Range = max(H-L, |H-prev_C|, |L-prev_C|)."""

def compute_stop(entry_price: float, atr: float, rules: dict) -> float:
    """
    ATR-based stop per rules:
      raw = entry - max(multiplier * atr, min_pct * entry)
      clamped to [entry * (1 - max_pct), entry * (1 - min_pct)]
    Returns stop_price.
    """

def compute_shares(portfolio_value: float, risk_dollars: float,
                   entry_price: float, stop_price: float) -> tuple[int, float]:
    """
    Risk-based position sizing.
    R_per_share = entry_price - stop_price
    shares = floor(risk_dollars / R_per_share)
    Returns (shares, R_per_share).
    Raises ValueError if R_per_share <= 0.
    """

def compute_chandelier_stop(bars: pd.DataFrame, entry_idx: int,
                             atr: float, multiplier: float = 3.0) -> float:
    """Highest high since entry_idx minus multiplier * ATR."""
```

### `src/logging_helpers.py` (created by Agent D)

```python
def log_signal_csv(ts, symbol, strategy, filters: dict, thresholds: dict, bar_values: dict):
    """Append to signals.csv — one row per strategy evaluation per bar."""

def log_position_update_csv(ts, symbol, current_price, unrealized_R, stop_level, action_taken):
    """Append to position_updates.csv — one row per manage_position() call."""

def log_rejected_signal_csv(ts, symbol, winning_strategy, rejected_strategy, reason):
    """Append to rejected_signals.csv — for dispatcher losers."""

def log_exit_csv(ts, symbol, strategy, exit_trigger, qty_sold, fill_price,
                 entry_price, r_multiple, pnl, remaining_qty):
    """Append to exits.csv — every partial and full exit."""
```

### `src/expectancy.py` (created by Agent B)

```python
def rolling_expectancy(strategy_name: str, n: int = 20) -> float:
    """
    Compute rolling expectancy from last n closed trades for strategy_name
    in trade_journal.jsonl.
    expectancy = (win_rate * avg_win_R) - (loss_rate * avg_loss_R)
    Returns 0.0 if fewer than 3 trades available.
    """
```

---

## Objective Specifications

### Objective 1 — Replace yfinance with IBKR data (Agent A)

**`src/ibkr_client.py` additions:**
```python
def get_historical_bars(self, symbol: str, bar_size: str, 
                        duration: str, end_dt: str = '') -> pd.DataFrame:
    """Wraps reqHistoricalData. bar_size e.g. '5 mins', '1 day'."""

def get_live_bars(self, symbol: str, bar_size: str = '5 mins',
                  lookback_bars: int = 78) -> pd.DataFrame:
    """reqHistoricalData with keepUpToDate=True for real-time streaming bars."""
```

**`src/data_shim.py`:**  
- Primary: `reqHistoricalData` with `whatToShow='TRADES'`  
- Fallback: Same call with `whatToShow='DELAYED_FROZEN_LAST'`  
- Never import yfinance  
- Return value must be a pandas DataFrame with ET-timezone index and columns Open/High/Low/Close/Volume  

**Remove yfinance from:**
- `strategy.py` — `_get_daily_bars`, `_get_intraday_bars`, yfinance fallback in `_get_price`
- `morning_prefilter.py` — entire yfinance batch download
- `cycle.py` — yfinance fallback in `manage_position` (Agent C handles this)

### Objective 2 — Noise Area Strategy (Agent B)

**Location:** New function `_noise_area(symbol, ib)` in strategy.py  
**Universe filter:** symbol must be in `{'SPY', 'QQQ', 'IWM'}` — fail immediately if not  
**Entry timing:** Only fire at HH:00 or HH:30 bars (within 1 minute of :00 or :30)

**Boundary computation:**
```python
# Get 14 trading days of 5-min bars
bars_14d = get_intraday_bars(symbol, 14*78, ib)
today_open = float(today_bars['Open'].iloc[0])
current_minute = current_bar.name.strftime('%H:%M')  # e.g. '10:30'

# For each past trading day, compute: (price_at_current_minute - day_open) / day_open
hist_returns = []
for day in past_14_trading_days:
    day_bars = bars_14d[bars_14d.index.date == day]
    if day_bars.empty: continue
    day_open = float(day_bars['Open'].iloc[0])
    bar_at_t = day_bars[day_bars.index.strftime('%H:%M') == current_minute]
    if bar_at_t.empty: continue
    ret = (float(bar_at_t['Close'].iloc[-1]) - day_open) / day_open
    hist_returns.append(ret)

avg_ret = np.mean(hist_returns) if hist_returns else 0.0
upper_boundary = today_open * (1 + abs(avg_ret))
lower_boundary = today_open * (1 - abs(avg_ret))
```

**Entry condition:** `current_price > upper_boundary`  
**Exit condition (managed in cycle.py):** First 5-min bar close at or below VWAP after being above it, OR 15:51 force close  
**Long only. No short entries.**

**Result dict keys** (same as other strategies):
```python
result = {
    "strategy": "noise_area", "symbol": symbol,
    "pass": bool, "reasons": list, "price": float,
    "filters": {
        "F1_etf_universe": bool,
        "F2_timing_half_hour": bool, 
        "F3_above_upper_boundary": bool,
    },
    "values": {
        "upper_boundary": float, "lower_boundary": float,
        "avg_ret_14d": float, "price": float
    }
}
```

### Objective 3 — Expectancy-Ranked Dispatcher (Agent B)

**Replace `evaluate_all()` in strategy.py:**

```python
STRATEGY_PRIORITY = {
    "noise_area":    0,
    "gap_and_go":    1,
    "orb_15":        2,
    "vwap_reclaim":  3,
    "hod_break":     4,
    "momentum_15":   5,
    "volume_spike":  6,
    "trend_rider":   7,
}

def evaluate_all(symbol: str, ib) -> dict:
    """
    1. Run all active strategies.
    2. Collect all that pass.
    3. If multiple pass: rank by rolling_expectancy(strategy, 20), highest wins.
       Tie-break by STRATEGY_PRIORITY (lower number = higher priority).
    4. Log all losing passers to rejected_signals.csv.
    5. If none pass: return highest filter-score result (existing behavior).
    """
```

**Key constraint:** Do NOT change any strategy's filter logic. Only change selection logic.

### Objective 4 — ATR-Based Stops (Agent C)

**Formula (in `src/risk.py`):**
```python
# rules.json keys used:
atr_stop_multiplier = rules.get('atr_stop_multiplier', 1.2)
min_stop_pct        = rules.get('min_stop_pct', 0.005)   # 0.5%
max_stop_pct        = rules.get('max_stop_pct', 0.025)   # 2.5%

atr14   = compute_atr(bars_5m, 14)
raw_gap = atr14 * atr_stop_multiplier
min_gap = entry_price * min_stop_pct
max_gap = entry_price * max_stop_pct

gap     = max(raw_gap, min_gap)      # at least 0.5%
gap     = min(gap, max_gap)          # at most 2.5%
stop    = entry_price - gap
```

**Every entry must log:** `atr_value`, `computed_stop_gap`, `stop_price` to safety log.

### Objective 5 — Universe Expansion (Agent A)

**`src/universe.py`:** Contains `RUSSELL_2000_LIQUID` — static list of ~150 liquid Russell 2000 names  
(Use well-known liquid mid-caps: IWM components with high avg dollar volume)

**`morning_prefilter.py` changes:**
- Combined universe = SP500_TICKERS + RUSSELL_2000_LIQUID (deduplicated)  
- After gap filter: apply secondary filters via IBKR:
  - 20-day avg dollar volume > $10M (price × volume > $10M daily avg)
  - Price > $5
  - Skip earnings check (log "earnings check not implemented" in watchlist header)
- Cap final watchlist at **40** names (up from 20), sort by gap_pct descending

### Objective 6 — Fix Position Sizing (Agent C)

**Replace current broken sizing in `cycle.py`:**
```python
# BROKEN (current):
budget = min(max_trade_usd, portfolio_value * 0.10)
qty = int(budget / price)

# CORRECT (new):
risk_pct    = float(os.getenv('MAX_RISK_PER_TRADE_PCT', '1.0')) / 100
risk_dollars = portfolio_value * risk_pct
R_per_share  = entry_price - stop_price   # from ATR stop
shares, _    = compute_shares(portfolio_value, risk_dollars, entry_price, stop_price)

# Assertion
computed_risk = shares * R_per_share
if abs(computed_risk - risk_dollars) / risk_dollars > 0.05:
    log_decision({"action": "sizing_alert", "symbol": symbol,
                  "computed": computed_risk, "target": risk_dollars})
    notify("SIZING ALERT", f"{symbol}: computed ${computed_risk:.0f} vs target ${risk_dollars:.0f}", "high")
```

**Expand `trades.csv` header** (backward compat — old rows get empty fields):
```
timestamp_iso,symbol,side,size,fill_price,order_id,status,
portfolio_value,risk_dollars,entry_price,stop_price,R_per_share,
computed_shares,actual_fill_shares,slippage_pct
```

### Objective 7 — Layered Exit System (Agent C)

**Position state machine** (field `state` in open_positions.json):

| State | Trigger | Action |
|-------|---------|--------|
| `initial` | entry | ATR stop placed |
| `scale1` | +0.75R | Sell 25%, move stop to entry - 0.25R |
| `scale2` | +1.5R | Sell 25% more (50% remaining), stop to breakeven |
| `scale3` | +2.5R | Sell 25% more (25% remaining), start Chandelier trail |
| `runner` | after scale3 | Trail with Chandelier until stop or 15:51 |

**Additional exit triggers (check every cycle):**
- **VWAP reversal:** If bar closes below VWAP after being above it for >30 min → sell 50% of remaining
- **Gap reversal:** If 5-min bar prints -1.5 × ATR from prior close → sell 100%
- **Time decay:** At 14:00, if unrealized < 0.25R → reduce to 50%
- **Force close:** 15:51 → sell 100%

**Every exit event logged to `exits.csv` via `log_exit_csv()`.**

### Objective 8 — Nightly Report (Agent D)

**`nightly_report.py`:** Standalone script, triggered by `run.py` scheduler at 16:30 ET

**Input data sources:**
- `logs/trade_journal.jsonl` — per-trade strategy/R/pnl
- `trades.csv` — fills with portfolio_value/sizing data
- `exits.csv` — exit triggers
- `signals.csv` — filter pass/fail per evaluation
- `rejected_signals.csv` — dispatcher losers
- `position_updates.csv` — per-cycle position states

**Output:** `reports/YYYY-MM-DD.md` + Telegram summary  
**Must run without error even on first day (empty data = graceful "no data yet" sections)**

### Objective 9 — Config and UI (Agent E)

**New `rules.json` fields to add** (preserve all existing fields):
```json
{
  "auto_disable_negative_expectancy": false,
  "min_trades_before_disable": 30,
  "strategy_priority_order": ["noise_area","gap_and_go","orb_15","vwap_reclaim","hod_break","momentum_15","volume_spike","trend_rider"],
  "atr_stop_multiplier": 1.2,
  "min_stop_pct": 0.005,
  "max_stop_pct": 0.025,
  "partial_exit_levels": [
    {"r_multiple": 0.75, "sell_pct": 0.25, "move_stop_to": "entry_minus_0.25R"},
    {"r_multiple": 1.5,  "sell_pct": 0.25, "move_stop_to": "breakeven"},
    {"r_multiple": 2.5,  "sell_pct": 0.25, "move_stop_to": "chandelier"}
  ],
  "chandelier_atr_multiplier": 3.0
}
```

**`webui.py` Settings additions:**
- Section: "Risk Parameters" — atr_stop_multiplier, min_stop_pct, max_stop_pct
- Section: "Exit Levels" — display partial_exit_levels as read-only table (editable JSON textarea)
- Section: "Strategy Automation" — auto_disable_negative_expectancy checkbox, min_trades_before_disable
- Section: "chandelier_atr_multiplier" — float input

**Preserve all existing settings fields exactly.**

### Objective 10 — Logging (Agent D + C)

**New CSV files:**

`signals.csv` (Agent D creates schema, Agent C writes rows via `log_signal_csv()`):
```
timestamp,symbol,strategy,filter_name,filter_value,threshold,pass_fail
```

`position_updates.csv`:
```
timestamp,symbol,current_price,unrealized_R,stop_level,action_taken
```

`rejected_signals.csv`:
```
timestamp,symbol,winning_strategy,rejected_strategy,winning_expectancy,rejected_expectancy,reason
```

`exits.csv`:
```
timestamp,symbol,strategy,exit_trigger,qty_sold,fill_price,entry_price,r_multiple,pnl,remaining_qty
```

---

## Critical Constraints Reminder

- **DO NOT** remove any existing strategy's filter logic
- **DO NOT** add ML libraries (no scikit-learn, numpy/pandas only)
- **DO NOT** break broker sync logic in `cycle.py`
- **Trend Rider MUST remain capable of firing** — only priority changes when another strategy has higher expectancy
- All new public functions: type hints + one-line docstring
- Test with `python bot.py --symbol SPY --check-only` before declaring done
- `hod_break` is the 4th strategy (was unnamed in brief) — preserve it, add to priority order between `vwap_reclaim` and `momentum_15`

---

## Agent Status

| Agent | Status | Files Changed | Notes |
|-------|--------|---------------|-------|
| A — Data | COMPLETE | `src/data_shim.py` (new), `src/universe.py` (new), `src/ibkr_client.py`, `morning_prefilter.py` | |
| B — Strategy | COMPLETE | `strategy.py`, `src/expectancy.py` (new) | yfinance removed; noise_area added; expectancy-ranked dispatcher; STRATEGY_PRIORITY dict |
| C — Risk/Exits | COMPLETE | `src/risk.py` (new), `cycle.py` | ATR stops, risk-based sizing, layered R exits, Chandelier trail |
| D — Diagnostics | COMPLETE | `nightly_report.py` (new), `run.py`, `src/logging_helpers.py` (new) | |
| E — Config/UI | COMPLETE | `rules.json`, `webui.py`, `templates/settings.html` | New fields: ATR stops, partial exits, chandelier, auto-disable; Settings page sections added |
| F — Verification | COMPLETE | `docs/MIGRATION_NOTES.md` (new) | 9 PASS, 1 PARTIAL (nightly report 1038 chars, sections present but content is stubs), 0 FAIL, 0 BLOCKED |

---

## Agent F Verification Scorecard (2026-07-31)

| # | Objective | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Replace yfinance with IBKR data | PASS | `yfinance` absent from `strategy.py`, `morning_prefilter.py`, `cycle.py`; `data_shim` imports cleanly |
| 2 | Noise Area strategy | PASS | `noise_area` in `strategy.STRATEGIES`; `STRATEGY_PRIORITY['noise_area'] == 0` |
| 3 | Expectancy-ranked dispatcher | PASS | `evaluate_all` returns highest-priority passer; trend_rider wins when sole passer (mock test passed) |
| 4 | ATR-based stops | PASS | `compute_atr` → 2.0000, `compute_stop` clamps within [0.5%, 2.5%], assertions passed |
| 5 | Universe expansion (Russell 2000) | PASS | `get_combined_universe()` returns 680 tickers; morning_prefilter caps at 40 |
| 6 | Risk-based position sizing | PASS | `compute_shares(25000, 250, 115.5, stop)` → qty=104 (risk-dollar based); assertion passed |
| 7 | Layered exit system | PASS | States `initial→scale1→scale2→scale3→runner` present in `cycle.py`; `pre_breakeven` backward-compat retained |
| 8 | Nightly report (8 sections) | PARTIAL | Generates without error, all 8 section headers present, but report is only 1038 chars — sections contain stub/empty-data content. Acceptable for day-0 with no trade history; will fill in live. |
| 9 | Config and UI (rules.json + settings.html) | PASS | All 7 required fields present in `rules.json`; `noise_area` in `active_strategies` |
| 10 | CSV logging (4 files) | PASS | All 4 helpers import and write rows; headers correct; files created on first write |

**Summary: 9 PASS, 1 PARTIAL, 0 FAIL, 0 BLOCKED**

### Notes on PARTIAL — Nightly Report Content

The report generates cleanly and hits all 8 section headers. The 1038-char output is expected
behaviour on a fresh install with no `trade_journal.jsonl`, `trades.csv`, `exits.csv`, or
`signals.csv`. Each section falls through to its "no data yet" branch. Once the bot runs live
trades, sections will populate automatically — no code change needed.
