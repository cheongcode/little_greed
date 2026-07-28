# little_greed — Setup Guide

little_greed is a paper-trading bot for Interactive Brokers that screens S&P 500 stocks for gap-and-go setups and manages positions automatically. It runs entirely on your computer and connects to your IBKR paper account through TWS or IB Gateway.

---

## Requirements

| Requirement | Notes |
|---|---|
| **Python 3.12+** | [python.org/downloads](https://python.org/downloads/) |
| **IBKR paper account** | Free at [interactivebrokers.com](https://www.interactivebrokers.com) |
| **TWS or IB Gateway** | Installed and logged in on the same machine |
| **Windows 10+ or macOS 12+** | Linux not supported (TWS requires a desktop) |

---

## Step 1 — Install and configure TWS

1. Download **Trader Workstation (TWS)** from:
   https://www.interactivebrokers.com/en/trading/tws.php

2. Log in using your **paper trading** username and password.
   (Paper accounts start with a `D` prefix, e.g. `DUQ635712`)

3. Enable the API socket:
   - In TWS: **Edit → Global Configuration → API → Settings**
   - Check **"Enable ActiveX and Socket Clients"**
   - Set **Socket port** to `7497`
   - Check **"Allow connections from localhost only"**
   - Click **Apply** then **OK**

4. Leave TWS open. The bot connects to it while it is running.

---

## Step 2 — Run the installer

Open a terminal (Command Prompt on Windows, Terminal on macOS) in the project folder and run:

```
python install.py
```

The installer will:
- Create a Python virtual environment (`.venv/`)
- Install all dependencies
- Ask for your port and portfolio size (press Enter to accept defaults)
- Test the connection to TWS
- Print a success message when ready

> If you see a connection error, go back to Step 1 and make sure the API socket is enabled in TWS.

---

## Step 3 — Start the bot

**Windows:** double-click `start.bat`

**macOS:** run in terminal:
```
./start.sh
```

This activates the virtual environment, opens your browser, and starts the bot.

---

## Step 4 — Check the dashboard

Your browser will open to **http://localhost:8000**

Go to **Preflight** first to confirm all systems are green before the market opens.

Key pages:
| Page | What it does |
|---|---|
| **Dashboard** | Live positions, P&L, stop distances |
| **Signals** | Every ticker the bot evaluated today and why |
| **Settings** | Edit risk limits, time windows, Telegram alerts |
| **Preflight** | Health check — run this before market open |
| **Kill Switch** | Flatten all positions immediately |

---

## How to stop

Press **Ctrl+C** in the terminal window where `start.bat` / `start.sh` is running.

---

## Troubleshooting

**"Connect call failed (127.0.0.1, 7497)"**
TWS is not running or the API socket is not enabled.
→ Start TWS, log in, enable API socket (Step 1), then restart the bot.

**"Preflight shows 3/11 passed"**
This is normal before market open. TWS checks will pass once TWS is running.
→ Visit http://localhost:8000/preflight for specific fix hints per check.

**"watchlist.txt missing"**
The morning pre-filter has not run yet.
→ This runs automatically at 9:25 ET on weekdays. Run it manually: `python morning_prefilter.py`

**Telegram notifications not working**
→ Go to http://localhost:8000/settings, enter your Bot Token and Chat ID, click "Send test".
To create a bot: message @BotFather on Telegram, type `/newbot`, follow the prompts.
To get your Chat ID: message @userinfobot on Telegram.

---

## How to distribute this bot to someone else

Zip the project folder, **excluding** these paths:

```
.venv/
.env
logs/
trades.csv
open_positions.json
watchlist.txt
```

The recipient runs `python install.py` on their own machine.
Their `.env` is created fresh from `.env.template` — no secrets travel in the zip.
