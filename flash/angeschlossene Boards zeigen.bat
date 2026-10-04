@echo off
chcp 65001 >nul
"%USERPROFILE%\.platformio\penv\Scripts\python.exe" "%~dp0flashen.py"
echo.
pause
