#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# autostart.sh — Start the entire little_greed bot stack
# Run this once. It handles everything automatically.
# ─────────────────────────────────────────────────────────────────

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║         little_greed autostart               ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── 1. Activate virtual environment ──────────────────────────────
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
    echo -e "${GREEN}✓${NC} Virtual environment activated"
elif [ -f ".venv/Scripts/activate" ]; then
    source .venv/Scripts/activate
    echo -e "${GREEN}✓${NC} Virtual environment activated (Windows)"
else
    echo -e "${YELLOW}⚠ No .venv found — run: python install.py${NC}"
    exit 1
fi

# ── 2. Check Python version ───────────────────────────────────────
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "${GREEN}✓${NC} Python $PY_VER"

# ── 3. Check .env exists ──────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo -e "${RED}✗ .env missing — run: python install.py${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} .env found"

# ── 4. Check TWS is reachable ─────────────────────────────────────
IBKR_PORT=$(grep IBKR_PORT .env | cut -d= -f2 | tr -d ' \r')
IBKR_PORT=${IBKR_PORT:-7497}
echo -e "  Checking TWS on port $IBKR_PORT..."
if python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(3)
try:
    s.connect(('127.0.0.1', $IBKR_PORT))
    s.close()
    print('ok')
except:
    print('fail')
    sys.exit(1)
" 2>/dev/null | grep -q "ok"; then
    echo -e "${GREEN}✓${NC} TWS/IB Gateway reachable on port $IBKR_PORT"
else
    echo -e "${RED}✗ Cannot reach TWS on port $IBKR_PORT${NC}"
    echo -e "  Open TWS or IB Gateway, log in, and enable the API."
    echo -e "  Then re-run this script."
    exit 1
fi

# ── 5. Kill any existing run.py or uvicorn ────────────────────────
pkill -f "run.py" 2>/dev/null && echo -e "${YELLOW}↻${NC} Killed existing run.py" || true
pkill -f "uvicorn webui" 2>/dev/null && echo -e "${YELLOW}↻${NC} Killed existing uvicorn" || true
sleep 1

# ── 6. Morning prefilter (if watchlist is stale or missing) ───────
WATCHLIST_AGE=999999
if [ -f "watchlist.txt" ]; then
    NOW=$(date +%s)
    MOD=$(stat -f %m watchlist.txt 2>/dev/null || stat -c %Y watchlist.txt 2>/dev/null)
    WATCHLIST_AGE=$(( NOW - MOD ))
fi
HOUR=$(TZ="America/New_York" date +%H)
if [ "$WATCHLIST_AGE" -gt 7200 ] || [ ! -f "watchlist.txt" ]; then
    echo -e "${YELLOW}↻${NC} Watchlist stale — running morning prefilter..."
    python3 morning_prefilter.py > /tmp/prefilter_out.json 2>/dev/null && \
        echo -e "${GREEN}✓${NC} Prefilter done" || \
        echo -e "${YELLOW}⚠ Prefilter had errors (continuing)${NC}"
else
    echo -e "${GREEN}✓${NC} Watchlist fresh (age: ${WATCHLIST_AGE}s)"
fi

# ── 7. Create required dirs ───────────────────────────────────────
mkdir -p logs logs/archive
echo -e "${GREEN}✓${NC} Log directories ready"

# ── 8. Start run.py (scheduler + web server) ─────────────────────
LOG_FILE="logs/autostart_$(date +%Y%m%d).log"
echo ""
echo -e "${GREEN}${BOLD}Starting run.py scheduler...${NC}"
nohup python3 run.py >> "$LOG_FILE" 2>&1 &
RUN_PID=$!
echo -e "${GREEN}✓${NC} run.py started (PID: $RUN_PID)"
echo "$RUN_PID" > .run_pid

# ── 9. Wait for web server to be ready ───────────────────────────
echo -n "  Waiting for dashboard"
for i in {1..15}; do
    sleep 1
    echo -n "."
    if curl -s http://127.0.0.1:8000/time > /dev/null 2>&1; then
        echo ""
        echo -e "${GREEN}✓${NC} Dashboard ready at http://localhost:8000"
        break
    fi
done

# ── 10. Open browser ─────────────────────────────────────────────
if command -v open &>/dev/null; then
    open http://localhost:8000/dashboard
elif command -v xdg-open &>/dev/null; then
    xdg-open http://localhost:8000/dashboard
fi

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║  little_greed is running                     ║${NC}"
echo -e "${BOLD}║  Dashboard: http://localhost:8000            ║${NC}"
echo -e "${BOLD}║  Logs:      $LOG_FILE  ║${NC}"
echo -e "${BOLD}║  Stop:      ./stop.sh  or  Ctrl+C           ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "Tailing logs (Ctrl+C to detach — bot keeps running):"
tail -f "$LOG_FILE"
