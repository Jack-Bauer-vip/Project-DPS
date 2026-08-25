@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  B 行情刷新并发包（D 中台 → fund_daily.csv）
echo  时间：%date% %time%
echo ============================================
.venv\Scripts\python.exe scripts\refresh_fund_daily.py --publish
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
    echo [OK] 行情刷新 + 发包完成，可查看 B 桌面第 7/8 tab
) else (
    echo [失败] 返回码=%RC%，见上方日志
)
echo.
pause
endlocal & exit /b %RC%
