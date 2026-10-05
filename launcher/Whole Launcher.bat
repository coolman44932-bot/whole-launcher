@echo off
rem Whole Launcher - starts the mod manager window (files\whole_launcher.py) with Python.
cd /d "%~dp0"
set "PYW="
py -3 -c "import tkinter" >nul 2>nul && set "PYW=pyw -3"
if not defined PYW python -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul && set "PYW=pythonw"
if not defined PYW goto nopython
rem any .zip files dragged onto this .bat are added as mods
start "" %PYW% "files\whole_launcher.py" %*
exit /b 0

:nopython
echo.
echo  Whole Launcher needs Python 3.8 or newer (it is free).
echo.
echo  1. Get it from https://www.python.org/downloads/
echo  2. In the installer, tick "Add python.exe to PATH".
echo  3. Run Whole Launcher.bat again.
echo.
pause
