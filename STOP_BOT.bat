@echo off
title Stop Bot
cd /d D:\BotTrading
echo Stopping bot...

REM ── ปิด Watchdog + ลูก process ทั้งหมด (/T = kill tree) ─────────────
if exist watchdog.lock (
    set /p WD_PID=<watchdog.lock
    taskkill /PID %WD_PID% /T /F >nul 2>&1
    echo [OK] Watchdog + Bot stopped ^(PID %WD_PID%^)
) else (
    echo [--] Watchdog not running
)

REM ── ปิด Bot โดยตรง กรณีรันโดยไม่มี watchdog ─────────────────────────
if exist live_bot.lock (
    set /p BOT_PID=<live_bot.lock
    taskkill /PID %BOT_PID% /T /F >nul 2>&1
    echo [OK] Bot stopped ^(PID %BOT_PID%^)
) else (
    echo [--] Bot not running without watchdog
)

REM ── รอให้ process ตาย ─────────────────────────────────────────────────
echo Waiting for processes to exit...
timeout /t 3 /nobreak >nul

REM ── ลบ lock files ────────────────────────────────────────────────────
if exist watchdog.lock (
    del watchdog.lock >nul 2>&1
    echo [OK] Removed watchdog.lock
)
if exist live_bot.lock (
    del live_bot.lock >nul 2>&1
    echo [OK] Removed live_bot.lock
)

echo.
echo Done.
timeout /t 2 >nul
