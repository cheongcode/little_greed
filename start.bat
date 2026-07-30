@echo off
REM =========================================================
REM start.bat - Complete Windows startup script
REM Checks everything and starts little_greed trading bot
REM ASCII-only, no special Unicode characters
REM =========================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"
set ERRORS=0

cls
echo.
echo =========================================================
echo                  little_greed Startup
echo =========================================================
echo.

REM --- 1. Python check ---
echo [*] Checking Python...
python --version >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo [ERROR] Python not found
    set ERRORS=1
    goto show_errors
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [OK] Python %PYVER%

REM --- 2. Virtual environment check ---
echo [*] Checking virtual environment...
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found
    echo [INFO] Run: python install.bat
    set ERRORS=1
    goto show_errors
)
echo [OK] Virtual environment exists

REM --- 3. Activate venv ---
call .venv\Scripts\activate.bat >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo [ERROR] Failed to activate venv
    set ERRORS=1
    goto show_errors
)
echo [OK] Virtual environment activated

REM --- 4. Required files check ---
echo [*] Checking required files...
set MISSING=0
if not exist "run.py" (
    echo [ERROR] run.py not found
    set MISSING=1
)
if not exist "cycle.py" (
    echo [ERROR] cycle.py not found
    set MISSING=1
)
if not exist ".env" (
    echo [ERROR] .env not found - run: python install.bat
    set MISSING=1
)
if !MISSING! equ 1 (
    set ERRORS=1
    goto show_errors
)
echo [OK] All files present

REM --- 5. Create all required directories ---
echo [*] Creating directories...
if not exist "logs" mkdir logs
if not exist "logs\archive" mkdir logs\archive
if not exist "static" mkdir static
if not exist "templates" mkdir templates
if not exist "reports" mkdir reports
echo [OK] Directories ready

REM --- 6. IBKR connectivity test ---
echo [*] Testing IBKR connection...
powershell -Command "
    try {
        \$client = New-Object System.Net.Sockets.TcpClient
        \$result = \$client.BeginConnect('127.0.0.1', 7497, \$null, \$null)
        \$wait = \$result.AsyncWaitHandle.WaitOne(3000)
        if (\$wait) {
            \$client.EndConnect(\$result)
            \$client.Close()
            Write-Host 'OK'
        } else {
            Write-Host 'TIMEOUT'
        }
    } catch {
        Write-Host 'FAIL'
    }
" > ibkr_check.tmp 2>&1

set IBKR_OK=FAIL
for /f %%i in (ibkr_check.tmp) do set IBKR_OK=%%i
del /q ibkr_check.tmp 2>nul

if "!IBKR_OK!" == "OK" (
    echo [OK] IBKR reachable
) else (
    echo [WARNING] IBKR not reachable
    echo [INFO] Make sure TWS/IB Gateway is running
)

REM --- 7. Kill old processes ---
echo [*] Cleaning old processes...
for /f "tokens=5" %%A in ('netstat -ano 2^>nul ^| find ":8000"') do (
    taskkill /F /PID %%A >nul 2>&1
)
timeout /t 1 /nobreak >nul
echo [OK] Cleaned

REM --- 8. Get log filename ---
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set LOGDATE=%%c%%a%%b)
set LOGFILE=logs\autostart_!LOGDATE!.log

REM --- 9. Start bot ---
echo [*] Starting bot...
start /B "" python run.py >> "!LOGFILE!" 2>&1
timeout /t 2 /nobreak >nul
echo [OK] Bot started

REM --- 10. Wait for dashboard ---
echo [*] Waiting for dashboard...
set ATTEMPT=0
:wait_loop
if !ATTEMPT! geq 15 (
    echo [ERROR] Dashboard timeout
    echo [INFO] Check: !LOGFILE!
    set ERRORS=1
    goto show_errors
)

powershell -Command "
    try {
        \$response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/time' -TimeoutSec 1 -ErrorAction Stop
        if (\$response.StatusCode -eq 200) {
            Write-Host 'OK'
        }
    } catch {
        Write-Host 'WAIT'
    }
" > dash_check.tmp 2>&1

set DASH_OK=WAIT
for /f %%i in (dash_check.tmp) do set DASH_OK=%%i
del /q dash_check.tmp 2>nul

if "!DASH_OK!" == "OK" (
    echo [OK] Dashboard ready
    goto dashboard_ready
)

timeout /t 1 /nobreak >nul
set /a ATTEMPT=!ATTEMPT!+1
goto wait_loop

:dashboard_ready
REM --- 11. Open browser ---
start http://localhost:8000/dashboard

REM --- 12. Success ---
echo.
echo =========================================================
echo                SUCCESS - BOT RUNNING
echo =========================================================
echo.
echo Dashboard:  http://localhost:8000
echo Logs:       !LOGFILE!
echo.
echo Keep this window open. Press Ctrl+C to stop.
echo.

REM --- 13. Tail logs ---
:tail
timeout /t 3 /nobreak >nul
cls
type "!LOGFILE!" 2>nul
goto tail

:show_errors
echo.
echo =========================================================
echo                    ERRORS FOUND
echo =========================================================
echo.
echo Please fix the issues above and try again.
echo.
pause
exit /b 1
