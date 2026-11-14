@echo off
setlocal
cd /d "%~dp0"
python scripts\launch_iwtt.py
if errorlevel 1 (
  echo.
  echo IWTT could not start. Install Python 3.10 or newer, then try again.
  pause
)
