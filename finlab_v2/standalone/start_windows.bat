@echo off
setlocal
cd /d %~dp0\..\..

set VENV=.triaid_fin_venv
set PY=%VENV%\Scripts\python.exe
set MARKER=%VENV%\.triaid_fin_dependencies_ready

if not exist "%PY%" (
  echo [TRIAID FIN] First run: creating local Python environment...
  python -m venv "%VENV%"
  if errorlevel 1 exit /b 1
)

if not exist "%MARKER%" (
  echo [TRIAID FIN] First run: installing dependencies...
  "%PY%" -m pip install -r finlab_v2\requirements.txt
  if errorlevel 1 exit /b 1
  echo ready>"%MARKER%"
)

echo [TRIAID FIN] Starting standalone research runtime...
"%PY%" finlab_v2\standalone\run_triaid_fin.py --watch
