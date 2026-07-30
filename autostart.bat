@echo off
REM ─────────────────────────────────────────────────────────────────
REM autostart.bat — Start the entire little_greed bot stack (Windows)
REM Run this once every trading day. It handles everything automatically.
REM ─────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion
cd /d "%~dp0"

cls
echo.
echo ╔══════════════════════════════════════════════╗
echo ║         little_greed autostart               ║
echo ║         (Windows)                            ║
echo ╚══════════════════════════════════════════════╝
echo.

REM ── 1. Activate virtual environment ──────────────────────────────
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    echo [✓] Virtual environment activated
) else (
    echo [ERROR] .venv not found. Run: python install.bat
    pause
    exit /b 1
)

REM ── 2. Check Python version ──────────────────────────────────────
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [✓] Python %PYVER%

REM ── 3. Check .env exists ─────────────────────────────────────────
if not exist ".env" (
    echo [ERROR] .env missing. Run: python install.bat
    pause
    exit /b 1
)
echo [✓] .env found

REM ── 4. Check TWS is reachable ────────────────────────────────────
echo [*] Checking TWS on port 7497...
for /f "delims=" %%i in ('python -c "import socket; s=socket.socket(); s.settimeout(3); result='ok' if s.connect_ex(('127.0.0.1',7497))==0 else 'fail'; s.close(); print(result)" 2^>nul') do set IBKR_TEST=%%i

if "%IBKR_TEST%"=="ok" (
    echo [✓] TWS/IB Gateway reachable on port 7497
) else (
    echo [ERROR] Cannot reach TWS on port 7497
    echo [ERROR] Open TWS or IB Gateway, log in, and enable the API.
    echo [ERROR] Then re-run this script.
    pause
    exit /b 1
)

REM ── 5. Kill any existing processes ──────────────────────────────
taskkill /F /FI "WINDOWTITLE eq run.py*" 2>nul
taskkill /F /IM python.exe /V 2>nul | find "run.py" >nul && (echo [↻] Killed existing processes)

REM ── 6. Check watchlist freshness ───────────────────────────────
echo [*] Checking watchlist...
if not exist watchlist.txt (
    echo [↻] Watchlist missing — running morning prefilter...
    python morning_prefilter.py > nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo [✓] Prefilter done
    ) else (
        echo [!] Prefilter had errors (continuing)
    )
) else (
    echo [✓] Watchlist fresh
)

REM ── 7. Create required directories ─────────────────────────────
if not exist logs mkdir logs
if not exist logs\archive mkdir logs\archive
echo [✓] Log directories ready

REM ── 8. Start run.py ────────────────────────────────────────────
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set mydate=%%c%%a%%b)
for /f "tokens=1-2 delims=/:" %%a in ('time /t') do (set mytime=%%a%%b)
set LOGFILE=logs\autostart_%mydate%.log

echo.
echo [*] Starting run.py scheduler...
start /B python run.py >> "!LOGFILE!" 2>&1

REM Get PID (Windows version - using tasklist)
for /f "tokens=2" %%A in ('tasklist /FI "WINDOWTITLE eq python*" /FO list ^| find /V "Image"') do (
    echo !pid! > .run_pid
    goto :pid_saved
)
:pid_saved
echo [✓] run.py started

REM ── 9. Wait for web server to be ready ──────────────────────────
echo [*] Waiting for dashboard (max 15 seconds)
setlocal enabledelayedexpansion
set attempts=0
:wait_loop
if !attempts! geq 15 (
    echo [!] Dashboard did not start in time. Check !LOGFILE!
    pause
    exit /b 1
)

timeout /t 1 /nobreak >nul
for /f %%i in ('curl -s -o nul -w "%%{http_code}" http://127.0.0.1:8000/time 2^>nul') do (
    if "%%i"=="200" (
        echo.
        echo [✓] Dashboard ready at http://localhost:8000
        goto :dashboard_ready
    )
)
set /a attempts=!attempts!+1
goto :wait_loop

:dashboard_ready
REM ── 10. Open browser ────────────────────────────────────────────
start http://localhost:8000/dashboard

echo.
echo ╔══════════════════════════════════════════════╗
echo ║  little_greed is running                     ║
echo ║  Dashboard: http://localhost:8000            ║
echo ║  Logs:      !LOGFILE!                       ║
echo ║  Stop:      stop.bat or close this window   ║
echo ╚══════════════════════════════════════════════╝
echo.
echo Dashboard opening in your browser...
echo Keep this window open. Press Ctrl+C to stop the bot.
echo.

REM Tail logs (simplified for Windows - just wait)
timeout /t 2 /nobreak >nul
cls
echo.
echo Following logs (Ctrl+C to detach — bot keeps running):
echo.
:tail_loop
type "!LOGFILE!" 2>nul
timeout /t 5 /nobreak >nul
goto :tail_loop
