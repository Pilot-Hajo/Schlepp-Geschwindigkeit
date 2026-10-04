@echo off
chcp 65001 >nul
echo === Mitlesen (Beenden mit Strg + C) ===
echo.
"%USERPROFILE%\.platformio\penv\Scripts\python.exe" -m serial.tools.miniterm --raw %1 115200
