@echo off
echo Installing Kintara Bot to Windows Startup...
set DEST=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\KintaraBot.bat
copy "%CD%\run.bat" "%DEST%" /y
echo.
echo Done! Bot will auto-start on Windows login.
echo To remove auto-start, delete: %DEST%
pause
