@echo off
title League Commissioner Desk
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   Commissioner's Desk failed to start.
  echo   The project virtual environment is missing:
  echo     %~dp0.venv\Scripts\python.exe
  echo   Recreate it per README.md, then double-click this launcher again.
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" "scripts\launch_desk.py"
if errorlevel 1 (
  echo.
  echo   Commissioner's Desk exited with an error.
  echo   Startup log: %~dp0logs\desk-startup.log
  echo.
  pause
)
endlocal
