# little_greed

A paper-trading bot for Interactive Brokers that screens the full S&P 500 for momentum setups and manages positions automatically. Runs entirely on your machine, connects to your IBKR paper account through TWS or IB Gateway, and has a live web dashboard at `http://localhost:8000`.

**Status: Working** — IBKR connection verified, real paper fills confirmed, all 4 strategies implemented with live filter evaluation.

---

## Quick Start

```bash
# 1. Install (creates .venv, installs deps, tests IBKR connection)
python install.py

# 2. Start
./start.sh          # macOS
start.bat           # Windows

# 3. Open browser
http://localhost:8000
```

Go to **Preflight** first to confirm all systems are green.

---

## What It Does

| Time (ET) | Action |
|-----------|--------|
| 9:25 AM | Screens all 503 S&P 500 stocks for gap-up setups → `watchlist.txt` |
| 9:55 AM | Second prefilter run (catches late movers) |
| 10:05 AM | Entry window opens — cycle scans watchlist every 5 min |
| 10:05–15:30 | Places market orders when strategy triggers, manages stops |
| 15:30–15:51 | Manage-only mode (no new entries, existing positions managed) |
| 15:51 PM | **Force-close** — everything sold before close |
| 16:05 PM | Daily P&L summary sent via Telegram |

---

## Strategies

Switch strategies from **Settings → Active Strategy** — no code changes needed.

### 1. Gap and Go *(default)*

Stocks that gap up 3%+ from prior close, trading above yesterday's high, above their 200-day SMA, above premarket highs, with RVOL > 2× — then making new highs of the day.

**Filters:**
- D1: Today's price > prior day high
- D2: Prior close > 200-day SMA (trend filter)
- D3: Gap % from prior close ≥ 3.0%
- I1: Current price > premarket high
- I2: Current price near today's high of day
- I3: RVOL (today's volume vs 14-day avg) ≥ 2.0×

**Stop:** Low of day × 0.99
**Partial exit:** ⅓ position at 0.75R, stop moves to entry
**Breakeven:** Stop moves to entry at 1.0R
**Trail:** 5-min swing lows after breakeven

---

### 2. Opening Range Breakout (ORB 15)

The first 15 minutes after open establish a range. A clean break above that range with a tight initial range signals institutional accumulation.

**Filters:**
- D1: Above 200-day SMA
- I1: Current price > 15-min opening range high
- I2: Opening range width < 2% (tight = controlled)

**Stop:** Opening range low
**Best window:** 9:45–10:30 ET

---

### 3. VWAP Reclaim

When a stock dips below its daily VWAP and then reclaims it with above-average volume, algorithms and institutions are stepping in — a reliable intraday reversal signal.

**Filters:**
- D1: Above 200-day SMA
- I1: Prior bar below VWAP → current bar above VWAP
- I2: Volume on reclaim bar > 1.5× prior bar

**Stop:** Session low
**Best window:** 10:30–14:00 ET

---

### 4. High of Day Break

A stock making new highs of the day with double the average volume means strong demand. Works best when the stock is also above prior day's high.

**Filters:**
- D1: Current price > prior day high
- D2: Above 200-day SMA
- I1: Current bar is setting new HOD
- I2: Volume > 2× recent average

**Stop:** Prior swing low
**Best window:** 11:00–14:00 ET

---

## Risk Management

All configurable from Settings or `.env`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| Portfolio Value | $25,000 | Your paper account balance |
| Max Risk Per Trade | 1.0% | Max loss if stop hits ($250 on $25k) |
| Max Trade Size | $2,500 | Hard USD cap per position |
| Max Trades Per Day | 5 | Bot stops entering after N buys |
| Force Close | 15:51 ET | Everything sold before close |

**Position sizing:**
```
risk_dollars = portfolio × max_risk_pct
size = min(risk_dollars / R, portfolio × 10% / price)
```
Where R = entry_price − stop_price.

---

## Dashboard Pages

| Page | URL | What you see |
|------|-----|-------------|
| Dashboard | `/dashboard` | Live positions, P&L chart, trade history, strategy filter panel |
| Signals | `/signals` | Every ticker evaluated: current value vs threshold, pass/fail badges |
| Settings | `/settings` | Edit risk params, time windows, strategy, Telegram credentials |
| Preflight | `/preflight` | 11 health checks: Python version, TWS connection, disk space, etc. |
| Kill Switch | `/kill` | Flatten everything with one confirmed click |

---

## Project Structure

```
little_greed/
├── run.py                 ← Start here. Scheduler + web server.
├── cycle.py               ← Trading brain. Runs every 5 min.
├── strategy.py            ← 4 strategies. Add your own here.
├── morning_prefilter.py   ← S&P 500 gap screener.
├── bot.py                 ← Manual test: python bot.py --symbol NVDA --check-only
├── trade.py               ← Executes 1 order via IBKR subprocess.
├── webui.py               ← FastAPI dashboard (localhost:8000).
├── compute_perf.py        ← End-of-day P&L summary.
├── rotate_logs.py         ← Log rotation and trades.csv pruning.
├── rules.json             ← Strategy parameters (editable from UI).
├── src/
│   ├── ibkr_client.py     ← ib_async wrapper.
│   ├── notify.py          ← Telegram/ntfy alerts.
│   └── sp500_tickers.py   ← 503 S&P 500 tickers (auto-updated).
└── templates/             ← Web UI templates (Tailwind + HTMX).
```

---

## Requirements

- Python 3.12+
- IBKR paper account (free at interactivebrokers.com)
- TWS or IB Gateway running with API enabled (port 7497)
- macOS 12+ or Windows 10+

---

## Telegram Notifications

Add your bot token and chat ID in Settings. The bot sends alerts for:
- Every BUY entry (symbol, price, stop, qty)
- Stop-out exits (price, P&L)
- Partial fills at 0.75R
- Breakeven stop moves
- EOD force-close
- Daily P&L summary
- Job crashes

To create a Telegram bot: message `@BotFather` → `/newbot`. To get your chat ID: message `@userinfobot`.

---

## Adding a New Strategy

1. Open `strategy.py`
2. Copy the `_hod_break()` function as a template
3. Rename it and implement your filters
4. Add it to the `STRATEGIES` dict and `STRATEGY_DESCRIPTIONS`
5. It appears in Settings automatically — no UI changes needed

---

## Documentation

- [CODE_GUIDE.md](CODE_GUIDE.md) — full code walkthrough, trade flow, state management
- [README_INSTALL.md](README_INSTALL.md) — step-by-step setup guide
