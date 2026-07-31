@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo 未找到项目虚拟环境：%PYTHON%
    echo 请先在 qteasy_lab 目录创建 .venv 并安装项目依赖。
    pause
    exit /b 1
)

echo 正在启动 ETF/股票投前研究桌面版...
"%PYTHON%" -B -m qteasy_research.desktop
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo 桌面版异常退出，错误代码：%EXIT_CODE%
    echo 上面的 Python 错误信息可用于排查问题。
    pause
)
exit /b %EXIT_CODE%
