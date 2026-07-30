@echo off
REM =========================================================
REM little_greed.bat - Unified installer + launcher
REM   First run:  installs Python venv, deps, config, dirs
REM   Every run:  verifies environment, then starts the bot
REM ASCII-only. Foreground bot (Ctrl+C stops cleanly).
REM =========================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

cls
echo.
echo =========================================================
echo              little_greed - Setup ^& Launch
echo =========================================================
echo.

REM ---------- 1. Python present ----------
echo [*] Checking Python...
where python >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo [ERROR] Python not found in PATH.
    echo         Install Python 3.12+ from https://python.org/downloads
    echo         Tick "Add Python to PATH" during install.
    goto fail
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [OK] System Python !PYVER!

REM ---------- 2. Python version >= 3.12 ----------
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set PYMAJ=%%a
    set PYMIN=%%b
)
if !PYMAJ! LSS 3 (
    echo [ERROR] Python 3.12+ required, found !PYVER!
    goto fail
)
if !PYMAJ! EQU 3 if !PYMIN! LSS 12 (
    echo [ERROR] Python 3.12+ required, found !PYVER!
    goto fail
)

REM ---------- 3. Source files present ----------
echo [*] Checking source files...
set MISSING=0
if not exist "run.py"           ( echo [ERROR] run.py missing          & set MISSING=1 )
if not exist "cycle.py"         ( echo [ERROR] cycle.py missing        & set MISSING=1 )
if not exist "requirements.txt" ( echo [ERROR] requirements.txt missing & set MISSING=1 )
if !MISSING! equ 1 goto fail
echo [OK] Source files present

REM ---------- 4. Virtual environment ----------
set VENV_PY=.venv\Scripts\python.exe
if not exist "!VENV_PY!" (
    echo [*] Creating virtual environment...
    python -m venv .venv
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] venv creation failed
        goto fail
    )
    echo [OK] venv created
) else (
    echo [OK] venv exists
)

REM ---------- 5. Upgrade pip (best effort) ----------
echo [*] Ensuring pip is current...
"!VENV_PY!" -m pip install --upgrade pip --quiet
if !ERRORLEVEL! neq 0 echo [WARN] pip upgrade failed, continuing

REM ---------- 6. Requirements install / verify ----------
REM Stamp file so we only reinstall when requirements.txt changes.
set STAMP=.venv\.requirements.stamp
set NEED_INSTALL=1
if exist "!STAMP!" (
    powershell -NoProfile -Command "if ((Get-Item 'requirements.txt').LastWriteTimeUtc -le (Get-Item '!STAMP!').LastWriteTimeUtc) { exit 0 } else { exit 1 }"
    if !ERRORLEVEL! equ 0 set NEED_INSTALL=0
)

if !NEED_INSTALL! equ 1 (
    echo [*] Installing dependencies from requirements.txt...
    "!VENV_PY!" -m pip install -r requirements.txt
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] pip install failed. Fix errors above and rerun.
        goto fail
    )
    echo done > "!STAMP!"
    echo [OK] Dependencies installed
) else (
    echo [OK] Dependencies up to date
)

REM ---------- 7. .env config ----------
if not exist ".env" (
    echo [*] Creating default .env...
    (
        echo IBKR_HOST=127.0.0.1
        echo IBKR_PORT=7497
        echo IBKR_CLIENT_ID=90
        echo PAPER_TRADING=true
        echo PORTFOLIO_VALUE_USD=25000
        echo MAX_POSITIONS=20
        echo MAX_TRADES_PER_DAY=50
        echo TELEGRAM_BOT_TOKEN=
        echo TELEGRAM_CHAT_ID=
    ) > .env
    echo [OK] .env created - edit it if you need non-default settings
) else (
    echo [OK] .env exists
)

REM ---------- 8. Read host/port from .env for IBKR check ----------
set IBKR_HOST=127.0.0.1
set IBKR_PORT=7497
for /f "usebackq tokens=1,2 delims==" %%a in (".env") do (
    if /i "%%a"=="IBKR_HOST" set IBKR_HOST=%%b
    if /i "%%a"=="IBKR_PORT" set IBKR_PORT=%%b
)

REM ---------- 9. Directories ----------
echo [*] Ensuring directories...
for %%d in (logs logs\archive static templates reports) do (
    if not exist "%%d" mkdir "%%d"
)
echo [OK] Directories ready

REM ---------- 10. IBKR connectivity (non-blocking) ----------
echo [*] Testing IBKR at !IBKR_HOST!:!IBKR_PORT!...
for /f "usebackq delims=" %%r in (`powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient; $r = $c.BeginConnect('!IBKR_HOST!',!IBKR_PORT!,$null,$null); if ($r.AsyncWaitHandle.WaitOne(3000)) { $c.EndConnect($r); $c.Close(); 'OK' } else { 'TIMEOUT' } } catch { 'FAIL' }"`) do set IBKR_STATUS=%%r

if /i "!IBKR_STATUS!"=="OK" (
    echo [OK] IBKR reachable
) else (
    echo [WARN] IBKR not reachable ^(!IBKR_STATUS!^). Start TWS/IB Gateway if you want live data.
    echo [WARN] Continuing anyway - bot will start.
)

REM ---------- 11. Free port 8000 if held by a stale process ----------
echo [*] Checking port 8000...
for /f "tokens=5" %%A in ('netstat -ano -p tcp ^| findstr /R /C:":8000 .*LISTENING"') do (
    echo [INFO] Killing stale PID %%A on port 8000
    taskkill /F /PID %%A >nul 2>&1
)
echo [OK] Port 8000 free

REM ---------- 12. Log filename (locale-safe via PowerShell) ----------
for /f "usebackq" %%i in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`) do set STAMP2=%%i
set LOGFILE=logs\run_!STAMP2!.log
echo [INFO] Log file: !LOGFILE!

REM ---------- 13. Launch bot in foreground with unbuffered tee ----------
echo.
echo =========================================================
echo   Starting bot. Dashboard: http://localhost:8000
echo   Press Ctrl+C to stop.
echo =========================================================
echo.

REM Open browser after short delay, async.
start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 6; Start-Process 'http://localhost:8000/dashboard'"

REM Force unbuffered Python I/O across the pipeline.
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

REM Python-based tee: reads bot stdout line-by-line, flushes each line to
REM both console and log file. No PowerShell object buffering in between.
"!VENV_PY!" -u run.py 2>&1 | "!VENV_PY!" -u -c "import sys; f=open(sys.argv[1],'w',buffering=1,encoding='utf-8',errors='replace'); [(sys.stdout.write(l), sys.stdout.flush(), f.write(l), f.flush()) for l in sys.stdin]" "!LOGFILE!"

set RC=!ERRORLEVEL!
echo.
echo [INFO] Bot exited with code !RC!
echo [INFO] Log saved to !LOGFILE!
pause
exit /b !RC!

:fail
echo.
echo =========================================================
echo                    SETUP FAILED
echo =========================================================
echo Fix the issue reported above and run this script again.
echo.
pause
exit /b 1