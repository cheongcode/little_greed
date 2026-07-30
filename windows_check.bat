@echo off
REM =========================================================
REM windows_check.bat - Diagnostic system check for Windows
REM ASCII-only, no special Unicode characters
REM =========================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

cls
echo.
echo =========================================================
echo              Windows System Check
echo =========================================================
echo.

set PASS=0
set FAIL=0

REM --- 1. Python ---
echo [1] Python installation...
python --version >nul 2>&1
if !ERRORLEVEL! equ 0 (
    for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
    echo     [PASS] Python !PYVER!
    set /a PASS=!PASS!+1
) else (
    echo     [FAIL] Python not found
    echo     Fix: Download Python 3.12+ from python.org
    set /a FAIL=!FAIL!+1
)
echo.

REM --- 2. Virtual environment ---
echo [2] Virtual environment...
if exist ".venv\Scripts\python.exe" (
    echo     [PASS] .venv exists
    set /a PASS=!PASS!+1
) else (
    echo     [FAIL] .venv not found
    echo     Fix: Run python install.bat
    set /a FAIL=!FAIL!+1
)
echo.

REM --- 3. Required files ---
echo [3] Required files...
set FILE_FAIL=0
if exist "run.py" (
    echo     [PASS] run.py
) else (
    echo     [FAIL] run.py missing
    set FILE_FAIL=1
)
if exist "cycle.py" (
    echo     [PASS] cycle.py
) else (
    echo     [FAIL] cycle.py missing
    set FILE_FAIL=1
)
if exist ".env" (
    echo     [PASS] .env
) else (
    echo     [FAIL] .env missing
    echo     Fix: Run python install.bat
    set FILE_FAIL=1
)
if !FILE_FAIL! equ 0 (
    set /a PASS=!PASS!+1
) else (
    set /a FAIL=!FAIL!+1
)
echo.

REM --- 4. Directories ---
echo [4] Directories...
set DIR_FAIL=0
if not exist "templates" mkdir templates & set DIR_FAIL=1
if not exist "static" mkdir static & set DIR_FAIL=1
if not exist "logs" mkdir logs & set DIR_FAIL=1
if !DIR_FAIL! equ 0 (
    echo     [PASS] All directories exist
    set /a PASS=!PASS!+1
) else (
    echo     [PASS] Created missing directories
    set /a PASS=!PASS!+1
)
echo.

REM --- 5. Python packages ---
echo [5] Python packages...
if exist ".venv\Scripts\python.exe" (
    set PKG_FAIL=0
    .venv\Scripts\python -c "import fastapi" >nul 2>&1
    if !ERRORLEVEL! neq 0 set PKG_FAIL=1
    .venv\Scripts\python -c "import uvicorn" >nul 2>&1
    if !ERRORLEVEL! neq 0 set PKG_FAIL=1
    .venv\Scripts\python -c "import ib_async" >nul 2>&1
    if !ERRORLEVEL! neq 0 set PKG_FAIL=1

    if !PKG_FAIL! equ 0 (
        echo     [PASS] fastapi, uvicorn, ib_async installed
        set /a PASS=!PASS!+1
    ) else (
        echo     [FAIL] Some packages missing
        echo     Fix: pip install -r requirements.txt
        set /a FAIL=!FAIL!+1
    )
) else (
    echo     [SKIP] Cannot check (no venv)
    set /a PASS=!PASS!+1
)
echo.

REM --- 6. IBKR connectivity ---
echo [6] IBKR connection...
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

set IBKR_RESULT=FAIL
for /f %%i in (ibkr_check.tmp) do set IBKR_RESULT=%%i
del /q ibkr_check.tmp 2>nul

if "!IBKR_RESULT!" == "OK" (
    echo     [PASS] IBKR on localhost:7497
    set /a PASS=!PASS!+1
) else (
    echo     [FAIL] IBKR not reachable
    echo     Fix: Start TWS/IB Gateway, log in, enable API
    set /a FAIL=!FAIL!+1
)
echo.

REM --- 7. Port 8000 ---
echo [7] Port 8000 availability...
netstat -ano 2>nul | find ":8000" >nul
if !ERRORLEVEL! equ 0 (
    echo     [WARN] Port 8000 in use
    set /a PASS=!PASS!+1
) else (
    echo     [PASS] Port 8000 available
    set /a PASS=!PASS!+1
)
echo.

REM --- 8. Disk space ---
echo [8] Disk space...
for /f "usebackq" %%A in (`powershell -Command "
    \$drive = Get-PSDrive C | Select-Object @{Name='Free';Expression={[math]::Round(\$_.Free/1GB,1)}}
    Write-Host \$drive.Free
"`) do set DISK_FREE=%%A

echo     [PASS] %DISK_FREE% GB available
set /a PASS=!PASS!+1
echo.

REM --- Results ---
echo =========================================================
echo                   RESULTS
echo =========================================================
echo.
echo Passed:  %PASS%
echo Failed:  %FAIL%
echo.

if %FAIL% equ 0 (
    echo [OK] All checks passed
    echo Run: start.bat
) else (
    echo [ERROR] Some checks failed
    echo Fix the issues above and try again
)

echo.
pause
