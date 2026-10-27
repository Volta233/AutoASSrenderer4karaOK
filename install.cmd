@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "AIKTV_INSTALL_EXIT=%ERRORLEVEL%"
if not "%AIKTV_INSTALL_EXIT%"=="0" pause
exit /b %AIKTV_INSTALL_EXIT%
