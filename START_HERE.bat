@echo off
title TravelAI Pro
cd /d "%~dp0"
echo.
echo  Starting TravelAI Pro...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_windows.ps1"
pause
