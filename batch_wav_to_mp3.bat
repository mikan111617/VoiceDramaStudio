@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem Standalone WAV-to-MP3 converter. Standard-library only; FFmpeg required.
cd /d "%~dp0"
if errorlevel 1 goto fail
if exist ".venv\Scripts\pythonw.exe" (
    ".venv\Scripts\pythonw.exe" "batch_wav_to_mp3.py"
    if not errorlevel 1 exit /b 0
)
where py >nul 2>&1
if not errorlevel 1 (
    py -3 "batch_wav_to_mp3.py"
    if not errorlevel 1 exit /b 0
)
where python >nul 2>&1
if not errorlevel 1 (
    python "batch_wav_to_mp3.py"
    if not errorlevel 1 exit /b 0
)
:fail
echo [ERROR] Could not launch the converter.
echo Python 3.10+ must be installed. FFmpeg is required for audio encoding.
echo Check the Python errors above, then retry.
pause
exit /b 1
