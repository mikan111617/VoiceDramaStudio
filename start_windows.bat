@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem Keep this batch file ASCII-only for reliable Windows cmd.exe parsing.
rem The app supports Unicode; only the bootstrap messages are English.

cd /d "%~dp0"
if errorlevel 1 goto path_error
if not exist "app.py" goto files_error
if not exist "requirements.txt" goto files_error

set "VDS_PYTHON=.venv\Scripts\python.exe"
if exist "%VDS_PYTHON%" goto python_ready

echo [VoiceDrama Studio] Setting up the Python environment...
where py >nul 2>&1
if errorlevel 1 goto try_python
py -3 -m venv ".venv"
if not errorlevel 1 goto venv_created

:try_python
where python >nul 2>&1
if errorlevel 1 goto python_error
python -m venv ".venv"
if errorlevel 1 goto python_error

:venv_created
if not exist "%VDS_PYTHON%" goto python_error

:python_ready
if exist ".venv\.vds_ready" goto start_app
echo [VoiceDrama Studio] Installing app dependencies...
"%VDS_PYTHON%" -m pip install -r "requirements.txt"
if errorlevel 1 goto pip_error
type nul > ".venv\.vds_ready"

:start_app
set "PYTHONUTF8=1"
echo [VoiceDrama Studio] Starting local server...
echo [VoiceDrama Studio] If the browser does not open, visit http://127.0.0.1:8765
echo [VoiceDrama Studio] Keep this window open while using the app.
"%VDS_PYTHON%" "app.py"
if errorlevel 1 goto app_error
goto end

:path_error
echo [ERROR] Could not change to the application directory.
echo         Move the extracted app to a local folder such as C:\VoiceDramaStudio.
goto failure

:files_error
echo [ERROR] app.py or requirements.txt was not found.
echo         Extract ALL files from the ZIP before running this batch file.
echo         Do not run start_windows.bat from inside the ZIP preview.
goto failure

:python_error
echo [ERROR] Python 3.10 or newer is required to create the environment.
echo         Install Python from https://www.python.org/downloads/
echo         Select the option to add Python to PATH, then retry.
goto failure

:pip_error
echo [ERROR] Package installation failed.
echo         Check your internet connection and the pip errors above.
goto failure

:app_error
echo [ERROR] The app exited with an error. See messages above.
echo         If needed, report these messages for diagnosis.
goto failure

:failure
pause
exit /b 1

:end
echo [VoiceDrama Studio] Server stopped.
pause
exit /b 0
