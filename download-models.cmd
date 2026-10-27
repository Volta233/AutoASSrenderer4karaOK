@echo off
cd /d "%~dp0"
set "PYTHONUTF8=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0download-models.ps1" %*
set "DOWNLOAD_EXIT=%ERRORLEVEL%"
if not "%DOWNLOAD_EXIT%"=="0" pause
exit /b %DOWNLOAD_EXIT%
