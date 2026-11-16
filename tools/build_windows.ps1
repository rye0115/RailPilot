# Build the Windows onedir bundle with PyInstaller.
# Usage: powershell -ExecutionPolicy Bypass -File tools\build_windows.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "missing .venv; create it and install requirements-dev.txt first"
}

.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --console `
    --name RailPilot --collect-all playwright --collect-submodules railpilot launcher.py

Copy-Item "design\config.example.yaml" "dist\RailPilot\config.yaml" -Force
Copy-Item "design\README-portable.txt" "dist\RailPilot\README.txt" -Force

$zip = "dist\RailPilot-v0.1-win64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "dist\RailPilot" -DestinationPath $zip -CompressionLevel Optimal

Write-Host "built: $zip"
