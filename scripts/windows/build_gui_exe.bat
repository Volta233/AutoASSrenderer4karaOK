@echo off
setlocal
cd /d "%~dp0\..\.."
python scripts\windows\build_gui_exe.py %*
exit /b %ERRORLEVEL%
