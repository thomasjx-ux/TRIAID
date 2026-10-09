@echo off
setlocal EnableExtensions
cd /d "%~dp0\..\.."

set "VENV=.triaid_fin_venv"
set "PY=%VENV%\Scripts\python.exe"
set "MARKER=%VENV%\.triaid_fin_requirements_sha256"
set "CURRENT_HASH_FILE=%VENV%\.triaid_fin_requirements_current"
set "BOOTSTRAP_PY="
set "CHECK_ONLY=0"
if /I "%~1"=="--check" set "CHECK_ONLY=1"

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
del /q "%CURRENT_HASH_FILE%" >nul 2>nul
"%PY%" -c "import hashlib,pathlib; pathlib.Path(r'%CURRENT_HASH_FILE%').write_text(hashlib.sha256(pathlib.Path(r'finlab_v2/requirements.txt').read_bytes()).hexdigest(), encoding='utf-8')"
if errorlevel 1 goto :hash_failed

set "REQ_HASH="
set /p REQ_HASH=<"%CURRENT_HASH_FILE%"
del /q "%CURRENT_HASH_FILE%" >nul 2>nul
if not defined REQ_HASH goto :hash_failed

set "OLD_HASH="
if exist "%MARKER%" set /p OLD_HASH=<"%MARKER%"
if /I "%REQ_HASH%"=="%OLD_HASH%" goto :dependencies_ready

echo [TRIAID FIN] Installing or refreshing pinned dependencies...
"%PY%" -m pip install --disable-pip-version-check -r finlab_v2\requirements.txt
if errorlevel 1 goto :deps_failed
>"%MARKER%" echo %REQ_HASH%

:dependencies_ready
if "%CHECK_ONLY%"=="1" goto :check_only

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
if not "%CHECK_ONLY%"=="1" pause
exit /b 10

:venv_failed
echo [TRIAID FIN] Failed to create the local Python environment.
if not "%CHECK_ONLY%"=="1" pause
exit /b 11

:hash_failed
del /q "%CURRENT_HASH_FILE%" >nul 2>nul
echo [TRIAID FIN] Failed to fingerprint requirements.txt. The runtime was not started.
if not "%CHECK_ONLY%"=="1" pause
exit /b 12

:deps_failed
echo [TRIAID FIN] Dependency installation failed. The runtime was not started.
if not "%CHECK_ONLY%"=="1" pause
exit /b 13
