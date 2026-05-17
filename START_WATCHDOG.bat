@echo off
title ParanoidSignals - Watchdog
cd /d D:\BotTrading
echo ============================================
echo   ParanoidSignals - Watchdog + Discord Control
echo   Bot จะเริ่มทำงานอัตโนมัติ
echo   Ctrl+C เพื่อปิดทุกอย่าง
echo ============================================
echo.
.venv\Scripts\python.exe watchdog.py
pause
