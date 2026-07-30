@echo off
REM ─────────────────────────────────────────────────────────────────
REM windows_check.bat — Comprehensive Windows environment check
REM Run this to diagnose any issues before starting the bot
REM ─────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion
cd /d "%~dp0"

cls
echo.
echo ============================================================
echo           little_greed Windows System Check
echo ============================================================
echo.

set PASS=0
set FAIL=0

REM ── 1. Python installation ──────────────────────────────────
echo [1] Checking Python installation...
python --version >nul 2>&1
if !ERRORLEVEL! equ 0 (
    for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
    echo     [✓] Python !PYVER! installed and in PATH
    set /a PASS=!PASS!+1
) else (
    echo     [✗] Python not found in PATH
    echo        Action: Download Python 3.12+ from https://python.org/downloads
    echo        Make sure to check "Add Python to PATH" during install
    set /a FAIL=!FAIL!+1
)
echo.

REM ── 2. Virtual environment ──────────────────────────────────
echo [2] Checking virtual environment...
if exist ".venv\Scripts\python.exe" (
    echo     [✓] Virtual environment exists at .venv
    set /a PASS=!PASS!+1
) else (
    echo     [✗] Virtual environment not found
    echo        Action: Run: python install.bat
    set /a FAIL=!FAIL!+1
)
echo.

REM ── 3. Required files ───────────────────────────────────────
echo [3] Checking required project files...
set FILE_FAIL=0

if exist "run.py" (
    echo     [✓] run.py
) else (
    echo     [✗] run.py missing
    set /a FILE_FAIL=!FILE_FAIL!+1
)

if exist "cycle.py" (
    echo     [✓] cycle.py
) else (
    echo     [✗] cycle.py missing
    set /a FILE_FAIL=!FILE_FAIL!+1
)

if exist "strategy.py" (
    echo     [✓] strategy.py
) else (
    echo     [✗] strategy.py missing
    set /a FILE_FAIL=!FILE_FAIL!+1
)

if exist ".env" (
    echo     [✓] .env
) else (
    echo     [✗] .env missing
    echo        Action: Run: python install.bat
    set /a FILE_FAIL=!FILE_FAIL!+1
)

if exist "requirements.txt" (
    echo     [✓] requirements.txt
) else (
    echo     [✗] requirements.txt missing
    set /a FILE_FAIL=!FILE_FAIL!+1
)

if !FILE_FAIL! equ 0 (
    set /a PASS=!PASS!+1
) else (
    set /a FAIL=!FAIL!+1
)
echo.

REM ── 4. Required directories ────────────────────────────────
echo [4] Checking required directories...
set DIR_FAIL=0

if exist "templates" (
    echo     [✓] templates/
) else (
    echo     [!] templates/ missing - will be created on first run
    set /a DIR_FAIL=!DIR_FAIL!+1
)

if exist "static" (
    echo     [✓] static/
) else (
    echo     [!] static/ missing - will be created on first run
    set /a DIR_FAIL=!DIR_FAIL!+1
)

if exist "logs" (
    echo     [✓] logs/
) else (
    echo     [!] logs/ missing - will be created on first run
    set /a DIR_FAIL=!DIR_FAIL!+1
)

if !DIR_FAIL! equ 0 (
    set /a PASS=!PASS!+1
) else (
    set /a PASS=!PASS!+1
)
echo.

REM ── 5. Python packages ──────────────────────────────────────
echo [5] Checking Python packages (venv requirements)...
if exist ".venv\Scripts\python.exe" (
    set PIP_FAIL=0

    .venv\Scripts\python -c "import fastapi" >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo     [✓] fastapi
    ) else (
        echo     [✗] fastapi not installed
        set /a PIP_FAIL=!PIP_FAIL!+1
    )

    .venv\Scripts\python -c "import uvicorn" >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo     [✓] uvicorn
    ) else (
        echo     [✗] uvicorn not installed
        set /a PIP_FAIL=!PIP_FAIL!+1
    )

    .venv\Scripts\python -c "import ib_async" >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo     [✓] ib_async
    ) else (
        echo     [✗] ib_async not installed
        set /a PIP_FAIL=!PIP_FAIL!+1
    )

    .venv\Scripts\python -c "import dotenv" >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo     [✓] dotenv
    ) else (
        echo     [✗] dotenv not installed
        set /a PIP_FAIL=!PIP_FAIL!+1
    )

    if !PIP_FAIL! equ 0 (
        set /a PASS=!PASS!+1
    ) else (
        echo.
        echo     Action: Run this to reinstall: pip install -r requirements.txt
        set /a FAIL=!FAIL!+1
    )
) else (
    echo     [!] Cannot check - virtual environment not active
    set /a PASS=!PASS!+1
)
echo.

