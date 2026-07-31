@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" goto missing_python

echo Starting ETF Research Desk...
"%PYTHON%" -B -m qteasy_research.desktop
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo Desktop app exited with code %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%

:missing_python
echo Project virtual environment was not found:
echo %PYTHON%
pause
exit /b 1
