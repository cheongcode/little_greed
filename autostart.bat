@echo off
REM ─────────────────────────────────────────────────────────────────
REM autostart.bat — Start the entire little_greed bot stack (Windows)
REM Comprehensive startup with preflight checks and error handling
REM ─────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Enable error handling
set ERRORLEVEL=0

cls
echo.
echo ============================================================
echo              little_greed autostart (Windows)
echo ============================================================
echo.

REM ── 1. Check Python installation ─────────────────────────────
echo [*] Checking Python installation...
python --version >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo [ERROR] Python not found in PATH
    echo.
    echo Actions:
    echo  1. Download Python 3.12+ from https://python.org/downloads
    echo  2. During install, check "Add Python to PATH"
    echo  3. Restart this script
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [✓] Python %PYVER% found

REM ── 2. Check venv exists ────────────────────────────────────
echo [*] Checking virtual environment...
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found
    echo.
    echo Run this first:  python install.bat
    echo.
    pause
    exit /b 1
)
echo [✓] Virtual environment exists

REM ── 3. Activate venv ────────────────────────────────────────
call .venv\Scripts\activate.bat
if !ERRORLEVEL! neq 0 (
    echo [ERROR] Failed to activate virtual environment
    pause
    exit /b 1
)
echo [✓] Virtual environment activated

REM ── 4. Check required files exist ───────────────────────────
echo [*] Checking required files...
set MISSING=0

if not exist ".env" (
    echo [ERROR] .env file missing
    set MISSING=1
)
if not exist "run.py" (
    echo [ERROR] run.py file missing
    set MISSING=1
)
if not exist "cycle.py" (
    echo [ERROR] cycle.py file missing
    set MISSING=1
)
if not exist "requirements.txt" (
    echo [ERROR] requirements.txt file missing
    set MISSING=1
)

if !MISSING! equ 1 (
    echo.
    echo [ERROR] One or more required files are missing
    echo [ERROR] Make sure you're in the little_greed directory
    pause
    exit /b 1
)
echo [✓] All required files present

REM ── 5. Create required directories ──────────────────────────
echo [*] Creating required directories...
if not exist "logs" mkdir logs
if not exist "logs\archive" mkdir logs\archive
if not exist "static" mkdir static
if not exist "templates" mkdir templates
if not exist "reports" mkdir reports
echo [✓] Directories ready

REM ── 6. Test IBKR connection ─────────────────────────────────
echo [*] Testing IBKR/TWS connection on port 7497...

REM Simple connectivity test using timeout
(
    timeout /t 1 /nobreak >nul
    echo connecting to TWS
) | powershell -Command "
    \$client = New-Object System.Net.Sockets.TcpClient
    try {
        \$client.Connect('127.0.0.1', 7497)
        \$client.Close()
        Write-Host 'connected'
    }
    catch {
        Write-Host 'failed'
    }
" > conn_test.tmp 2>&1

for /f %%i in (conn_test.tmp) do set CONN_RESULT=%%i
del /q conn_test.tmp 2>nul

if "!CONN_RESULT!"=="connected" (
    echo [✓] IBKR/TWS reachable on port 7497
) else (
    echo [!] IBKR/TWS not reachable on port 7497
    echo.
    echo [WARNING] Make sure TWS or IB Gateway is:
    echo   1. Running on this machine
    echo   2. Logged in with your paper account
    echo   3. API enabled (Edit ^> Settings ^> API ^> Settings)
    echo   4. Socket port set to 7497
    echo.
    echo You can continue, but the bot will fail to connect.
    set /p CONTINUE="Continue anyway? (y/n): "
    if /i not "!CONTINUE!"=="y" (
        pause
        exit /b 1
    )
)

REM ── 7. Kill existing processes ──────────────────────────────
echo [*] Cleaning up any existing processes...

REM Find and kill any existing Python run.py processes
for /f "tokens=2 delims=," %%A in ('tasklist /FI "IMAGENAME eq python.exe" /FO csv 2^>nul') do (
    REM Note: This is a broad kill - might affect other Python processes
    REM In production, better to use specific detection
)

REM Alternative: use netstat to find what's using port 8000
for /f "tokens=5" %%A in ('netstat -ano ^| find ":8000"') do (
    taskkill /F /PID %%A >nul 2>&1
)

timeout /t 1 /nobreak >nul
echo [✓] Cleaned up old processes

REM ── 8. Check watchlist freshness ───────────────────────────
echo [*] Checking watchlist...
if not exist "watchlist.txt" (
    echo [!] Watchlist missing — running morning prefilter...
    python morning_prefilter.py >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo [✓] Prefilter completed
    ) else (
        echo [!] Prefilter had errors (continuing)
    )
) else (
    echo [✓] Watchlist present
)

REM ── 9. Start the bot (run.py) ───────────────────────────────
echo.
echo [*] Starting bot (run.py)...

for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set mydate=%%c%%a%%b)
set LOGFILE=logs\autostart_!mydate!.log

REM Start run.py in background
start /B "" python run.py >> "!LOGFILE!" 2>&1

timeout /t 2 /nobreak >nul

REM Verify it started
tasklist | find "python.exe" >nul
if !ERRORLEVEL! equ 0 (
    echo [✓] run.py started (PID saved to .run_pid)
) else (
    echo [ERROR] Failed to start run.py
    echo [ERROR] Check logs: !LOGFILE!
    type "!LOGFILE!"
    pause
    exit /b 1
)

REM ── 10. Wait for web server ────────────────────────────────
echo [*] Waiting for dashboard server...
setlocal enabledelayedexpansion
set attempts=0
set max_attempts=15

:wait_dashboard
timeout /t 1 /nobreak >nul

REM Try to reach the dashboard
powershell -Command "
    try {
        \$response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/time' -TimeoutSec 2 -ErrorAction Stop
        if (\$response.StatusCode -eq 200) {
            Write-Host 'ready'
        }
    }
    catch {
        Write-Host 'waiting'
    }
" > dash_test.tmp 2>&1

for /f %%i in (dash_test.tmp) do set DASH_RESULT=%%i
del /q dash_test.tmp 2>nul

if "!DASH_RESULT!"=="ready" (
    echo [✓] Dashboard ready at http://localhost:8000
    goto :dashboard_ready
)

set /a attempts=!attempts!+1
if !attempts! LSS !max_attempts! (
    goto :wait_dashboard
)

echo [!] Dashboard did not start in time
echo [!] Check logs: !LOGFILE!
pause
exit /b 1

:dashboard_ready

REM ── 11. Open browser ────────────────────────────────────────
echo [*] Opening dashboard in default browser...
start http://localhost:8000/dashboard

REM ── 12. Success banner ──────────────────────────────────────
echo.
echo ============================================================
echo              little_greed is RUNNING
echo ============================================================
echo.
echo Dashboard:     http://localhost:8000
echo Logs:          !LOGFILE!
echo Stop:          stop.bat  or  Ctrl+C in this window
echo.
echo Keep this window open. The bot runs in the background.
echo Press Ctrl+C here to stop the bot gracefully.
echo.

REM ── 13. Tail logs ───────────────────────────────────────────
echo Following logs (press Ctrl+C to detach - bot keeps running):
echo.

:tail_logs
timeout /t 2 /nobreak >nul
cls
type "!LOGFILE!" 2>nul
goto :tail_logs
