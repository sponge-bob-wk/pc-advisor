@echo off
rem Double-click -> stop server AND tunnel, free port 7860
call "%~dp0scripts\launcher.bat" stop
echo.
echo   Done. Press any key to close.
pause >nul