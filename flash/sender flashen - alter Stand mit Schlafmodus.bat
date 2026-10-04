@echo off
chcp 65001 >nul
echo === Sender flashen: alter Stand MIT Schlafmodus ===
echo.
echo Diese Fassung schaltet nach 3 Minuten ohne Bewegung ab und braucht
echo dann den Reset-Taster. Nur zum Zurueckgehen gedacht.
echo.
"%USERPROFILE%\.platformio\penv\Scripts\python.exe" "%~dp0flashen.py" sender-mit-schlafmodus.bin sender
echo.
pause
