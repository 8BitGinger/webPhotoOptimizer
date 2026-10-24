@echo off
cd /d "%~dp0"

set PY=python
where python >nul 2>nul || set PY=py

%PY% -c "import PIL, tqdm, tkinter" >nul 2>nul
if errorlevel 1 (
    echo Could not start: Python, tkinter, or the required packages are missing.
    echo Install Python 3.9+ from python.org, then run:
    echo     pip install pillow tqdm imageio-ffmpeg
    echo.
    pause
    exit /b 1
)

REM pythonw runs the GUI without leaving a console window open
start "" %PY%w gui.py
