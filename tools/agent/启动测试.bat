@echo off
chcp 65001 >nul
title 电脑选机助手
cd /d "E:\电脑涨价分析\tools\agent"

echo.
echo   电脑选机助手 · 测试版
echo   ----------------------------------------
echo   需要先确认 Ollama 在运行
echo.

curl -s --max-time 3 http://127.0.0.1:11434/api/version >nul 2>&1
if errorlevel 1 (
    echo   [!] Ollama 没有响应，正在尝试启动...
    start "" "C:\Users\23575\AppData\Local\Programs\Ollama\ollama app.exe"
    timeout /t 6 >nul
)

echo   浏览器将自动打开 http://127.0.0.1:7860
echo   关掉这个窗口即停止服务
echo.
start "" http://127.0.0.1:7860
python server.py
pause
