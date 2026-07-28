# little_greed — Code Guide

Complete walkthrough of every file, how they connect, and what happens when the bot trades.

---

## Big Picture: What This Bot Does

1. Every morning at 9:25 AM ET it screens all 503 S&P 500 stocks for gap-up setups
2. During market hours (10:05–15:30 ET) it scans the resulting watchlist every 5 minutes
3. When a stock passes all strategy filters it places a market buy order via IBKR
4. It manages open positions: trails stops, takes partials, enforces breakeven
5. At 15:51 ET it force-closes everything before the close
6. At 16:05 ET it sends a daily performance summary to Telegram

**Does it actually place real trades?** Yes — it connects to your IBKR paper account and submits MarketOrder objects through the TWS API. Every order in trades.csv was a real paper fill at the real market price.

---

## File Map

```
little_greed/
├── run.py                   ← single entry point, starts everything
├── cycle.py                 ← main trading loop, runs every 5 min
├── strategy.py              ← all 4 strategy implementations
├── morning_prefilter.py     ← S&P 500 gap screener, builds watchlist.txt
├── bot.py                   ← CLI tool for manual single-symbol testing
├── trade.py                 ← subprocess that actually places 1 order
├── compute_perf.py          ← end-of-day P&L summary
├── rotate_logs.py           ← log archival and trades.csv pruning
├── webui.py                 ← FastAPI web dashboard (port 8000)
├── rules.json               ← strategy parameters (editable from Settings)
├── .env                     ← secrets and env config (never commit)
├── watchlist.txt            ← today's gap-up candidates
├── trades.csv               ← every fill: timestamp, symbol, price, qty
├── open_positions.json      ← current open positions and stop levels
├── safety-check-log.json    ← every decision the bot made (JSONL)
├── logs/
│   ├── runner.log           ← scheduler start/stop/errors
│   ├── signals.jsonl        ← per-ticker strategy evaluation (every cycle)
│   ├── safety_log.jsonl     ← strategy filter results
│   └── notify_errors.log    ← Telegram send failures
├── src/
│   ├── ibkr_client.py       ← thin wrapper around ib_async IB object
│   ├── notify.py            ← fire-and-forget Telegram + ntfy alerts
│   └── sp500_tickers.py     ← 503 S&P 500 symbols (IBKR format)
└── templates/               ← Jinja2 HTML templates for the web UI
```

---

## How a Trade Happens (End to End)

```
09:25 ET
  morning_prefilter.py
    → downloads 2d/1d bars for all 503 S&P 500 tickers via yfinance
    → applies gap filter (>3% from prior close) + price filter (>$3)
    → sorts by gap % descending, caps at top 20
    → writes watchlist.txt
    → sends Telegram summary

10:05 ET (first cycle after entry window opens)
  cycle.py (via run.py APScheduler, every 5 min)
    step 1: time_gate() → returns "ok"
    step 2: load open_positions.json
    step 3: broker_sync() → reconcile with IBKR actual positions
    step 4: check_stopouts() → match fills to stop order IDs
    step 5: manage_position() → move stops, take partials
    step 6: save_positions()
    step 7: skip force_close (not time yet)
    step 8: skip manage_only (not time yet)
    step 9: ENTRY SCAN
      → count today's BUYs in trades.csv (stop if >= MAX_TRADES_PER_DAY)
      → read watchlist.txt
      → call ib.positions() to get held symbols
      → for each watchlist ticker not already held:
          strategy.evaluate(symbol, ib)
            → runs D1/D2/D3 daily filters via yfinance
            → runs I1/I2/I3 intraday filters via yfinance
            → logs result to logs/signals.jsonl
          if pass:
            → size position: min(MAX_TRADE_SIZE_USD, portfolio*10%) / price
            → subprocess: python trade.py --symbol X --side BUY --size N
              → trade.py connects with IBKR_EXEC_CLIENT_ID (separate connection)
              → qualifies contract, places MarketOrder(outsideRth=True)
              → waits up to 10s for fill
              → appends row to trades.csv
              → disconnects
            → appends position dict to open_positions.json
            → sends Telegram BUY alert

15:51 ET
  cycle.py time_gate() → "force_close"
    → cancels every stop order
    → market-sells every position
    → clears open_positions.json
    → sends Telegram "EOD Force Close" alert

16:05 ET
  compute_perf.py
    → reads today's trades.csv rows
    → FIFO-pairs BUY+SELL
    → calculates wins/losses/P&L/profit factor
    → sends Telegram daily summary
```

