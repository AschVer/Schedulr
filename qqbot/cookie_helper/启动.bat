@echo off
chcp 65001 >nul
cd /d "%~dp0"

python -c "import selenium" >nul 2>&1
if errorlevel 1 (
    echo 正在安装 Selenium（只需一次）...
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
echo 启动失败：请确认已安装 Python 3.10+，并且 python 命令在 PATH 里。
pause
exit /b 1
