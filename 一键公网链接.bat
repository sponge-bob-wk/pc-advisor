@echo off
rem Double-click -> one-click public test link (LAN + Cloudflare tunnel)
call "%~dp0scripts\launcher.bat" public
if errorlevel 1 pause