---

## Strategy System

The active strategy is set by `strategy_name_key` in `rules.json`. You can change it from the Settings page without touching code.

### Strategy 1 — Gap and Go (default)

**Idea:** Buy stocks that gapped up significantly at the open and are continuing to make new highs with unusual volume.

| Filter | What it checks | Threshold |
|--------|---------------|-----------|
| D1 | Today's close > prior day high | must be above |
| D2 | Prior close > 200-day SMA | trend confirmation |
| D3 | Gap % from prior close | ≥ 3.0% (configurable) |
| I1 | Current price > premarket high | momentum continuing |
| I2 | Current price ≥ 99.5% of today's high | near HOD |
| I3 | Today's volume / 14-day avg volume | ≥ 2.0x (configurable) |

**Stop:** Low of day × 0.99  
**Trail:** 5-min swing lows after breakeven  
**Partial:** Sell ⅓ at 0.75R, move stop to entry  
**Works best:** 10:05–11:30 ET on strong gap days  

### Strategy 2 — Opening Range Breakout (ORB 15)

**Idea:** The first 15 minutes after open set the day's range. A breakout above that range with volume is a high-probability momentum continuation.

| Filter | What it checks |
|--------|---------------|
| D1 | Above 200-day SMA |
| I1 | Current price > 15-min opening range high |
| I2 | Opening range is tight (< 2% wide) |

**Stop:** Opening range low  
**Works best:** 9:45–10:30 ET  

### Strategy 3 — VWAP Reclaim

**Idea:** Stocks that dip below the Volume-Weighted Average Price then reclaim it often see aggressive buying as algorithmic orders trigger.

| Filter | What it checks |
|--------|---------------|
| D1 | Above 200-day SMA |
| I1 | Previous bar was below VWAP, current bar is above VWAP |
| I2 | Volume on reclaim bar > 1.5× the prior bar |

**Stop:** Session low  
**Works best:** Any time intraday, strongest 10:30–14:00 ET  

### Strategy 4 — High of Day (HOD) Break

**Idea:** When a stock makes a new high of the day with significantly above-average volume, institutions are accumulating and it's likely to continue.

| Filter | What it checks |
|--------|---------------|
| D1 | Today's price > prior day high |
| D2 | Above 200-day SMA |
| I1 | Current bar is making new HOD |
| I2 | Volume on breakout bar > 2× average |

**Stop:** Prior swing low  
**Works best:** Any time, especially 11:00–14:00 ET  

---

## Key Files Explained

### `run.py` — The Entry Point

```
python run.py
```

- Checks the `.env` paper/live port safety guard first
- Starts APScheduler (BlockingScheduler, America/New_York timezone)
- Registers 4 jobs: prefilter (cron), cycle (interval 5min), dashboard (cron), rotate (cron)
- Starts uvicorn in a background thread (web dashboard)
- Traps SIGINT/SIGTERM for clean shutdown
- All exceptions in jobs are caught, logged, and sent to Telegram — scheduler never crashes

### `cycle.py` — The Trading Brain

The most important file. Runs every 5 minutes. Contains:
- `time_gate()` — returns one of 6 states, exits immediately on weekend/closed/too_early
- `broker_sync()` — **critical**: reconciles local JSON state with IBKR truth on every run
- `check_stopouts()` — matches fills by order ID (NOT quantity — avoids partial fill false triggers)
- `manage_position()` — implements the 3-state position management (pre_breakeven → post_breakeven)
- `_log_signal()` — logs every ticker evaluation to signals.jsonl for the dashboard

### `strategy.py` — All 4 Strategies

Each strategy is a function `_strategy_name(symbol, ib) -> dict`. The dict always contains:
- `pass` (bool) — whether to enter
- `reasons` (list) — why it passed or failed  
- `price` (float) — current price
- `filters` (dict) — each filter's pass/fail
- `values` (dict) — the actual numbers (gap%, rvol, sma200, etc.)

The `evaluate(symbol, ib)` dispatcher reads `strategy_name_key` from `rules.json` and calls the right function. Adding a new strategy = add a function + register it in the `STRATEGIES` dict.

### `trade.py` — Order Execution

