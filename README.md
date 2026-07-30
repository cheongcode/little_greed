# little_greed

An automated paper-trading bot for Interactive Brokers. It screens all 503 S&P 500 stocks every morning, builds a watchlist of the best gap-up setups, then trades them automatically throughout the day using 6 concurrent strategies. Every decision is logged with full audit trail. Everything is visible live in a web dashboard at `http://localhost:8000`.

Built for: **macOS and Windows**. Requires **Python 3.12+** and an **IBKR paper-trading account**.

---

## Quick Start

### First Time Only
```bash
python install.py
# Creates virtual environment, installs dependencies, tests IBKR connection
```

### Every Trading Day
```bash
./autostart.sh          # macOS/Linux
start.bat               # Windows
```

The dashboard opens automatically at `http://localhost:8000`. To stop: `./stop.sh`

---

## Architecture Overview

The bot runs on a strict schedule:

| Time (ET) | Component | What It Does |
|-----------|-----------|------------|
| **9:25 AM** | `morning_prefilter.py` | Screens 503 S&P 500 + 150 Russell 2000 stocks. Finds gap-ups ≥ 1.5%. Saves top 20 to `watchlist.txt`. Sends Telegram summary. |
| **9:35 AM – 3:45 PM** | `cycle.py` (every 5 min) | Evaluates watchlist against all 6 strategies. Places buys, manages stops, trails positions, logs every decision. |
| **3:51 PM** | `cycle.py` (force-close) | Sells all remaining positions before market close. |
| **4:05 PM** | `compute_perf.py` | Calculates daily P&L, win rate, best/worst trade. Sends end-of-day Telegram. |
| **4:30 PM** | `nightly_report.py` | Generates diagnostic report: rolling expectancy per strategy, filter hit rates, performance trends. |

All timing is **automatic**. You start the bot once; it handles the rest on schedule.

---

## Trading Strategies (6 Total)

The bot evaluates all active strategies every 5 minutes. The core principle: **momentum + volume = continuation**. All strategies exploit the fact that stocks already moving on volume tend to keep moving.

