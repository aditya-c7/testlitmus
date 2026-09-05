#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "precedent")
& ".\start.ps1"
