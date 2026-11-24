# Builds the Windows TEST build: dist\KalciCam\KalciCam.exe plus a zip.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
param([switch]$BundleFfmpeg)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venv = Join-Path $root ".venv-build"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    py -3.12 -m venv $venv
    if (-not (Test-Path "$venv\Scripts\python.exe")) { python -m venv $venv }
}
& "$venv\Scripts\python.exe" -m pip install -q --upgrade pip
& "$venv\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller

& "$venv\Scripts\python.exe" -m PyInstaller packaging\kalcicam.spec --noconfirm --distpath dist --workpath build

# -BundleFfmpeg copies this PC's ffmpeg next to the exe so the folder works on a PC
# without ffmpeg (adds ~200 MB). Otherwise KalciCam uses the ffmpeg on PATH.
if ($BundleFfmpeg) {
    $ff = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
    if ($ff) { Copy-Item $ff "dist\KalciCam\ffmpeg.exe" -Force }
}

$zip = "dist\KalciCam-Windows-Test.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "dist\KalciCam" -DestinationPath $zip
Write-Host "Built dist\KalciCam\KalciCam.exe and $zip"
