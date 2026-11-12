@echo off
REM Double-click to start Simplified QuickBooks in your browser (Windows).
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run.py %*
) else (
  python run.py %*
)
if errorlevel 1 (
  echo.
  echo Simplified QuickBooks could not start. It needs Python 3.9 or newer:
  echo https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^)
)
pause
