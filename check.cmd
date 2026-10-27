@echo off
cd /d "%~dp0"
if not exist ".runtime\app\Scripts\python.exe" (
  echo Please run install.cmd first. See README.md.
  pause
  exit /b 1
)
set "PYTHONUTF8=1"
".runtime\app\Scripts\python.exe" scripts\doctor.py --json .runtime\doctor.json %*
set "AIKTV_CHECK_EXIT=%ERRORLEVEL%"
echo Report: .runtime\doctor.json
pause
exit /b %AIKTV_CHECK_EXIT%
