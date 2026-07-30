@echo off
REM =========================================================
REM stop.bat - Stop little_greed bot cleanly
REM ASCII-only, no special Unicode characters
REM =========================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo =========================================================
echo              Stopping little_greed
echo =========================================================
echo.

set FOUND=0

REM --- Kill process on port 8000 ---
echo [*] Stopping dashboard server...
for /f "tokens=5" %%A in ('netstat -ano 2^>nul ^| find ":8000"') do (
    taskkill /F /PID %%A >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo [OK] Stopped PID %%A
        set FOUND=1
    )
)

REM --- Kill Python processes ---
echo [*] Stopping bot processes...
for /f "tokens=2" %%A in ('tasklist /FI "IMAGENAME eq python.exe" /FO csv 2^>nul ^| findstr python') do (
    taskkill /F /IM python.exe >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo [OK] Stopped Python
        set FOUND=1
    )
)

REM --- Clean up PID file ---
if exist ".run_pid" (
    del /q .run_pid 2>nul
    echo [OK] Cleaned .run_pid
)

echo.
if !FOUND! equ 1 (
    echo [OK] Bot stopped
) else (
    echo [INFO] No processes found - bot already stopped
)
echo.

pause
