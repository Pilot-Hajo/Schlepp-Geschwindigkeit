@echo off
chcp 65001 >nul
echo === Sender flashen: Dauerbetrieb (kein Schlafmodus) ===
echo.
"%USERPROFILE%\.platformio\penv\Scripts\python.exe" "%~dp0flashen.py" sender-dauerbetrieb.bin sender
echo.
pause
