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

Require-Command python "Install Python 3.11+ first."

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host "Node.js not found. Installing Node.js LTS with winget..." -ForegroundColor Yellow
    winget install -e --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
    $env:Path = "$env:Path;C:\Program Files\nodejs"
  }
}
Require-Command npm "Install Node.js LTS first."

Write-Host "Installing Python dependencies..." -ForegroundColor Cyan
python -m pip install --upgrade pip
Assert-LastCommand "pip upgrade"
python -m pip install --index-url https://pypi.org/simple -r requirements.txt pyinstaller
Assert-LastCommand "pip install"

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

Write-Host "Building Electron package..." -ForegroundColor Cyan
Push-Location (Join-Path $Root "frontend")
try {
    $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
    $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"

    if (-not (Test-Path "node_modules")) {
        npm install
        Assert-LastCommand "npm install"
  }
  npm run dist:win
  Assert-LastCommand "electron-builder"
}
finally {
  Pop-Location
}

Write-Host ""
Write-Host "Done. Windows installers are in frontend\release" -ForegroundColor Green
