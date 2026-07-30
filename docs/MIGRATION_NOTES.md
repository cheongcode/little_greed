# Migration Notes — little_greed Refactor (2026-07-31)

This document covers what you need to know to operate the bot after the 10-objective refactor.
It assumes you were running the prior version and need to understand what changed operationally.

---

## 1. IBKR Market Data Subscription Requirement

`src/data_shim.py` fetches bars exclusively from IBKR using `reqHistoricalData` with
`whatToShow='TRADES'`. This requires an active IBKR market data subscription.

- Primary: real-time TRADES data (any US equities bundle)
- Fallback: `whatToShow='DELAYED_FROZEN_LAST'` — automatically tried if real-time returns empty

**Action required:** Ensure your IBKR account has at least the "US Securities Snapshot and Futures
Value Bundle" (or equivalent) activated under Account Management > Market Data Subscriptions.
Without it, `get_bars()` will fall back to delayed data and may return empty DataFrames for
instruments outside the delayed feed.

---

## 2. New IBKR Client IDs

The morning prefilter now uses a dedicated IBKR client ID to avoid conflicts with the main bot
connection.

| Component | Client ID | Env var override |
|-----------|-----------|-----------------|
| `morning_prefilter.py` | **3** (default) | `IBKR_CLIENT_ID_PREFILTER` |
| Main bot (`run.py` / `cycle.py`) | Whatever you configured previously | unchanged |

**Action required:** If you already use client ID 3 for another TWS/Gateway connection, set
`IBKR_CLIENT_ID_PREFILTER` in your `.env` to a free ID (e.g. `4`).

---

## 3. New rules.json Fields and Defaults

The following fields were added. Existing fields are unchanged. If you have a customised
`rules.json`, merge these in — the bot will fail with a `KeyError` if they are absent
(no graceful fallback for the risk fields).

| Field | Default | Description |
|-------|---------|-------------|
| `atr_stop_multiplier` | `1.2` | ATR × multiplier = raw stop gap |
| `min_stop_pct` | `0.005` | Floor: stop gap never less than 0.5% of entry |
| `max_stop_pct` | `0.025` | Ceiling: stop gap never more than 2.5% of entry |
| `partial_exit_levels` | 3-level array | R multiples, sell %, and stop relocation for each layer |
| `chandelier_atr_multiplier` | `3.0` | Highest-high minus N×ATR for the trailing runner stop |
| `auto_disable_negative_expectancy` | `false` | Auto-disable strategy if rolling expectancy goes negative |
| `min_trades_before_disable` | `30` | Minimum trade count before auto-disable can trigger |
| `strategy_priority_order` | `[noise_area, ...]` | Tie-break order when strategies have equal expectancy |

The `partial_exit_levels` default:
```json
[
  {"r_multiple": 0.75, "sell_pct": 0.25, "move_stop_to": "entry_minus_0.25R"},
  {"r_multiple": 1.5,  "sell_pct": 0.25, "move_stop_to": "breakeven"},
  {"r_multiple": 2.5,  "sell_pct": 0.25, "move_stop_to": "chandelier"}
]
```

---

## 4. New CSV Log Files

Four new CSV files are written to the project root (same directory as `trades.csv`):

| File | Written by | Content |
|------|-----------|---------|
| `signals.csv` | `cycle.py` via `log_signal_csv()` | One row per filter per strategy evaluation per bar |
| `position_updates.csv` | `cycle.py` via `log_position_update_csv()` | One row per `manage_position()` call |
| `exits.csv` | `cycle.py` via `log_exit_csv()` | Every partial and full exit with R-multiple and P&L |
| `rejected_signals.csv` | `strategy.py` via `log_rejected_signal_csv()` | Dispatcher losers — strategies that passed filters but lost to a higher-expectancy strategy |

Files are created on first write (append mode, header written if file does not exist). No
rotation is implemented — archive or truncate manually between sessions.

---

## 5. New reports/ Directory

`nightly_report.py` writes daily markdown reports to `reports/YYYY-MM-DD.md`. This directory is
created automatically on first run.

**Action required:** Add `reports/` to `.gitignore` if you do not want report files tracked.

---

## 6. Telegram Still Required for Nightly Report Summary

`nightly_report.main()` (called by the scheduler at 16:30 ET) sends the first 1500 characters of
the report to Telegram. This uses the same `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` env vars the
main bot already uses. The report is still written to `reports/` even if Telegram delivery fails —
failure is logged to stderr and does not raise.

**No new Telegram configuration needed.** If those env vars are not set, the send is silently
skipped.

---

## 7. Russell 2000 Expansion — Watchlist Now Caps at 40

`morning_prefilter.py` now builds a combined universe of S&P 500 + ~150 liquid Russell 2000
names (defined in `src/universe.py`). After gap and liquidity filtering ($10M 20-day average
dollar volume, price > $5), the watchlist is capped at **40 symbols** sorted by gap percentage
descending.

Previous cap was 20. If your downstream tooling or position limits assume ≤20 watchlist entries,
update accordingly. The `max_concurrent_positions` field in `rules.json` (currently `20`) remains
the live position limit — the watchlist cap only affects how many candidates the morning scan
returns.

---

## 8. Position State Machine Changed

The position `state` field in `open_positions.json` now uses a 5-value state machine.

| New state | Old equivalent | Meaning |
|-----------|---------------|---------|
| `initial` | `open` / `pre_breakeven` | Full position, ATR stop active |
| `scale1` | (none) | 25% sold at +0.75R; stop moved to entry − 0.25R |
| `scale2` | (none) | 25% more sold at +1.5R; stop at breakeven |
| `scale3` | (none) | 25% more sold at +2.5R; Chandelier trail started |
| `runner` | (none) | 25% remaining, Chandelier trail until stop or 15:51 |

**Compatibility:** The `pre_breakeven` state is still accepted by `cycle.py` in the `initial`
branch (`if state == "initial" or state == "pre_breakeven":`), so any positions that were open
before the upgrade and persisted to `open_positions.json` with the old state string will continue
to be managed. They will transition to `scale1` at the first +0.75R trigger.

---

## 9. trades.csv Format Expanded

The `trades.csv` header was extended with risk/sizing columns. Old rows (missing these columns)
are compatible — missing fields will simply be blank when read with `pandas.read_csv()`.

New columns appended to the right of the existing schema:
```
portfolio_value, risk_dollars, entry_price, stop_price,
R_per_share, computed_shares, actual_fill_shares, slippage_pct
```

No migration of old rows is needed.

---

## 10. yfinance Status

`yfinance` is no longer used in any hot path (strategy evaluation, bar fetching, cycle
management, morning prefilter). It remains as a fallback in `webui.py` for the dashboard price
display (`/api/portfolio` endpoint tries IBKR first, then falls back to yfinance).

**If you want to remove it entirely:**
1. Remove `yfinance` from `requirements.txt`.
2. In `webui.py`, remove the `import yfinance as yf` block inside `_get_prices_yfinance()`
   (around line 119–124) and ensure the IBKR price path is the only code path.
3. The health-check on line 365 also lists `yfinance` as a checked package — remove that entry.

Until you do this, `yfinance` stays installed but is only called when the IBKR price fetch fails
during dashboard rendering. It has no effect on trade execution.
