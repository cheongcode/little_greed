@echo off
REM Stop the little_greed bot cleanly (Windows)

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo Stopping little_greed...
echo.

REM Kill run.py process
taskkill /F /FI "WINDOWTITLE eq run.py*" 2>nul
if !ERRORLEVEL! equ 0 (
    echo [✓] Stopped run.py
) else (
    REM Try killing by module name (alternative method)
    tasklist | find /i "python.exe" >nul
    if !ERRORLEVEL! equ 0 (
        REM This is risky - only do if needed
        REM taskkill /F /IM python.exe 2>nul >nul
        echo [*] No running processes found
    )
)

REM Kill uvicorn
taskkill /F /FI "WINDOWTITLE eq uvicorn*" 2>nul
if !ERRORLEVEL! equ 0 (
    echo [✓] Stopped uvicorn
)

REM Clean up PID file
if exist ".run_pid" (
    del /q .run_pid
)

echo.
echo [✓] little_greed stopped cleanly.
echo.
pause
