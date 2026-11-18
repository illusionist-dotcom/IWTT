@echo off
setlocal
cd /d "%~dp0"

rem The built application is the way to open IWTT. The Python source
rem checkout is only a fallback for when no build exists yet.
if exist "dist\IWTT-Thought-Communities.exe" (
  "dist\IWTT-Thought-Communities.exe"
  goto :end
)

echo No build found; starting from source instead.
python scripts\launch_iwtt.py
if errorlevel 1 (
  echo.
  echo IWTT could not start. Install Python 3.10 or newer, then try again.
  pause
)

:end
