#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)

$Py = $null
foreach ($cand in @(".\.venv\Scripts\python.exe", ".venv/bin/python")) {
  if (Test-Path -LiteralPath $cand) { $Py = $cand; break }
}
if (-not $Py) {
  if (Get-Command python -ErrorAction SilentlyContinue) { $Base = "python" }
  elseif (Get-Command py -ErrorAction SilentlyContinue) { $Base = "py -3" }
  else { throw "No Python found. Install Python 3.10+ and retry." }
  Write-Host "Creating virtualenv (.venv)..."
  Invoke-Expression "$Base -m venv .venv"
  if (Test-Path -LiteralPath ".\.venv\Scripts\python.exe") { $Py = ".\.venv\Scripts\python.exe" }
  else { $Py = $Base }
}
& $Py -m pip install --disable-pip-version-check -r requirements.txt
if (-not $env:PORT) { $env:PORT = "8000" }
Write-Host "Serving Precedent on http://127.0.0.1:$env:PORT/ (Ctrl+C to stop)"
& $Py server.py
