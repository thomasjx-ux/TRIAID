# TRIAID FIN Desktop: install for the currently signed-in Windows user.
# Requires Python 3.12 and Internet access only for fetching pinned dependencies.
param([switch]$NoAutostart)
$ErrorActionPreference = 'Stop'
$FinLab = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$HomeDir = Join-Path $env:LOCALAPPDATA 'TRIAID_FIN_Local'
$Venv = Join-Path $HomeDir 'venv'
New-Item -Path $HomeDir -ItemType Directory -Force | Out-Null

$Interpreter = $null
$PyArgs = @()
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
  & $py.Source -3.12 -c 'import sys;sys.exit(0 if sys.version_info[:2] == (3,12) else 1)' 2>$null
  if ($LASTEXITCODE -eq 0) { $Interpreter = $py.Source; $PyArgs = @('-3.12') }
}
if (-not $Interpreter) {
  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($python) {
    & $python.Source -c 'import sys;sys.exit(0 if sys.version_info[:2] == (3,12) else 1)' 2>$null
    if ($LASTEXITCODE -eq 0) { $Interpreter = $python.Source }
  }
}
if (-not $Interpreter) { throw 'Python 3.12 is required. Install it, then rerun this installer.' }
Push-Location $FinLab
try {
  & $Interpreter @PyArgs -m venv $Venv
  if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
  $Py = Join-Path $Venv 'Scripts\python.exe'
  $Pyw = Join-Path $Venv 'Scripts\pythonw.exe'
  $WheelRoot = Join-Path $FinLab 'vendor\wheels'
  $Manifest = Join-Path $FinLab 'vendor\WHEEL_SHA256SUMS.txt'
  if (Test-Path $WheelRoot) {
    if (-not (Test-Path $Manifest)) { throw 'Bundled wheelhouse has no SHA-256 manifest' }
    foreach ($line in Get-Content $Manifest) {
      if ($line -notmatch '^([0-9a-fA-F]{64})  ([^\\/]+)

  # The audit runs against isolated temporary data, never the user's research records.
  $OldHome = $env:TRIAID_LOCAL_HOME
  $OldAutomation = $env:TRIAID_DATA_AUTOMATION
  $OldCalendar = $env:TRIAID_CALENDAR_SYNC
  $OldBootstrap = $env:TRIAID_LONG_RESEARCH_BOOTSTRAP
  $env:TRIAID_LOCAL_HOME = Join-Path $env:TEMP ("triaid-local-audit-" + [guid]::NewGuid().ToString('N'))
  $env:TRIAID_DATA_AUTOMATION = '0'
  $env:TRIAID_CALENDAR_SYNC = '0'
  $env:TRIAID_LONG_RESEARCH_BOOTSTRAP = '0'
  try {
    & $Py -m local_desktop.smoke
    if ($LASTEXITCODE -ne 0) { throw 'Desktop security smoke failed' }
    & $Py -m local_desktop.integration_smoke
    if ($LASTEXITCODE -ne 0) { throw 'Desktop HTTP integration smoke failed' }
    & $Py -m local_desktop.process_smoke
    if ($LASTEXITCODE -ne 0) { throw 'Desktop background-process/restart smoke failed' }
    & $Py local_desktop_contract_smoke.py
    if ($LASTEXITCODE -ne 0) { throw 'Desktop architecture audit failed' }
    & $Py local_desktop_parity_smoke.py
    if ($LASTEXITCODE -ne 0) { throw 'Desktop UI/API parity audit failed' }
  } finally {
    Remove-Item Env:\TRIAID_LOCAL_HOME -ErrorAction SilentlyContinue
    if ($null -ne $OldHome) { $env:TRIAID_LOCAL_HOME = $OldHome }
    Remove-Item Env:\TRIAID_DATA_AUTOMATION -ErrorAction SilentlyContinue
    if ($null -ne $OldAutomation) { $env:TRIAID_DATA_AUTOMATION = $OldAutomation }
    Remove-Item Env:\TRIAID_CALENDAR_SYNC -ErrorAction SilentlyContinue
    if ($null -ne $OldCalendar) { $env:TRIAID_CALENDAR_SYNC = $OldCalendar }
    Remove-Item Env:\TRIAID_LONG_RESEARCH_BOOTSTRAP -ErrorAction SilentlyContinue
    if ($null -ne $OldBootstrap) { $env:TRIAID_LONG_RESEARCH_BOOTSTRAP = $OldBootstrap }
  }

  # Installer preflight records the first independent durability checkpoint.
  & $Py -m local_desktop.durability
  if ($LASTEXITCODE -ne 0) { throw 'Local data-directory durability preflight failed' }

  $Shell = New-Object -ComObject WScript.Shell
  $Desktop = [Environment]::GetFolderPath('DesktopDirectory')
  $AppLink = $Shell.CreateShortcut((Join-Path $Desktop 'TRIAID FIN Desktop.lnk'))
  $AppLink.TargetPath = $Pyw
  $AppLink.Arguments = '-m local_desktop.client'
  $AppLink.WorkingDirectory = $FinLab
  $AppLink.Description = 'TRIAID FIN: full research workbench'
  $AppLink.Save()

  if (-not $NoAutostart) {
    $Startup = [Environment]::GetFolderPath('Startup')
    $ServerLink = $Shell.CreateShortcut((Join-Path $Startup 'TRIAID FIN Research Server.lnk'))
    $ServerLink.TargetPath = $Pyw
    $ServerLink.Arguments = '-m local_desktop.supervisor'
    $ServerLink.WorkingDirectory = $FinLab
    $ServerLink.Description = 'Keep the private research backend running after login'
    $ServerLink.Save()
    Start-Process -FilePath $Pyw -ArgumentList '-m local_desktop.supervisor' -WorkingDirectory $FinLab
  }
  Write-Host "TRIAID FIN Desktop installed. Open the desktop shortcut." -ForegroundColor Green
  if ($NoAutostart) { Write-Warning 'Background auto-start was disabled by request.' }
  Write-Host "Personal data: $HomeDir"
} finally {
  Pop-Location
}
) {
        throw 'Malformed wheelhouse checksum manifest'
      }
      $Expected = $Matches[1].ToUpperInvariant()
      $Wheel = Join-Path $WheelRoot $Matches[2]
      if (-not (Test-Path $Wheel)) { throw "Missing packaged dependency: $Wheel" }
      $Actual = (Get-FileHash -Path $Wheel -Algorithm SHA256).Hash
      if ($Actual -ne $Expected) { throw "Bundled dependency checksum failed: $Wheel" }
    }
    & $Py -m pip install --disable-pip-version-check --no-index --find-links $WheelRoot -r (Join-Path $FinLab 'requirements-desktop.txt')
  } else {
    & $Py -m pip install --disable-pip-version-check -r (Join-Path $FinLab 'requirements-desktop.txt')
  }
  if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed' }

  # The audit runs against isolated temporary data, never the user's research records.
  $OldHome = $env:TRIAID_LOCAL_HOME
  $OldAutomation = $env:TRIAID_DATA_AUTOMATION
  $OldCalendar = $env:TRIAID_CALENDAR_SYNC
  $OldBootstrap = $env:TRIAID_LONG_RESEARCH_BOOTSTRAP
  $env:TRIAID_LOCAL_HOME = Join-Path $env:TEMP ("triaid-local-audit-" + [guid]::NewGuid().ToString('N'))
  $env:TRIAID_DATA_AUTOMATION = '0'
  $env:TRIAID_CALENDAR_SYNC = '0'
  $env:TRIAID_LONG_RESEARCH_BOOTSTRAP = '0'
  try {
    & $Py -m local_desktop.smoke
    if ($LASTEXITCODE -ne 0) { throw 'Desktop security smoke failed' }
    & $Py -m local_desktop.integration_smoke
    if ($LASTEXITCODE -ne 0) { throw 'Desktop HTTP integration smoke failed' }
    & $Py local_desktop_contract_smoke.py
    if ($LASTEXITCODE -ne 0) { throw 'Desktop architecture audit failed' }
    & $Py local_desktop_parity_smoke.py
    if ($LASTEXITCODE -ne 0) { throw 'Desktop UI/API parity audit failed' }
  } finally {
    Remove-Item Env:\TRIAID_LOCAL_HOME -ErrorAction SilentlyContinue
    if ($null -ne $OldHome) { $env:TRIAID_LOCAL_HOME = $OldHome }
    Remove-Item Env:\TRIAID_DATA_AUTOMATION -ErrorAction SilentlyContinue
    if ($null -ne $OldAutomation) { $env:TRIAID_DATA_AUTOMATION = $OldAutomation }
    Remove-Item Env:\TRIAID_CALENDAR_SYNC -ErrorAction SilentlyContinue
    if ($null -ne $OldCalendar) { $env:TRIAID_CALENDAR_SYNC = $OldCalendar }
    Remove-Item Env:\TRIAID_LONG_RESEARCH_BOOTSTRAP -ErrorAction SilentlyContinue
    if ($null -ne $OldBootstrap) { $env:TRIAID_LONG_RESEARCH_BOOTSTRAP = $OldBootstrap }
  }

  # Installer preflight records the first independent durability checkpoint.
  & $Py -m local_desktop.durability
  if ($LASTEXITCODE -ne 0) { throw 'Local data-directory durability preflight failed' }

  $Shell = New-Object -ComObject WScript.Shell
  $Desktop = [Environment]::GetFolderPath('DesktopDirectory')
  $AppLink = $Shell.CreateShortcut((Join-Path $Desktop 'TRIAID FIN Desktop.lnk'))
  $AppLink.TargetPath = $Pyw
  $AppLink.Arguments = '-m local_desktop.client'
  $AppLink.WorkingDirectory = $FinLab
  $AppLink.Description = 'TRIAID FIN: full research workbench'
  $AppLink.Save()

  if (-not $NoAutostart) {
    $Startup = [Environment]::GetFolderPath('Startup')
    $ServerLink = $Shell.CreateShortcut((Join-Path $Startup 'TRIAID FIN Research Server.lnk'))
    $ServerLink.TargetPath = $Pyw
    $ServerLink.Arguments = '-m local_desktop.supervisor'
    $ServerLink.WorkingDirectory = $FinLab
    $ServerLink.Description = 'Keep the private research backend running after login'
    $ServerLink.Save()
    Start-Process -FilePath $Pyw -ArgumentList '-m local_desktop.supervisor' -WorkingDirectory $FinLab
  }
  Write-Host "TRIAID FIN Desktop installed. Open the desktop shortcut." -ForegroundColor Green
  if ($NoAutostart) { Write-Warning 'Background auto-start was disabled by request.' }
  Write-Host "Personal data: $HomeDir"
} finally {
  Pop-Location
}
