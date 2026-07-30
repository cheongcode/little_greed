@echo off
REM Stop the little_greed bot cleanly (Windows)

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo              Stopping little_greed
echo ============================================================
echo.

set KILLED=0

REM ── 1. Kill process using port 8000 (the web server) ────────
echo [*] Looking for processes using port 8000...
for /f "tokens=5" %%A in ('netstat -ano 2^>nul ^| find ":8000"') do (
    echo [*] Found PID %%A on port 8000
    taskkill /F /PID %%A >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo [✓] Stopped process %%A
        set KILLED=1
    )
)

REM ── 2. Kill any python.exe running run.py ──────────────────
echo [*] Looking for run.py processes...
wmic process where name="python.exe" get processid 2>nul | findstr . >procs.tmp
for /f %%A in (procs.tmp) do (
    taskkill /F /PID %%A >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo [✓] Stopped Python process %%A
        set KILLED=1
    )
)
del /q procs.tmp 2>nul

REM ── 3. Clean up .run_pid file ───────────────────────────────
if exist ".run_pid" (
    del /q .run_pid
    echo [✓] Cleaned up .run_pid
)

echo.
if !KILLED! equ 1 (
    echo [✓] little_greed stopped
) else (
    echo [!] No processes found (bot might already be stopped)
)
echo.

pause
