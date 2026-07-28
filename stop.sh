#!/bin/bash
# Stop the little_greed bot cleanly

echo "Stopping little_greed..."

if [ -f ".run_pid" ]; then
    PID=$(cat .run_pid)
    kill "$PID" 2>/dev/null && echo "✓ run.py (PID $PID) stopped" || echo "Already stopped"
    rm -f .run_pid
fi

pkill -f "run.py" 2>/dev/null && echo "✓ Killed run.py" || true
pkill -f "uvicorn webui" 2>/dev/null && echo "✓ Killed uvicorn" || true
pkill -f "morning_prefilter" 2>/dev/null || true

echo "Done. Bot is stopped."
