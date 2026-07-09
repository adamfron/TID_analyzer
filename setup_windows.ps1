$ErrorActionPreference = "Stop"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  Write-Host "Python launcher 'py' was not found. Install it with:"
  Write-Host "winget install Python.Python.3.12"
  exit 1
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  Write-Host "npm was not found. Install Node.js LTS with:"
  Write-Host "winget install OpenJS.NodeJS.LTS"
  Write-Host "Then reopen PowerShell and run setup_windows.ps1 again."
  exit 1
}

if (-not (Test-Path ".venv")) {
  py -m venv .venv
}

. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Push-Location frontend
npm install
Pop-Location
Write-Host "Setup complete. Start the app with .\run_windows.ps1"
