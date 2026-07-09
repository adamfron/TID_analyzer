$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
  Write-Host "Run .\setup_windows.ps1 first."
  exit 1
}

. .\.venv\Scripts\Activate.ps1

if (-not (Test-Path "frontend\node_modules")) {
  Write-Host "Run .\setup_windows.ps1 first."
  exit 1
}

tid-analyzer --start-frontend
