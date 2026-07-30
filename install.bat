@echo off
REM =========================================================
REM install.bat - Complete Windows setup for little_greed
REM Checks Python, creates venv, installs deps, tests IBKR
REM ASCII-only, no special Unicode characters
REM =========================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

cls
echo.
echo =========================================================
echo              little_greed Installer
echo =========================================================
echo.

REM --- 1. Python check ---
echo [*] Checking Python...
python --version >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo [ERROR] Python not found in PATH
    echo.
    echo Download Python 3.12+ from: https://python.org/downloads
    echo IMPORTANT: Check "Add Python to PATH" during install
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    if %%a LSS 3 (
        echo [ERROR] Python 3.12+ required, you have %PYVER%
        pause
        exit /b 1
    )
    if %%a EQU 3 (
        if %%b LSS 12 (
            echo [ERROR] Python 3.12+ required, you have %PYVER%
            pause
            exit /b 1
        )
    )
)
echo [OK] Python %PYVER%

REM --- 2. Create venv ---
echo [*] Creating virtual environment...
if exist ".venv" (
    echo [OK] .venv already exists
) else (
    python -m venv .venv >nul 2>&1
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] Failed to create venv
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
)

REM --- 3. Activate venv ---
call .venv\Scripts\activate.bat >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo [ERROR] Failed to activate venv
    pause
    exit /b 1
)
echo [OK] Virtual environment activated

REM --- 4. Upgrade pip ---
echo [*] Upgrading pip...
python -m pip install --upgrade pip --quiet 2>nul
echo [OK] pip upgraded

REM --- 5. Install requirements ---
echo [*] Installing dependencies...
if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found
    pause
    exit /b 1
)
pip install -r requirements.txt --quiet 2>nul
if !ERRORLEVEL! neq 0 (
    echo [ERROR] Failed to install packages
    echo Run manually: pip install -r requirements.txt
    pause
    exit /b 1
)
echo [OK] Dependencies installed

REM --- 6. Create .env ---
echo [*] Creating configuration...
if not exist ".env" (
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
    echo [OK] .env created - edit it with your settings
) else (
    echo [OK] .env already exists
)

REM --- 7. Create directories ---
echo [*] Creating directories...
if not exist "logs" mkdir logs
if not exist "logs\archive" mkdir logs\archive
if not exist "static" mkdir static
if not exist "templates" mkdir templates
if not exist "reports" mkdir reports
echo [OK] Directories created

REM --- 8. Test IBKR ---
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
" > ibkr_test.tmp 2>&1

set TEST_RESULT=FAIL
for /f %%i in (ibkr_test.tmp) do set TEST_RESULT=%%i
del /q ibkr_test.tmp 2>nul

if "!TEST_RESULT!" == "OK" (
    echo [OK] IBKR connection successful
) else (
    echo [WARNING] IBKR not reachable
    echo [INFO] Start TWS/IB Gateway and log in before running the bot
)

REM --- 9. Success ---
cls
echo.
echo =========================================================
echo                    SUCCESS
echo =========================================================
echo.
echo Installation complete!
echo.
echo Next: Run start.bat to begin trading
echo.
pause
