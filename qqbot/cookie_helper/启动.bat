@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo 首次运行，正在创建 Python 环境...
    python -m venv .venv
    if errorlevel 1 goto error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto error
)

".venv\Scripts\python.exe" -c "import selenium" >nul 2>&1
if errorlevel 1 (
    echo 正在安装 Selenium...
    ".venv\Scripts\pip.exe" install -r requirements.txt
    if errorlevel 1 goto error
)

start "" ".venv\Scripts\pythonw.exe" cookie_helper.py
exit /b 0

:error
echo.
echo 安装失败：请确认已安装 Python 3.10+，并且 python 命令在 PATH 里。
pause
exit /b 1
