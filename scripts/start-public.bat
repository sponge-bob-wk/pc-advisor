@echo off
chcp 65001 >nul
cd /d "%~dp0..\tools\agent"
if "%ACCESS_CODE%"=="" set ACCESS_CODE=change-me
echo ============================================
echo  PC Advisor - public link via Cloudflare Tunnel
echo  Access code: %ACCESS_CODE%
echo  The tunnel URL will be printed below.
echo  Send people:  https://xxxx.trycloudflare.com/?k=%ACCESS_CODE%
echo  NOTE: closing this window or sleeping the PC kills the link.
echo ============================================
start "pc-advisor-server" python server.py --lan
timeout /t 3 >nul
cloudflared tunnel --url http://127.0.0.1:7860
pause
