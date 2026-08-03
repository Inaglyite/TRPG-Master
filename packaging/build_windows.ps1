param(
  [switch]$SkipDependencyInstall,
  [switch]$UseChinaMirrors
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Require-Command($Name, $InstallHint) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "$Name not found. $InstallHint"
  }
}

function Assert-LastCommand($Label) {
  if ($LASTEXITCODE -ne 0) {
    throw "$Label failed with exit code $LASTEXITCODE"
  }
}

Write-Host "== TRPG Master Windows package ==" -ForegroundColor Cyan
Write-Host "Project: $Root"

Require-Command python "Install Python 3.12+ first."

$PythonVersion = python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
Assert-LastCommand "Python version check"
if ([version]$PythonVersion -lt [version]"3.12") {
  throw "Python 3.12+ is required; found $PythonVersion."
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host "Node.js not found. Installing Node.js LTS with winget..." -ForegroundColor Yellow
    winget install -e --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
    $env:Path = "$env:Path;C:\Program Files\nodejs"
  }
}
Require-Command npm "Install Node.js LTS first."
Require-Command git "Install Git and build from a Git checkout so only tracked release assets are packaged."

if (-not $SkipDependencyInstall) {
  Write-Host "Installing Python dependencies..." -ForegroundColor Cyan
  python -m pip install --upgrade pip
  Assert-LastCommand "pip upgrade"
  python -m pip install --index-url https://pypi.org/simple -r requirements-packaging.txt
  Assert-LastCommand "pip install"

  Write-Host "Installing locked frontend dependencies..." -ForegroundColor Cyan
  Push-Location (Join-Path $Root "frontend")
  try {
    npm ci
    Assert-LastCommand "npm ci"
  }
  finally {
    Pop-Location
  }
}

Write-Host "Building backend executable..." -ForegroundColor Cyan
$BackendOut = Join-Path $Root "release-backend\win\trpg-server"
if (Test-Path $BackendOut) {
  Remove-Item $BackendOut -Recurse -Force
}
python -m PyInstaller --noconfirm --clean `
  --distpath (Join-Path $Root "release-backend\win") `
  --workpath (Join-Path $Root "build\pyinstaller") `
  (Join-Path $Root "packaging\trpg-server.spec")
Assert-LastCommand "PyInstaller"

if (-not (Test-Path (Join-Path $BackendOut "trpg-server.exe"))) {
  throw "Backend build failed: trpg-server.exe was not created."
}
python (Join-Path $Root "packaging\verify_backend_bundle.py") `
  $BackendOut --platform windows
Assert-LastCommand "backend bundle verification"

Write-Host "Building Electron package..." -ForegroundColor Cyan
Push-Location (Join-Path $Root "frontend")
try {
  if (Test-Path "release") {
    Remove-Item "release" -Recurse -Force
  }

  if ($UseChinaMirrors) {
    $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
    $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
  }

  npm run dist:win
  Assert-LastCommand "electron-builder"
}
finally {
  Pop-Location
}

Write-Host ""
Write-Host "Done. Windows installers are in frontend\release" -ForegroundColor Green
