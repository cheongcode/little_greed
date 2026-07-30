@echo off
REM ─────────────────────────────────────────────────────────────────
REM install.bat — Complete Windows setup for little_greed
REM Checks Python, creates venv, installs deps, tests IBKR connection
REM ─────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion
cd /d "%~dp0"

cls
echo.
echo ============================================================
echo              little_greed Installer (Windows)
echo ============================================================
echo.

REM ── 1. Python version check ─────────────────────────────────
echo [1] Checking Python installation...
python --version >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo.
    echo [✗ ERROR] Python not found in system PATH
    echo.
    echo Required: Python 3.12 or newer
    echo Download: https://python.org/downloads
    echo.
    echo Installation steps:
    echo   1. Download Python 3.12+ installer from python.org
    echo   2. Run the installer
    echo   3. IMPORTANT: Check "Add Python to PATH" during installation
    echo   4. Click "Install Now"
    echo   5. Restart this batch file after Python is installed
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    if %%a LSS 3 (
        echo [✗ ERROR] Python 3.12+ required, you have %PYVER%
        pause
        exit /b 1
    )
    if %%a EQU 3 (
        if %%b LSS 12 (
            echo [✗ ERROR] Python 3.12+ required, you have %PYVER%
            pause
            exit /b 1
        )
    )
)
echo [✓] Python %PYVER% OK
echo.

REM ── 2. Create virtual environment ───────────────────────────
echo [2] Setting up virtual environment...
if exist ".venv" (
    echo [✓] .venv already exists
) else (
    echo [*] Creating .venv (this may take a minute)...
    python -m venv .venv
    if !ERRORLEVEL! neq 0 (
        echo [✗ ERROR] Failed to create virtual environment
        echo.
        echo Try:
        echo   python -m venv .venv
        echo.
        pause
        exit /b 1
    )
    echo [✓] Virtual environment created
)
echo.

REM ── 3. Activate virtual environment ────────────────────────
echo [3] Activating virtual environment...
call .venv\Scripts\activate.bat
if !ERRORLEVEL! neq 0 (
    echo [✗ ERROR] Failed to activate virtual environment
    pause
    exit /b 1
)
echo [✓] Virtual environment activated
echo.

REM ── 4. Upgrade pip ──────────────────────────────────────────
echo [4] Upgrading pip...
python -m pip install --upgrade pip --quiet 2>nul
if !ERRORLEVEL! neq 0 (
    echo [!] pip upgrade had warnings (continuing)
) else (
    echo [✓] pip upgraded
)
echo.

REM ── 5. Install requirements ────────────────────────────────
echo [5] Installing dependencies from requirements.txt...
if not exist "requirements.txt" (
    echo [✗ ERROR] requirements.txt not found
    echo Make sure you're in the little_greed directory
    pause
    exit /b 1
)

pip install -r requirements.txt --quiet
if !ERRORLEVEL! neq 0 (
    echo.
    echo [✗ ERROR] Failed to install requirements
    echo.
    echo Run manually to see the error:
    echo   pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
echo [✓] Dependencies installed
echo.

REM ── 6. Create/check .env file ───────────────────────────────
echo [6] Checking .env configuration file...
if not exist ".env" (
    echo [*] Creating .env with defaults...
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
    echo [✓] .env created with template values
    echo [!] IMPORTANT: Edit .env and fill in your settings
) else (
    echo [✓] .env already exists
)
echo.

REM ── 7. Create required directories ─────────────────────────
echo [7] Creating required directories...
if not exist "logs" mkdir logs & echo [✓] logs/
if not exist "logs\archive" mkdir logs\archive & echo [✓] logs/archive/
if not exist "static" mkdir static & echo [✓] static/
if not exist "templates" mkdir templates & echo [✓] templates/
if not exist "reports" mkdir reports & echo [✓] reports/
echo.

REM ── 8. Test IBKR connection ────────────────────────────────
echo [8] Testing IBKR/TWS connectivity...
echo [*] Attempting connection to 127.0.0.1:7497 (timeout 3 seconds)...

powershell -Command "
    \$client = New-Object System.Net.Sockets.TcpClient
    \$async = \$client.BeginConnect('127.0.0.1', 7497, \$null, \$null)
    \$wait = \$async.AsyncWaitHandle.WaitOne([timespan]::FromSeconds(3))
    if (\$wait) {
        try {
            \$client.EndConnect(\$async)
            \$client.Close()
            Write-Host 'OK'
        } catch {
            Write-Host 'FAIL'
        }
    } else {
        \$client.Close()
        Write-Host 'TIMEOUT'
    }
" > ibkr_result.tmp 2>&1

set IBKR_RESULT=FAIL
for /f %%i in (ibkr_result.tmp) do set IBKR_RESULT=%%i
del /q ibkr_result.tmp 2>nul

if "!IBKR_RESULT!"=="OK" (
    echo [✓] IBKR/TWS connection successful
    echo.
) else (
    echo [!] IBKR/TWS connection failed
    echo.
    echo Make sure:
    echo   1. TWS or IB Gateway is INSTALLED on this machine
    echo      Download: https://www.interactivebrokers.com/en/trading/tws.php
    echo   2. TWS/IB Gateway is RUNNING and LOGGED IN
    echo   3. API is ENABLED:
    echo      Edit menu ^> Settings ^> API ^> Settings
    echo        - Check "Enable ActiveX and Socket Clients"
    echo        - Socket port should be: 7497
    echo   4. Port 7497 is not blocked by firewall
    echo.
    echo After you set up TWS, re-run this installer.
    echo.
    pause
    exit /b 1
)

REM ── 9. Success message ──────────────────────────────────────
cls
echo.
echo ============================================================
try (
    echo ╔══════════════════════════════════════════╗
    echo ║  Installation Complete!  Bot is Ready.   ║
    echo ╚══════════════════════════════════════════╝
) catch (
    echo ============================================================
    echo          Installation Complete! Bot is Ready.
    echo ============================================================
)
echo.
echo Next steps:
echo.
echo   1. Edit .env with your settings (optional):
echo      - TELEGRAM_BOT_TOKEN (for notifications)
echo      - TELEGRAM_CHAT_ID
echo.
echo   2. Start the bot:
echo      autostart.bat
echo.
echo   3. Open dashboard:
echo      http://localhost:8000
echo.
echo   4. Run preflight checks:
echo      http://localhost:8000/preflight
echo.
echo.
pause
