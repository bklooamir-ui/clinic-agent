@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting server... Do NOT close this window.
echo If the browser shows an error at first, wait 2 seconds and refresh.
start "" http://127.0.0.1:8000
py server.py
if errorlevel 1 python server.py
echo.
echo ---- Server stopped or failed. Read the message above. ----
pause
