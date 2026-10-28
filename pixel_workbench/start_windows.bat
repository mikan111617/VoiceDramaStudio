@echo off
setlocal
cd /d "%~dp0"
echo === PixelLab + GPT Workbench v0.7 ===
if exist ".venv\Scripts\python.exe" goto :install
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 (
    echo Python 3.10 or later is required. Install Python from python.org.
    pause
    exit /b 1
)
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install required packages. Check your internet connection.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
