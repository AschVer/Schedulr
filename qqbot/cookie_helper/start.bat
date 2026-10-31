@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python environment...
    python -m venv .venv
    if errorlevel 1 goto error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto error
)

".venv\Scripts\python.exe" -c "import selenium" >nul 2>&1
if errorlevel 1 (
    echo Installing Selenium...
    ".venv\Scripts\pip.exe" install -r requirements.txt
    if errorlevel 1 goto error
)

start "" ".venv\Scripts\pythonw.exe" cookie_helper.py
exit /b 0

:error
echo.
echo Install failed: make sure Python 3.10+ is installed and "python" is in PATH.
pause
exit /b 1
