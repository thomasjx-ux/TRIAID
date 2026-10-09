@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "TRIAID_STORAGE_BACKEND=file"
set "TRIAID_DATA_DIR=%~dp0TRIAID_FIN_LOCAL_DATA"
set "TRIAID_RUNTIME_ROLE=LOCAL_STANDALONE"
set "TRIAID_PERSISTENCE_SCOPE=LOCAL_ONLY"
set "VENV_DIR=%~dp0.venv-standalone"

if exist "%VENV_DIR%\Scripts\python.exe" goto :venv_ready

where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -m venv "%VENV_DIR%" 2>nul
  if exist "%VENV_DIR%\Scripts\python.exe" goto :venv_ready
  py -3 -m venv "%VENV_DIR%"
  if exist "%VENV_DIR%\Scripts\python.exe" goto :venv_ready
)

where python >nul 2>nul
if errorlevel 1 goto :no_python
python -m venv "%VENV_DIR%"
if not exist "%VENV_DIR%\Scripts\python.exe" goto :venv_failed

:venv_ready
echo.
echo [TRIAID FIN] Refreshing pinned dependencies...
"%VENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :deps_failed

echo.
echo [TRIAID FIN] Running self-check...
"%VENV_DIR%\Scripts\python.exe" standalone_selfcheck.py
if errorlevel 1 goto :selfcheck_failed

echo.
echo [TRIAID FIN] Updating US, CN and HK current verifiable states...
"%VENV_DIR%\Scripts\python.exe" standalone_runtime.py --markets all
if errorlevel 1 goto :runtime_failed

echo.
echo [TRIAID FIN] Completed.
echo Reports: %TRIAID_DATA_DIR%\TRIAID_FIN_OUTPUT
explorer "%TRIAID_DATA_DIR%\TRIAID_FIN_OUTPUT" >nul 2>nul
exit /b 0

:no_python
echo Python 3 is required. Install Python 3.12 or newer, then run this file again.
pause
exit /b 10

:venv_failed
echo Failed to create the local Python environment.
pause
exit /b 11

:deps_failed
echo Failed to install TRIAID FIN dependencies. Check network access and run again.
pause
exit /b 12

:selfcheck_failed
echo TRIAID FIN self-check failed. No formal local run was started.
pause
exit /b 20

:runtime_failed
echo TRIAID FIN runtime returned a failure status. Inspect TRIAID_FIN_OUTPUT\runtime_status.json.
pause
exit /b 21
