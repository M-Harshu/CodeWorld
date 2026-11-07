Set-Location (Join-Path $PSScriptRoot "..")
$env:CODEWORLD_UI_DIR = (Resolve-Path frontend\dist).Path
$env:PYTHONPATH = (Resolve-Path backend).Path
if (-not $env:CODEWORLD_ALLOW_LOCAL) { $env:CODEWORLD_ALLOW_LOCAL = "1" }

# ---- Load .env automatically (bug fix: run.ps1 previously ignored .env) ---------------
if (Test-Path .env) {
    Get-Content .env | Where-Object { $_ -match '^\s*[^#]\w+=.+' } | ForEach-Object {
        $parts = $_ -split '=', 2
        $name  = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (-not [System.Environment]::GetEnvironmentVariable($name)) {
            [System.Environment]::SetEnvironmentVariable($name, $value, 'Process')
            Write-Host "  .env: $name loaded"
        }
    }
}

.\.venv\Scripts\python -m uvicorn codeworld.api.main:app --port 8000
