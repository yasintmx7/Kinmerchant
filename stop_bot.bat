@echo off
echo Stopping Kintara Merchant Alert Bot...
taskkill /f /im python.exe /t 2>nul
taskkill /f /im pythonw.exe /t 2>nul
echo Bot stopped.
pause
