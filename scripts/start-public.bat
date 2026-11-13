@echo off
chcp 65001 >nul
cd /d "%~dp0..\tools\agent"
if "%ACCESS_CODE%"=="" set ACCESS_CODE=change-me

rem Cloudflare 临时隧道：给外网的人一个 https 链接。
rem cloudflared 刚装完要新开窗口才进 PATH，所以这里兜一个常见安装路径。
set "CF=cloudflared"
where cloudflared >nul 2>nul
if errorlevel 1 set "CF=C:\Program Files (x86)\cloudflared\cloudflared.exe"
if not exist "%CF%" if /i not "%CF%"=="cloudflared" (
  echo [!] cloudflared not found. Install it first:
  echo     winget install --id Cloudflare.cloudflared -e --source winget
  pause
  exit /b 1
)

echo ============================================
echo  PC Advisor - public link via Cloudflare Tunnel
echo  Access code: %ACCESS_CODE%
echo  A https://xxxx.trycloudflare.com URL will appear below.
echo  Share it as:  https://xxxx.trycloudflare.com/?k=%ACCESS_CODE%
echo  NOTE: closing this window or sleeping the PC kills the link.
echo ============================================
start "pc-advisor-server" python server.py --lan
timeout /t 3 >nul
"%CF%" tunnel --url http://127.0.0.1:7860
pause
