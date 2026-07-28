# little_greed

An automated paper-trading bot for Interactive Brokers. It screens all 503 S&P 500 stocks every morning, builds a watchlist of the best gap-up setups, then trades them automatically throughout the day using 4 strategies running concurrently. Every decision is logged. Everything is visible in a live web dashboard at `http://localhost:8000`.

Built for: macOS and Windows. Requires Python 3.12+ and an IBKR paper account.

---

## How to Start

```bash
# First time only — creates virtual env, installs packages, tests IBKR connection
python install.py

# Every day after that
./autostart.sh          # macOS
start.bat               # Windows

# To stop
./stop.sh
```

That's it. The browser opens automatically.

---

## What It Does (Plain English)

Every trading day, the bot runs on a fixed schedule without you doing anything:

| Time (ET) | What happens |
|-----------|-------------|
| 9:25 AM | Downloads 2 days of price data for all 503 S&P 500 stocks. Finds stocks that gapped up significantly from yesterday's close. Saves the top 20 to `watchlist.txt`. Sends a Telegram summary. |
| 9:35 AM | Entry window opens. Every 5 minutes, the bot scans the watchlist against all 4 active strategies. |
| 9:35–15:45 | When a stock passes a strategy's filters, the bot places a market buy order, immediately places a protective stop-loss order in IBKR, and starts managing the position. |
| Throughout | Positions are monitored every 5 minutes: stops trail up, partials are taken at 0.75R, breakeven triggers at 1R. |
| 15:51 PM | Force-close: everything is sold before the market closes. |
| 16:05 PM | End-of-day P&L summary sent via Telegram. |

---

## The Strategies

The bot runs up to 4 strategies at the same time. Every 5 minutes, each watchlist stock is evaluated against all active strategies. The first strategy that passes = immediate buy order.

### Why these strategies?

All 4 strategies are based on one core idea: **stocks that are already moving with volume are more likely to keep moving than stocks that aren't**. This is called momentum trading. The strategies differ in how they define "already moving" and at what point in the day they look for it.

---

### 1. Gap and Go
**Best time: 9:35–12:00 ET**

**What it looks for:** A stock that opened at least 1.5% above yesterday's close, is above its 50-day moving average, and has at least 1.2× its normal volume for the time of day.

**Why it works:** When a stock gaps up significantly at the open on high volume, it usually means there's a catalyst (earnings beat, analyst upgrade, sector news). Traders chasing the move and short sellers covering their positions both push the price higher. The first 2 hours after open are when this momentum is strongest.

**Filters:**
- D1: Today's high is above yesterday's high (confirms the gap held)
- D2: Prior close above 50-day SMA (in an uptrend — not a broken stock)
- D3: Opening gap ≥ 1.5% from yesterday's close
- I1: Current price above premarket high (momentum continuing after open)
- I2: Price within 5% of today's high (still near the highs, not fading hard)
- I3: Volume ≥ 1.2× the same time period on prior days (time-normalized)

