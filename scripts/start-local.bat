@echo off
chcp 65001 >nul
cd /d "%~dp0..\tools\agent"
echo ============================================
echo  PC Advisor - local only
echo  Open http://127.0.0.1:7860
echo ============================================
python server.py
pause
