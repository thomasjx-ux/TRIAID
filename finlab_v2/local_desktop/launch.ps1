$ErrorActionPreference='Stop'
$FinLab=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Pyw=Join-Path $env:LOCALAPPDATA 'TRIAID_FIN_Local\venv\Scripts\pythonw.exe'
if (-not (Test-Path $Pyw)) {
  throw 'Desktop not installed. Run local_desktop\install.ps1 first.'
}
Start-Process -FilePath $Pyw -ArgumentList '-m local_desktop.client' -WorkingDirectory $FinLab
