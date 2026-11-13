@echo off
rem ===================================================================
rem  Create a Desktop shortcut for the launcher.
rem
rem  Why a separate file instead of doing it automatically: writing to
rem  your Desktop is a change outside the project folder. That is your
rem  call, not mine -- so this only runs when you double-click it.
rem
rem  To remove it later: just delete the shortcut from the Desktop.
rem ===================================================================
setlocal
chcp 65001 >nul 2>nul
set "ROOT=%~dp0.."
pushd "%ROOT%"

set "TARGET=%ROOT%\启动助手.bat"
set "ICON=%SystemRoot%\System32\shell32.dll"

if not exist "%TARGET%" (
  echo   [!] Cannot find 启动助手.bat
  popd
  pause
  exit /b 1
)

echo.
echo   Creating shortcut on Desktop...
echo   Target: %TARGET%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$d = [Environment]::GetFolderPath('Desktop');" ^
  "$s = $ws.CreateShortcut((Join-Path $d '启动电脑选机助手.lnk'));" ^
  "$s.TargetPath = '%TARGET%';" ^
  "$s.WorkingDirectory = '%ROOT%';" ^
  "$s.IconLocation = '%ICON%,13';" ^
  "$s.Description = '电脑选机助手 - 启动服务并生成测试链接';" ^
  "$s.Save();" ^
  "Write-Output ('  OK -> ' + (Join-Path $d '启动电脑选机助手.lnk'))"

if errorlevel 1 (
  echo.
  echo   [!] Shortcut creation failed.
  echo       You can still double-click 启动助手.bat directly.
) else (
  echo.
  echo   Done. Look for "启动电脑选机助手" on your Desktop.
)

popd
echo.
pause