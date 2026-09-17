@echo off
REM Bypass local ExecutionPolicy so this can run on default Windows PowerShell.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack-desktop.ps1" %*
exit /b %ERRORLEVEL%
