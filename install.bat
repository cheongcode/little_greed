@echo off
REM ─────────────────────────────────────────────────────────────────
REM little_greed installer for Windows
REM Run this once. It creates venv, installs packages, tests IBKR connection.
REM ─────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion

REM Check Python version
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Python not found. Download from https://python.org/downloads/
    echo [ERROR] Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    if %%a LSS 3 (
        echo [ERROR] Python 3.12+ required. You have %PYVER%.
        pause
        exit /b 1
    )
    if %%a EQU 3 if %%b LSS 12 (
        echo [ERROR] Python 3.12+ required. You have %PYVER%.
        pause
        exit /b 1
    )
)

cls
echo.
echo ╔══════════════════════════════════════════════╗
echo ║     little_greed installer (Windows)         ║
echo ║     Python %PYVER%                             ║
echo ╚══════════════════════════════════════════════╝
echo.

REM Create venv
if not exist ".venv" (
    echo [*] Creating virtual environment...
    python -m venv .venv
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
    echo [✓] Virtual environment created
) else (
    echo [✓] Virtual environment already exists
)

REM Activate venv
call .venv\Scripts\activate.bat
if !ERRORLEVEL! neq 0 (
    echo [ERROR] Failed to activate venv.
    pause
    exit /b 1
)
echo [✓] Virtual environment activated

REM Upgrade pip
echo [*] Upgrading pip...
python -m pip install --upgrade pip -q
echo [✓] pip upgraded

REM Install requirements
echo [*] Installing dependencies from requirements.txt...
if exist requirements.txt (
    pip install -r requirements.txt -q
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] Failed to install requirements.
        echo Check requirements.txt and internet connection.
        pause
        exit /b 1
    )
    echo [✓] Dependencies installed
) else (
    echo [ERROR] requirements.txt not found.
    pause
    exit /b 1
)

REM Create .env if missing
if not exist ".env" (
    echo.
    echo [!] .env file not found. Creating template...
    (
        echo IBKR_HOST=127.0.0.1
        echo IBKR_PORT=7497
        echo IBKR_CLIENT_ID=90
        echo TELEGRAM_BOT_TOKEN=your_token_here
        echo TELEGRAM_CHAT_ID=your_chat_id_here
    ) > .env
    echo [✓] .env template created
    echo [!] IMPORTANT: Edit .env and fill in your IBKR/Telegram credentials
)

REM Test IBKR connection
echo.
echo [*] Testing IBKR connection on port 7497...
for /f "delims=" %%i in ('python -c "import socket; s=socket.socket(); s.settimeout(3); result='ok' if s.connect_ex(('127.0.0.1',7497))==0 else 'fail'; s.close(); print(result)" 2^>nul') do set IBKR_TEST=%%i

if "%IBKR_TEST%"=="ok" (
    echo [✓] IBKR/TWS connection OK
) else (
    echo [!] IBKR/TWS not reachable on port 7497
    echo [!] Start TWS or IB Gateway, log in, and enable the API.
    echo [!] Then re-run this script.
    echo.
    pause
    exit /b 1
)

REM Create log directories
if not exist logs mkdir logs
if not exist logs\archive mkdir logs\archive
echo [✓] Log directories created

echo.
echo ╔══════════════════════════════════════════════╗
echo ║  Installation complete!                      ║
echo ║                                              ║
echo ║  Next steps:                                 ║
echo ║  1. Edit .env with your IBKR credentials    ║
echo ║  2. Run autostart.bat to start trading       ║
echo ╚══════════════════════════════════════════════╝
echo.
pause
