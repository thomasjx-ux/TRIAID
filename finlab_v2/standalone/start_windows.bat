@echo off
setlocal
cd /d %~dp0\..\..
python -m pip install -r finlab_v2\requirements.txt
if errorlevel 1 exit /b 1
python finlab_v2\standalone\run_triaid_fin.py --watch