REM ── 6. IBKR/TWS connectivity ───────────────────────────────
echo [6] Checking IBKR/TWS connectivity on port 7497...

REM Use PowerShell for better connectivity test
powershell -Command "
    \$client = New-Object System.Net.Sockets.TcpClient
    \$result = \$client.ConnectAsync('127.0.0.1', 7497)
    \$timeout = New-Object System.Threading.CancellationTokenSource
    \$timeout.CancelAfter(3000)
    try {
        [System.Threading.Tasks.Task]::WaitAny(@(\$result), \$timeout.Token) | Out-Null
        if (\$result.IsCompleted) {
            \$client.Close()
            Write-Host 'connected'
        } else {
            Write-Host 'timeout'
        }
    }
    catch {
        Write-Host 'failed'
    }
" > ibkr_test.tmp 2>&1

set IBKR_RESULT=failed
for /f %%i in (ibkr_test.tmp) do set IBKR_RESULT=%%i
del /q ibkr_test.tmp 2>nul

if "!IBKR_RESULT!"=="connected" (
    echo     [✓] IBKR/TWS reachable on 127.0.0.1:7497
    set /a PASS=!PASS!+1
) else (
    echo     [✗] IBKR/TWS NOT reachable on 127.0.0.1:7497
    echo.
    echo     Make sure:
    echo       1. TWS or IB Gateway is RUNNING on this machine
    echo       2. You are LOGGED IN with paper trading account
    echo       3. API is ENABLED:
    echo          Edit ^> Settings ^> API ^> Settings
    echo          Check "Enable ActiveX and Socket Clients"
    echo          Socket port: 7497
    echo       4. No firewall is blocking localhost:7497
    echo.
    set /a FAIL=!FAIL!+1
)
echo.

REM ── 7. Port availability ────────────────────────────────────
echo [7] Checking port availability (8000 for dashboard)...

netstat -ano 2>nul | find ":8000" >nul
if !ERRORLEVEL! equ 0 (
    echo     [!] Port 8000 already in use (might be OK if bot is running)
    set /a PASS=!PASS!+1
) else (
    echo     [✓] Port 8000 available for dashboard
    set /a PASS=!PASS!+1
)
echo.

REM ── 8. .env configuration ───────────────────────────────────
echo [8] Checking .env configuration...

if exist ".env" (
    for /f "usebackq tokens=1,2 delims==" %%A in (".env") do (
        if "%%A"=="IBKR_PORT" (
            echo     [✓] IBKR_PORT=%%B
        )
        if "%%A"=="TELEGRAM_BOT_TOKEN" (
            if not "%%B"=="" (
                echo     [✓] TELEGRAM_BOT_TOKEN set
            ) else (
                echo     [!] TELEGRAM_BOT_TOKEN empty (optional)
            )
        )
    )
    set /a PASS=!PASS!+1
) else (
    echo     [✗] .env not found
    set /a FAIL=!FAIL!+1
)
echo.

REM ── 9. Disk space ───────────────────────────────────────────
echo [9] Checking disk space...

for /f "usebackq" %%A in (`powershell -Command "
    \$drive = Get-PSDrive C | Select-Object @{Name='Free(GB)';Expression={[math]::Round(\$_.Free/1GB,1)}}
    Write-Host \$drive.'Free(GB)'
"`) do set DISK_FREE=%%A

echo     [✓] Free disk space: %DISK_FREE% GB

if %DISK_FREE% GTR 5 (
    set /a PASS=!PASS!+1
) else (
    echo     [!] Low disk space (less than 5 GB free)
    set /a FAIL=!FAIL!+1
)
echo.

REM ── 10. Summary ─────────────────────────────────────────────
echo ============================================================
echo                       RESULTS
echo ============================================================
echo.
echo Passed:     [%PASS%]
echo Failed:     [%FAIL%]
echo.

if %FAIL% equ 0 (
    echo [✓] All checks passed! You can run: autostart.bat
) else (
    echo [✗] Some checks failed. Fix the issues above and try again.
    echo.
    echo Common fixes:
    echo   - Install Python 3.12+ and add to PATH
    echo   - Run: python install.bat (to set up everything)
    echo   - Start TWS/IB Gateway and log in
    echo   - Enable API in TWS settings
)

echo.
pause
