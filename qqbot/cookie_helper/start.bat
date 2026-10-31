@echo off
chcp 65001 >nul
cd /d "%~dp0"

python -c "import selenium" >nul 2>&1
if errorlevel 1 (
    echo Installing Selenium once...
    python -m pip install selenium
    if errorlevel 1 goto error
)

where pythonw >nul 2>&1
if errorlevel 1 (
    start "" python cookie_helper.py
) else (
    start "" pythonw cookie_helper.py
)
exit /b 0

:error
echo.
echo Failed: install Python 3.10+ and make sure "python" is in PATH.
pause
exit /b 1
