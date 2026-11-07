# Windows PowerShell setup.  Usage:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\pip install -r backend\requirements-dev.txt
.\.venv\Scripts\pip install -e backend
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path frontend\dist\index.html)) { Push-Location frontend; npm install --no-audit --no-fund; npm run build; Pop-Location }
Write-Host "Setup done. Start with: scripts\run.ps1"