Spawned as a **subprocess** by cycle.py and bot.py. Uses `IBKR_EXEC_CLIENT_ID` (separate from cycle.py's client ID) to avoid TWS connection conflicts. Always disconnects in a `finally` block.

### `src/ibkr_client.py` — IBKR Connection

Thin wrapper. `IBKRClient(host, port, client_id)` calls `ib.connect()`. `.place_order()` qualifies the contract, places a MarketOrder with `outsideRth=True`, and polls for 10 seconds for the status to change from PendingSubmit.

### `src/notify.py` — Alerts

Fires Telegram and/or ntfy (if configured). Every call is wrapped in try/except — a failed notification never stops a trade. Both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` must be set in `.env` for Telegram to work.

### `webui.py` — Dashboard

FastAPI app bound to `127.0.0.1:8000` (localhost only — never 0.0.0.0). Key routes:
- `GET /dashboard` — main page with positions, P&L chart, trade history
- `GET /dashboard/data` — HTMX partial refresh (positions table, every 5s)
- `GET /dashboard/strategy-panel` — live filter evaluation panel (every 30s)
- `GET /dashboard/chart-data` — Chart.js JSON data
- `POST /kill` — cancels all orders + market-sells all positions
- `GET/POST /settings` — reads/writes `.env` and `rules.json`
- `POST /preflight/run` — runs 11 health checks

### `rules.json` — Strategy Config

Human-readable. Editable from the Settings page. Key fields:
```json
{
  "strategy_name_key": "gap_and_go",   ← which strategy to run
  "daily_filters": {
    "D3_min_gap_pct_from_prior_close": 3.0
  },
  "intraday_filters": {
    "I3_rvol_min": 2.0
  },
  "time_filter": {
    "earliest_entry_et": "10:05",
    "latest_entry_et": "15:30",
    "force_close_et": "15:51"
  },
  "risk": {
    "max_risk_per_trade_pct": 1.0,
    "max_concurrent_positions": 5
  }
}
```

---

## Risk Management

| Setting | Default | Effect |
|---------|---------|--------|
| `MAX_RISK_PER_TRADE_PCT` | 1.0% | Dollar risk = portfolio × 1% / R |
| `MAX_TRADE_SIZE_USD` | $2,500 | Hard cap per position |
| `max_position_size_pct_of_portfolio` | 10% | Also caps position size |
| `MAX_TRADES_PER_DAY` | 5 | Bot stops entering after 5 BUYs |
| `max_concurrent_positions` | 5 | No new entries if 5 open |
| Force close at 15:51 ET | hardcoded | Everything gets sold before close |

**Position sizing formula:**
```
risk_dollars = PORTFOLIO_VALUE_USD × (MAX_RISK_PER_TRADE_PCT / 100)
R = entry_price - initial_stop
size = min(floor(risk_dollars / R), floor(PORTFOLIO_VALUE_USD × 0.10 / price))
```

---

## State Files

| File | Purpose | Lost if deleted? |
|------|---------|-----------------|
| `open_positions.json` | Tracks open positions | Positions re-added by broker_sync() on next cycle |
| `trades.csv` | Full trade history | Permanent loss — back up regularly |
| `watchlist.txt` | Today's scan candidates | Rebuilt by prefilter at 9:25 ET |
| `safety-check-log.json` | Decision audit trail | Rotated to logs/archive/ after market close |
| `logs/signals.jsonl` | Filter evaluations | Rotated daily |

---

## Common Questions

**Q: The bot connected but no trades placed — why?**  
→ Check the Signals page. It shows exactly why each ticker was rejected (e.g., gap too small, outside time window, below SMA200).

**Q: Position shows in IBKR but not in the dashboard?**  
→ `broker_sync()` in cycle.py adds missing positions from IBKR on the next cycle run. Or manually sync by running `python bot.py --symbol X --check-only`.

**Q: How do I add a new strategy?**  
1. Add a function `_my_strategy(symbol, ib) -> dict` in `strategy.py`
2. Add it to the `STRATEGIES` dict
3. Add a description in `STRATEGY_DESCRIPTIONS`
4. It will appear in the Settings → Active Strategy selector automatically

**Q: Can I run this live (not paper)?**  
→ Set `PAPER_TRADING=false` and `IBKR_PORT=7496` in `.env`. The bot has a guard that refuses to start if paper flag and live port don't match. **Test thoroughly on paper first.**

**Q: What happens if the bot crashes mid-trade?**  
→ The order may have gone through at IBKR even if the bot crashed. On restart, `broker_sync()` reads IBKR positions and adds any missing ones to `open_positions.json`. The bot resumes managing them on the next cycle.