**Entry logic:** When a stock passes a strategy's filters, the bot:
1. Calculates position size based on the stop loss (see [Risk Management](#risk-management))
2. Places a market buy order
3. Immediately places a protective stop-loss order at IBKR
4. Starts monitoring every 5 minutes

**Position management:** 
- At **0.75R profit**: sell ⅓, lock in gains
- At **1R profit**: move stop to entry (risk-free)
- After breakeven: trail stop using 5-minute swing lows
- At **3:51 PM ET**: force-close all remaining positions

---

### 1. Gap and Go
**Window: 9:35–12:00 ET** | **Trigger frequency: Low (1–2 per day)**

**Setup:** Stock gapped up ≥1.5% at open, is above its 50-day MA, and volume is elevated.

**Why:** When a stock gaps up on volume, it signals a catalyst (earnings beat, analyst upgrade, sector news). Short covering + momentum traders drive the first 2-hour push higher.

**Filters:**
- D1: Today's high > yesterday's high (gap held)
- D2: Prior close > 50-day SMA (in uptrend, not broken)
- D3: Open gap ≥ 1.5% from prior close
- I1: Current price > premarket high (momentum continuing)
- I2: Price within 5% of HOD (still near the highs)
- I3: Volume ≥ 1.2× time-normalized average

**Stop:** Low of day × 0.99

---

### 2. Opening Range Breakout (ORB 15)
**Window: 9:45–10:30 ET** | **Trigger frequency: Medium (3–5 per day)**

**Setup:** Price breaks above the first 15-min high on above-average volume.

**Why:** The opening 15 minutes is price discovery. When price breaks the 9:30–9:45 range, it means buyers have absorbed early selling — the breakout has momentum.

**Filters:**
- D1: Price > 50-day SMA (trend context)
- I1: Current price > 15-min range high
- I2: 15-min range width < 2% (tight = more powerful breakout)

**Stop:** Opening range low

---

### 3. VWAP Reclaim
**Window: 10:30–14:00 ET** | **Trigger frequency: Medium (4–6 per day)**

**Setup:** Stock dipped below VWAP, then reclaims it with 1.5× volume surge.

**Why:** VWAP is the benchmark that algorithmic traders use. When institutions step back in at VWAP, it signals accumulation.

**Filters:**
- D1: Price > 50-day SMA (not in downtrend)
- I1: Previous bar closed below VWAP, current bar closes above
- I2: Current bar volume ≥ 1.5× prior bar (institutions stepping in)

**Stop:** Session low

---

### 4. Volume Spike ⚡
**Window: All day (9:35–3:45 PM)** | **Trigger frequency: High (5–10 per day)**

**Setup:** Current 5-min bar has 3×+ typical volume AND price is within 3% of HOD.

**Why:** A sudden 3× volume spike has a catalyst. If it's near the high (not the low), someone large is buying aggressively.

**Filters:**
- D1: Price > $5 (avoids penny stocks)
- I1: Current bar RVOL ≥ 3×
- I2: Price within 3% of HOD
- I3: Current bar is green (close > open)

**Stop:** Low of day × 0.99

---

### 5. Momentum 15 ⚡
**Window: All day (9:35–3:45 PM)** | **Trigger frequency: Very High (8–15 per day)**

**Setup:** Stock moved +1.5% in the last 15 minutes, is above VWAP, volume is 1.5×+.

**Why:** Intraday momentum bursts happen all day. A stock moving 1.5% in 15 min on volume + above VWAP has a real catalyst.

**Filters:**
- D1: Price > $5
- I1: 15-min % change ≥ +1.5%
- I2: Current price > VWAP
- I3: Time-normalized RVOL ≥ 1.5×

**Stop:** Entry × 0.99

---

### 6. Trend Rider ⚡
**Window: All day (continuous)** | **Trigger frequency: Highest (15–25 per day)**

**Setup:** 9-period EMA > 20-period EMA on 5-min bars, price > VWAP, volume ≥ 1.2×.

**Why:** The 9/20 EMA relationship is the most-watched trend indicator. When both conditions hold, momentum is aligned.

**Filters:**
- D1: Price > $5
- D2: Stock didn't gap down (positive bias)
- I1: Price > 9 EMA
- I2: 9 EMA > 20 EMA
- I3: Price > VWAP
- I4: RVOL ≥ 1.2×

**Stop:** Entry × 0.99

---

## Risk Management

Position sizing is **fixed-dollar risk**: every trade risks the same amount regardless of stock price.

```python
risk_dollars = portfolio_value × 2%     # e.g. $500 on a $25k account
R = entry_price - stop_price            # e.g. $1.50 per share
shares = floor(risk_dollars / R)        # e.g. 333 shares
```

**Stop orders:** Placed as real orders in IBKR immediately after every buy. Even if the bot crashes, IBKR will execute the stop if price hits it.

**Position lifecycle:**
1. **Entry**: Buy at market, place protective stop at IBKR
2. **At 0.75R profit**: Sell ⅓ of shares, lock in gains, reduce position
3. **At 1.0R profit**: Move stop to entry price (risk-free from here on)
4. **After breakeven**: Trail the stop using 5-minute swing lows (lock in more gains as price rises)
5. **At 3:51 PM ET**: Force-close all remaining positions before market close

**Daily safeguards:**
- Max 50 trades per day (circuit breaker)
- Max 20 concurrent open positions
- No overnight holds (everything closed by 3:51 PM ET)
- Min position size: 1 share (avoids rounding errors on low-price stocks)
- Min trade price: $5 (avoids penny stocks and micro-floats)

**ATR-based stops (new):** The bot can optionally use Average True Range (14-period) to calculate dynamic stops:
```
raw_gap = ATR × multiplier             # e.g. $1.50 × 1.2 = $1.80
gap = max(raw_gap, entry × 0.5%)      # ensure minimum stop distance
gap = min(gap, entry × 2.5%)           # cap maximum stop distance
stop = entry - gap
```
This adapts to market volatility — choppy stocks get wider stops, smooth stocks get tighter ones. Configure in `/settings` on the dashboard.

---

## Dashboard User Guide

**Access:** Open `http://localhost:8000` in your browser. The dashboard is live — it updates every 2 seconds.

### Dashboard Page (Overview)
Your main control center. Shows:
- **Live positions table**: symbol, entry price, current price, unrealized P&L in dollars and R-multiples, stop level
- **P&L chart**: cumulative daily profit/loss in dollars and R-multiples (updated every 5 min)
- **Trade count**: running total of entries, exits, partial exits
- **Win rate**: % of closed trades that were profitable (updated as trades close)
- **Activity feed**: real-time log of every action — BUY, SELL (partial or full), STOP_HIT, FORCE_CLOSE

**What to monitor:**
- Are your stops showing up as red lines on the P&L chart? Good.
- Is unrealized P&L positive? The strategies are working.
- Is the activity feed constantly updating during market hours? The bot is running.

### Signals Page
Every stock the bot evaluated today, including **why it passed or failed** each strategy's filters.

**Columns:**
- `timestamp`: when the stock was evaluated
- `symbol`: the ticker
- `outcome`: ENTERED, REJECTED, or PARTIAL (if some filters passed but not all)
- `strategy`: which strategy tried to buy it
- `filters`: JSON object showing each filter (D1, D2, I1, etc.) as `true` or `false`
- `reject_reason`: human-readable why it didn't pass (e.g., "I1: price 150.32 below 9EMA 151.00")
- `size_planned`: how many shares would have been bought if it passed

**How to use it:**
- Scroll through to see which stocks were close to triggering (many filters `true`, just 1–2 `false`)
- Identify patterns: "this stock almost worked 5 times" suggests a parameter is off by a little
- If you see a stock that DID print a big move but was REJECTED, note the filter that blocked it — consider tuning that filter

### Settings Page
Configure everything **without editing code or JSON files**.

**Editable fields:**
- `risk_percent`: % of portfolio to risk per trade (default 2%)
- `portfolio_value`: your IBKR paper account size (used for position sizing)
- `max_positions`: max concurrent open positions (default 20)
- `max_trades_per_day`: circuit breaker (default 50)
- Active/inactive toggle for each strategy (enable only the strategies you want to test)
- Time windows for each strategy (e.g., gap_and_go only runs 9:35–12:00)
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` for notifications
- `use_atr_stops`: toggle between fixed % stops and ATR-based dynamic stops

**To apply changes:** Click "Save Settings". Settings are written to `rules.json` and take effect on the next 5-min cycle.

### Signals Details Page
When you click on a signal in the Signals page, you see the full filter breakdown:

```
Symbol: AAPL
Strategy: trend_rider
Outcome: ENTERED

Daily Checks (D filters):
  ✓ D1: price (165.32) > $5
  ✓ D2: gap_pct (2.3%) >= 0 (didn't gap down)
  
Intraday Checks (I filters):
  ✓ I1: price (165.32) > 9EMA (164.18)
  ✓ I2: 9EMA (164.18) > 20EMA (163.50)
  ✓ I3: price (165.32) > VWAP (164.80)
  ✓ I4: RVOL (1.8x) >= 1.2x

Position Sizing:
  Entry: $165.32
  Stop: $163.50 (0.99x entry)
  Risk per share: $1.82
  Risk dollars: $250 (portfolio $25,000 × 2%)
  Shares: 137

Status: BUY order placed for 137 shares
```

### Preflight Page
Run this **before market opens** to verify everything is working:

- Python version ≥ 3.12
- IBKR TWS connection OK
- Disk space > 5 GB
- All required files exist (rules.json, .env, etc.)
- Telegram credentials valid (if configured)
- Market hours are correct (ET timezone)

Green checkmarks = ready to trade. Red X = fix the issue before starting.

### Kill Switch Page
Emergency flatten all positions. Requires:
1. Type "FLATTEN" in the text box
2. Click the red button

This forces a market sell of every open position immediately. Use if:
- You spot an issue and want to stop all trading
- The market is acting weird and you want to exit cleanly
- You want to liquidate for manual inspection

---

## Logs and Monitoring

All activity is logged to `logs/`:

**`autostart_YYYYMMDD.log`** (text)
- Startup messages, scheduled job runs, errors
- Check this if the bot doesn't start

**`trade_journal.jsonl`** (JSON lines)
- One JSON object per event: entry, exit, partial, stop hit, error
- Each event has `ts`, `symbol`, `strategy`, `pnl`, `order_id`
- Most detailed log — read this to understand individual trades

**`signals.jsonl`** (JSON lines)
- One JSON object per stock evaluation
- Shows `symbol`, `outcome`, `strategy`, `filters` object, `reject_reason`
- Use to debug why a stock didn't trigger

**`cycle_errors.log`** (text)
- Errors that happened during a 5-minute cycle
- Stack traces, API connection issues, missing data
- If the bot is crashing, check here

**`safety_log.jsonl`** (JSON lines)
- Safety checks: circuit breaker hits, max positions exceeded, force-close events
- One line per safety event with timestamp and reason

**Reading logs in real-time:**
```bash
# Follow the trade journal
tail -f logs/trade_journal.jsonl | jq '.'

# Follow errors
tail -f logs/cycle_errors.log

# Count today's trades
grep "$(date +%Y-%m-%d)" logs/trade_journal.jsonl | grep '"event":"entry"' | wc -l
```

---

## New Features (Latest Updates)

### Nightly Reports (`nightly_report.py`)
Runs at 4:30 PM ET to generate end-of-day diagnostics:

**Rolling expectancy by strategy:**
```
Trend Rider: +0.23R (good)
Gap and Go: +0.05R (marginal)
Momentum 15: -0.10R (losing)
Volume Spike: +0.15R (okay)
```

Reads the last 20 closed trades per strategy and calculates:
- Win rate: % of trades that were profitable
- Avg win: average R-multiple on winning trades
- Avg loss: average R-multiple on losing trades
- **Expectancy**: (win_rate × avg_win) - (loss_rate × avg_loss)

**Positive expectancy** = the strategy is profitable long-term. **Negative** = consider disabling it.

### ATR-Based Stops (`src/risk.py`)
Optional dynamic stop calculation based on Average True Range:

Instead of `entry × 0.99`, the bot can calculate:
```
ATR(14) = 2.50
raw_gap = 2.50 × 1.2 = 3.00
gap = clamp(3.00, entry×0.5%, entry×2.5%)
stop = entry - gap
```

This adapts to volatility. Enable in `/settings` → `use_atr_stops`.

### Expectancy Calculator (`src/expectancy.py`)
Real-time rolling expectancy for each strategy. Used by the nightly report and optionally by `cycle.py` to **reject trades from losing strategies**.

```python
from src.expectancy import rolling_expectancy

exp = rolling_expectancy("trend_rider", n=20)  # last 20 closed trades
if exp < 0:
    print("Trend Rider is currently unprofitable — skip its entries")
```

### Data Abstraction Layer (`src/data_shim.py`)
Encapsulates all IBKR API calls. Benefits:
- Easy to swap IBKR for another broker later
- Consistent error handling
- Clear contract/data conversion logic

### Logging Helpers (`src/logging_helpers.py`)
CSV logging for signals, positions, exits, and rejected signals. Enables:
- Analysis in Excel/Google Sheets
- Filtering by symbol, strategy, time window
- Audit trail for compliance

---

## Project File Structure

```
little_greed/
│
├── 🚀 START HERE
├── autostart.sh          ← Start trading (run this every day)
├── stop.sh               ← Stop cleanly
├── install.py            ← First-time setup
│
├── 📅 SCHEDULER & MAIN LOOP
├── run.py                ← Scheduler — runs jobs at exact times
├── cycle.py              ← Runs every 5 min: evaluate + enter + manage
│
├── 📊 STRATEGY LAYER
├── morning_prefilter.py  ← 9:25 AM: Screen 500+ stocks, build watchlist
├── strategy.py           ← All 6 strategy definitions + evaluator
├── trade.py              ← Places a single market buy order
├── bot.py                ← Manual testing: python bot.py --symbol AAPL
│
├── 📈 ANALYSIS & REPORTING
├── compute_perf.py       ← End-of-day P&L and win rate calculation
├── nightly_report.py     ← 4:30 PM: Rolling expectancy by strategy
│
├── 🌐 WEB DASHBOARD
├── webui.py              ← FastAPI server (port 8000)
├── templates/
│   ├── base.html         ← Sidebar navigation
│   ├── dashboard.html    ← Live P&L, positions, activity feed
│   ├── signals.html      ← Every stock evaluated today
│   ├── settings.html     ← Edit all configuration
│   ├── preflight.html    ← Health checks before market open
│   └── kill.html         ← Emergency flatten all positions
│
├── 📚 DATA & CONFIG
├── rules.json            ← Strategy parameters (editable from /settings)
├── .env                  ← IBKR credentials, Telegram keys (NEVER share)
├── watchlist.txt         ← Today's gap-up candidates (auto-generated 9:25 AM)
├── open_positions.json   ← Current open positions + stops (auto-updated)
│
├── 💾 RUNTIME DATA
├── trades.csv            ← Every fill: entry/exit timestamp, price, qty
├── logs/
│   ├── autostart_*.log   ← Startup and scheduled jobs
│   ├── trade_journal.jsonl   ← Entry/exit/error events (most important)
│   ├── signals.jsonl     ← Every signal with filter details
│   ├── cycle_errors.log  ← Errors during 5-min cycles
│   └── safety_log.jsonl  ← Safety checks, circuit breaker hits
│
├── 🔧 INFRASTRUCTURE
├── src/
│   ├── ibkr_client.py    ← Connects to IBKR (ib_async wrapper)
│   ├── notify.py         ← Telegram/ntfy notifications
│   ├── sp500_tickers.py  ← All 503 S&P 500 symbols
│   ├── universe.py       ← Russell 2000 liquid stocks + S&P 500
│   ├── risk.py           ← ATR stops, position sizing
│   ├── expectancy.py     ← Rolling expectancy from trade_journal
│   ├── logging_helpers.py ← CSV signal/position/exit logging
│   └── data_shim.py      ← IBKR data abstraction layer
│
└── 📜 DOCUMENTATION
    ├── README.md         ← This file
    ├── README_INSTALL.md ← TWS setup with API configuration
    └── CODE_GUIDE.md     ← Technical walkthrough of all files
```

---

## Telegram Notifications

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `/settings` → "Telegram" section.

The bot sends:
- **BUY**: symbol, strategy, entry price, stop, quantity
- **STOP HIT**: exit price, P&L, R-multiple
- **PARTIAL**: "Sold ⅓ at 0.75R profit"
- **BREAKEVEN**: "Stop moved to entry — risk-free from here"
- **FORCE CLOSE**: "Market closed, flattened X positions"
- **EOD SUMMARY**: total trades, win rate, P&L, best/worst trade
- **ERRORS**: full stack trace so you can debug offline

**Get a Telegram bot:**
1. Message `@BotFather` on Telegram
2. Type `/newbot`
3. Name it (e.g., "little_greed")
4. Copy the API token → paste in Settings

**Get your chat ID:**
1. Message `@userinfobot`
2. Your user ID appears immediately
3. Paste in Settings

---

## Stopping and Restarting

**It's completely safe to stop and restart at any time.**

When you restart, the bot:
1. Reads your **actual IBKR positions** (broker sync)
2. Compares them against `open_positions.json`
3. Fixes any discrepancies (e.g., a partial was filled while bot was offline)
4. Resumes monitoring from the correct state

**Recommended routine:**
- **9:20 AM**: Start TWS, log in
- **9:21 AM**: Run `./autostart.sh`
- **3:51 PM**: Bot auto-closes all positions
- **After 4:00 PM**: Run `./stop.sh` (or leave running — it's idle outside market hours)

**If TWS crashes during the day:**
1. Restart TWS
2. The bot continues running — it will rebuild its IBKR connection on the next cycle
3. No positions are lost; stops remain active at IBKR

---

## Troubleshooting

### Bot won't start
- Check `/preflight` page for errors
- Verify TWS is running and logged in
- Check `logs/autostart_*.log` for the error message

### No trades executed
- Is the current time within a strategy's window? (e.g., Gap and Go only runs 9:35–12:00)
- Check `/signals` page — are stocks being evaluated?
- Are all 6 strategies disabled? Check `/settings` → ensure at least one is enabled
- Is the watchlist empty? Check `watchlist.txt` — if empty, no stocks will be evaluated

### Positions not showing
- Check IBKR TWS: are they really open?
- Check `/dashboard` → are they listed in the live positions table?
- If missing from dashboard but open in TWS, restart the bot (broker sync will fix it)

### Stops aren't working
- IBKR must have the stop order. Check TWS Order Status window.
- If a stop order is missing, the bot will re-place it on the next 5-min cycle
- If a stop is consistently not placed, check `logs/cycle_errors.log` for the error

### Telegram not working
- Check API token and chat ID are correct in `/settings`
- Try `/preflight` → it tests the Telegram connection
- If preflight says Telegram is down, verify your token and chat ID

### High losing rate
- Run 30+ trading days before judging a strategy
- Check the nightly report for rolling expectancy
- If expectancy is negative, consider disabling that strategy in `/settings`
- Review `/signals` page for patterns in what failed

---

## What's Next: From Paper Trading to Real

**This is paper trading.** Fills are at real market prices, but no real money is at risk.

To eventually go live:

1. **Run 30+ days of paper trading** with correct parameters
   - Verify win rate is > 50%
   - Verify P&L is positive
   - Review trade journal for patterns

2. **Backtest on historical data**
   - Use backtesting library (e.g., Backtrader, VectorBT)
   - Test the same parameter set on 12 months of data
   - Ensure backtest results match live results

3. **Gradual scale-up**
   - Start live with 10% of real account size
   - Trade for 5 days, verify again
   - Scale to 50%, then 100%

4. **Production checklist**
   - Enable exception handling for all API calls
   - Add circuit breaker on consecutive losses
   - Set up monitoring/alerting (Telegram, logging, health checks)
   - Document your risk tolerance in writing

---

## Setup from Scratch

See **[README_INSTALL.md](README_INSTALL.md)** for step-by-step TWS setup with API configuration.

See **[CODE_GUIDE.md](CODE_GUIDE.md)** for a full technical walkthrough of every file, the trade execution flow, and how to extend it.
