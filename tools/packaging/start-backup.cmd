@echo off
rem Starts Breathalyzer without Start Breathalyzer.exe: for when an antivirus holds that file back.
rem It does what Start Breathalyzer.exe does: app\runtime\pythonw.exe -I app\tools\launcher.py (or modman_gui.py).
setlocal
set "APP=%~dp0"
set "SCRIPT=%APP%tools\launcher.py"
if not exist "%SCRIPT%" set "SCRIPT=%APP%tools\modman_gui.py"
if not exist "%APP%runtime\pythonw.exe" goto missing
if not exist "%SCRIPT%" goto missing
cd /d "%APP%"
start "" "%APP%runtime\pythonw.exe" -I "%SCRIPT%" %*
exit /b 0
:missing
echo Breathalyzer can't find its files. Unpack the whole zip again, then open Start Breathalyzer.
pause
exit /b 1
