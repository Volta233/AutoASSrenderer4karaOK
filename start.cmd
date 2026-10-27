@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "AIKTV_START_EXIT=%ERRORLEVEL%"
if not "%AIKTV_START_EXIT%"=="0" pause
exit /b %AIKTV_START_EXIT%
