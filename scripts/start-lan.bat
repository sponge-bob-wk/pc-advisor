@echo off
chcp 65001 >nul
cd /d "%~dp0..\tools\agent"
if "%ACCESS_CODE%"=="" set ACCESS_CODE=change-me
echo ============================================
echo  PC Advisor - LAN access
echo  Access code: %ACCESS_CODE%
echo  Share: http://YOUR-LAN-IP:7860/?k=%ACCESS_CODE%
echo ============================================
python server.py --lan
pause
