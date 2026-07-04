@echo off
echo ============================================
echo  Kintara Merchant Alert Bot
echo  Auto-restart ON. Press Ctrl+C twice to stop.
echo ============================================
echo.
:loop
python bot.py
echo.
echo Bot stopped or crashed. Restarting in 5 sec...
timeout /t 5 /nobreak
goto loop
