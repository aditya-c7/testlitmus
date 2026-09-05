#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)

$Port = if ($env:PORT) { $env:PORT } else { "8641" }
$env:PORT = $Port
if (-not ($env:OPENAI_API_KEY -or $env:LITMUS_AI_API_KEY)) { $env:DEMO_MODE = "true" }

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) { $py = "python" }

Write-Host "Smoke: PORT=$Port (demo=$($env:DEMO_MODE))"
$proc = Start-Process -FilePath $py -ArgumentList "server.py" -PassThru -NoNewWindow
try {
  $ready = $false
  for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    if ($proc.HasExited) { throw "./start exited before becoming ready. Check server output above." }
    try {
      $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 5
      if ($h.status -eq "ready") { $ready = $true; break }
    } catch { }
  }
  if (-not $ready) { throw "GET /api/health did not return ready in time" }
  Write-Host "OK: ready"
  if (-not (Test-Path -LiteralPath "./playbook/playbook.json")) { throw "./playbook/playbook.json missing after boot" }
  Write-Host "OK: playbook exists"

  $smoke = 'This Master Services Agreement is made between Example Client Inc. ("Client") and the vendor ("Supplier"). 1. Fees and Payment. Client will pay undisputed fees within forty-five (45) days of the date of each invoice. 2. Governing Law. This Agreement is governed by the laws of the State of Delaware.'
  $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/review" -Method Post `
    -ContentType "application/json" -Body (@{ contract = $smoke } | ConvertTo-Json)
  if (-not $resp.clauses -or $resp.clauses.Count -eq 0) { throw "review returned no clauses" }
  $json = $resp | ConvertTo-Json -Depth 6
  if ($json -notmatch "accept|counter|escalate") { throw "no disposition vocabulary in review" }
  Write-Host ("OK: review returned {0} clauses" -f $resp.clauses.Count)
  Write-Host "OK: smoke satisfied."
} finally {
  if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
}
