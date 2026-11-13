@echo off
rem ===================================================================
rem  PC Advisor launcher (thin wrapper)
rem  All real logic lives in tools\launcher.py -- this file only finds
rem  Python and starts it.
rem
rem  Keep this file ASCII: cmd.exe codepage games make UTF-8 Chinese in
rem  .bat unreliable, and the error lines are exactly the ones you need
rem  to read.
rem ===================================================================
setlocal

rem Project root = parent of this scripts\ folder. Derive it, never rely
rem on the caller's working directory -- double-click, shortcut, and
rem command line all give a different cwd.
set "ROOT=%~dp0.."
pushd "%ROOT%" || (echo   [!] cannot enter project folder & pause & exit /b 1)
chcp 65001 >nul 2>nul

rem Prefer the py launcher, fall back to python on PATH.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo   [!] Python not found on PATH.
  echo       Install Python 3.10+ and make sure "Add to PATH" is checked.
  echo.
  popd
  pause
  exit /b 1
)

set "LP=%~dp0..\tools\launcher.py"
if not exist "%LP%" (
  echo.
  echo   [!] tools\launcher.py is missing.
  echo       Expected at: %LP%
  echo.
  popd
  pause
  exit /b 1
)

rem -u: unbuffered. Without it, redirecting output to a file leaves it
rem empty until exit -- and the launcher prints progress you need live.
%PY% -u "%LP%" %*
set "RC=%ERRORLEVEL%"

popd
if not "%RC%"=="0" (
  echo.
  echo   [!] launcher exited with code %RC%. See messages above.
  pause
)
exit /b %RC%