**Stop:** Low of day × 0.99 (just below today's lows)

---

### 2. Opening Range Breakout (ORB 15)
**Best time: 9:45–10:30 ET**

**What it looks for:** The first 15 minutes after open define a price range (the opening range). A breakout above that range with above-average volume is a high-probability continuation move.

**Why it works:** The opening range is where price discovery happens. Institutions place their large orders in the first 15 minutes. When price breaks above that range, it signals that buyers have absorbed the early selling pressure and demand is winning. The tighter the opening range, the more powerful the breakout when it comes.

**Filters:**
- D1: Above 50-day SMA (trend confirmation)
- I1: Current price above the 15-min high (actual breakout)
- I2: Opening range width < 2% (tight range = more compressed energy)

**Stop:** Opening range low

---

### 3. VWAP Reclaim
**Best time: 10:30–14:00 ET**

**What it looks for:** VWAP (Volume Weighted Average Price) is the average price of all trades weighted by volume. It's the reference line that institutional algorithms use to judge whether they're buying cheap or expensive. When a stock dips below VWAP and then reclaims it with a surge in volume, institutions are stepping back in.

**Why it works:** Algorithmic trading systems at hedge funds and market makers use VWAP as a benchmark. Many of their orders are programmed to buy when price is below VWAP and sell when it's above. A VWAP reclaim with volume is a signal that the institutional buy programs are activating, which tends to push price higher.

**Filters:**
- D1: Above 50-day SMA (not a broken downtrend)
- I1: Previous bar closed below VWAP, current bar closes above VWAP
- I2: Volume on the reclaim bar is 1.5× the prior bar (institutions stepping in)

**Stop:** Session low

---

### 4. Volume Spike ⚡ Aggressive
**Best time: All day**

**What it looks for:** Any stock where the current 5-minute bar has 3× or more of its normal per-bar volume, and the price is near today's high. This is the most aggressive strategy — it fires whenever unusual volume appears, regardless of time of day.

**Why it works:** A sudden volume spike almost always has a reason — breaking news, an analyst upgrade, a large institutional order hitting the market. When that spike happens near the high of day rather than near the low, it means someone large is buying aggressively. The price usually follows.

**Filters:**
- D1: Price > $5 (avoids penny stocks and low-float traps)
- I1: Current bar RVOL ≥ 3× average bar volume
- I2: Price within 3% of today's high
- I3: Current bar is green (close > open — buying pressure)

**Stop:** Low of day × 0.99

---

### 5. Momentum 15 ⚡ Aggressive
**Best time: All day**

**What it looks for:** A stock that has moved 1.5%+ in the last 15 minutes, is above its VWAP, and has above-average volume. This catches intraday momentum bursts throughout the entire session — not just at the open.

**Why it works:** Intraday momentum runs happen throughout the day, not just at open. A stock that moves 1.5% in 15 minutes while staying above VWAP and on above-average volume has a catalyst pushing it. The risk is buying the end of a move — so the VWAP filter ensures you're buying into strength, not chasing a fading move.

**Filters:**
- D1: Price > $5
- I1: 15-minute price change ≥ +1.5%
- I2: Current price above VWAP
- I3: Time-normalized RVOL ≥ 1.5×

**Stop:** Entry × 0.99

---

### 6. Trend Rider ⚡ Aggressive
**Best time: All day (continuous)**

**What it looks for:** The most permissive strategy. Looks for any stock where the 9-period EMA is above the 20-period EMA on 5-minute bars, price is above VWAP, and volume is at least 1.2× normal. This is a pure trend-continuation strategy that can fire at any point during the session.

**Why it works:** The 9 EMA and 20 EMA relationship on 5-minute charts is one of the most widely watched indicators by day traders. When 9 EMA > 20 EMA and price is above VWAP, almost every technical signal points up. This setup has the highest frequency of setups per day — it's the "catch everything trending" strategy.

**Filters:**
- D1: Price > $5
- D2: Stock didn't gap down (positive bias)
- I1: Price above 9-period EMA
- I2: 9 EMA above 20 EMA (trend structure intact)
- I3: Price above VWAP (above institutional benchmark)
- I4: RVOL ≥ 1.2× (any above-average volume)

**Stop:** Entry × 0.99

---

## Risk Management

The bot controls risk at every level:

**Position sizing:** For every trade, it calculates how much to buy based on where the stop is:
```
risk_dollars = portfolio_value × 2%           (e.g. $500 on $25,000 account)
R = entry_price - stop_price                  (e.g. $1.50 risk per share)
shares = risk_dollars / R                     (e.g. 333 shares)
```
This means every single trade risks the same dollar amount regardless of the stock price.

**Stop losses:** Placed as real orders in IBKR immediately after every buy. If price hits the stop, IBKR executes the sell automatically — even if the bot is offline.

**Position management:**
- At 0.75R profit: sell ⅓ of position, lock in gains
- At 1.0R profit: move stop to entry (breakeven — now risk-free)
- After breakeven: trail stop using 5-minute swing lows

**Daily limits:**
- Max 50 trades per day
- Max 20 concurrent positions
- Force-close everything at 15:51 ET (no overnight holds)

---

## Project Files

```
little_greed/
│
├── autostart.sh          ← Start everything (run this daily)
├── stop.sh               ← Stop everything cleanly
├── run.py                ← The scheduler — runs all jobs on a timer
│
├── morning_prefilter.py  ← Scans 503 S&P 500 stocks, builds watchlist
├── cycle.py              ← Runs every 5 min: scan, enter, manage positions
├── strategy.py           ← All 6 strategies + concurrent evaluator
├── trade.py              ← Places a single order in IBKR
├── bot.py                ← Manual test: python bot.py --symbol AAPL --check-only
│
├── webui.py              ← Web dashboard (FastAPI, port 8000)
├── compute_perf.py       ← End-of-day P&L calculation
├── rotate_logs.py        ← Cleans up old log files daily
│
├── rules.json            ← Strategy settings (edit from Settings page)
├── .env                  ← Your secrets and config (never share this)
├── watchlist.txt         ← Today's gap-up candidates
├── trades.csv            ← Every fill: timestamp, price, qty, status
├── open_positions.json   ← Current open positions and stop levels
│
├── src/
│   ├── ibkr_client.py    ← Connects to IBKR via ib_async library
│   ├── notify.py         ← Sends Telegram/ntfy notifications
│   └── sp500_tickers.py  ← All 503 S&P 500 symbols
│
└── templates/            ← Web dashboard HTML pages
    ├── base.html         ← Sidebar navigation, dark theme
    ├── dashboard.html    ← Main dashboard with charts and activity feed
    ├── settings.html     ← Edit all settings without touching code
    ├── signals.html      ← Every ticker evaluated today, filter-by-filter
    ├── preflight.html    ← 11 health checks before market open
    └── kill.html         ← Emergency flatten all positions
```

---

## The Dashboard

**http://localhost:8000**

| Page | What it shows |
|------|--------------|
| Dashboard | Live positions, P&L charts, R-multiples, activity feed showing every buy/sell/stop in real-time |
| Signals | Every ticker the bot evaluated, with current value vs threshold for each filter and why it passed or failed |
| Settings | Edit everything: risk %, time windows, which strategies are active, Telegram credentials |
| Preflight | 11 health checks: Python version, TWS connection, disk space, Telegram, etc. Run before market opens. |
| Kill Switch | One-click flatten all positions. Requires typing "FLATTEN" to confirm. |

---

## Telegram Notifications

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in Settings. The bot sends:

- **BUY alert**: symbol, strategy that triggered, entry price, stop, quantity
- **STOP hit**: exit price, P&L on the trade
- **Partial exit**: at 0.75R profit — sold ⅓ position
- **Breakeven**: stop moved to entry
- **EOD force-close**: flattening all positions
- **Daily summary**: total trades, win rate, P&L, best and worst trade
- **Any crash**: full error message so you can debug

To get a Telegram bot: message `@BotFather` → `/newbot`
To get your chat ID: message `@userinfobot`

---

## Stopping and Restarting

**It's safe to stop and restart at any time.**

When you restart, the bot runs `broker_sync()` on the first cycle — it reads your actual IBKR positions and reconciles them with the local state. Nothing is lost.

Recommended routine:
- **Morning**: Start TWS, log in → run `./autostart.sh`
- **Evening**: Run `./stop.sh` — or just leave it running overnight (it exits immediately outside market hours)
- **TWS**: Leave TWS running if you want stop orders to stay active overnight

---

## Honest Assessment

**What works well:**
- Screens 503 stocks in ~8 seconds using yfinance
- Places real orders in a real IBKR paper account
- Stop orders are placed immediately at IBKR level — not just tracked locally
- Dashboard shows every decision in real-time
- State survives restarts via broker sync

**What to know before trusting it:**
- The strategies are conceptually sound but haven't been backtested on historical data
- 30 paper trading days with correct timing (entering before noon) will tell you if the parameters make money
- Gap-and-go works best in the first 2 hours — don't judge the strategy by afternoon entries
- It's paper trading: fills are at real market prices but no real money is at risk

**To make it production-ready:**
1. Run 30+ market days of paper trading and review the signals log
2. Backtest gap_and_go parameters on 12 months of historical data
3. Tune RVOL and gap thresholds based on what actually hit vs what you should have taken

---

## Setup from Scratch

See [README_INSTALL.md](README_INSTALL.md) for step-by-step TWS setup with API configuration.
See [CODE_GUIDE.md](CODE_GUIDE.md) for a full technical walkthrough of every file and the trade execution flow.
