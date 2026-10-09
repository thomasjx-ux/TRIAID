@echo off
setlocal EnableExtensions
cd /d %~dp0\..\..

set "VENV=.triaid_fin_venv"
set "PY=%VENV%\Scripts\python.exe"
set "MARKER=%VENV%\.triaid_fin_requirements_sha256"
set "BOOTSTRAP_PY="

if exist "%PY%" goto :venv_ready

where py >nul 2>nul
if not errorlevel 1 set "BOOTSTRAP_PY=py -3"
if not defined BOOTSTRAP_PY (
  where python >nul 2>nul
  if not errorlevel 1 set "BOOTSTRAP_PY=python"
)
if not defined BOOTSTRAP_PY goto :no_python

echo [TRIAID FIN] First run: creating local Python environment...
%BOOTSTRAP_PY% -m venv "%VENV%"
if errorlevel 1 goto :venv_failed
if not exist "%PY%" goto :venv_failed

:venv_ready
set "REQ_HASH="
for /f "delims=" %%H in ('"%PY%" -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('finlab_v2/requirements.txt').read_bytes()).hexdigest())"') do set "REQ_HASH=%%H"
if not defined REQ_HASH goto :hash_failed

set "OLD_HASH="
if exist "%MARKER%" set /p OLD_HASH=<"%MARKER%"
if /I "%REQ_HASH%"=="%OLD_HASH%" goto :dependencies_ready

echo [TRIAID FIN] Installing or refreshing pinned dependencies...
"%PY%" -m pip install --disable-pip-version-check -r finlab_v2\requirements.txt
if errorlevel 1 goto :deps_failed
>"%MARKER%" echo %REQ_HASH%

:dependencies_ready
if /I "%~1"=="--check" goto :check_only

echo [TRIAID FIN] Starting standalone research runtime...
"%PY%" finlab_v2\standalone\run_triaid_fin.py --watch
exit /b %errorlevel%

:check_only
echo [TRIAID FIN] Verifying standalone launcher and runtime imports...
"%PY%" finlab_v2\standalone\run_triaid_fin.py --help >nul
if errorlevel 1 exit /b 20
echo [TRIAID FIN] Windows launcher check passed.
exit /b 0

:no_python
echo [TRIAID FIN] Python 3 is required. Install Python 3.12 or newer and run this file again.
pause
exit /b 10

:venv_failed
echo [TRIAID FIN] Failed to create the local Python environment.
pause
exit /b 11

:hash_failed
echo [TRIAID FIN] Failed to fingerprint requirements.txt. The runtime was not started.
pause
exit /b 12

:deps_failed
echo [TRIAID FIN] Dependency installation failed. The runtime was not started.
pause
exit /b 13
