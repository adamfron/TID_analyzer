param(
  [switch]$Run
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path $PSScriptRoot
$cachePath = Join-Path $repoRoot ".tid_analyzer_cache"

Write-Host "Developer-phase destructive cache reset."
Write-Host "This removes only the repository-local TID Analyzer cache: $cachePath"
Write-Host "Source input data and bundled scientific assets are not deleted."

if (Test-Path -LiteralPath $cachePath) {
  Remove-Item -LiteralPath $cachePath -Recurse -Force
  Write-Host "Cache existed and was removed."
} else {
  Write-Host "Cache did not exist; nothing to remove."
}

if ($Run) {
  Write-Host "Developer cache reset complete. Starting .\run_windows.ps1"
  & (Join-Path $repoRoot "run_windows.ps1")
} else {
  Write-Host "Developer cache reset complete. Start with .\run_windows.ps1"
}
