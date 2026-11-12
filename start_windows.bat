@echo off
REM Double-click to start Simplified QuickBooks in your browser (Windows).
cd /d "%~dp0"
where python >nul 2>nul && (python run.py %*) || (py -3 run.py %*)
pause
