$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Python = "python"
try {
    $PythonVersion = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
} catch {
    $PythonVersion = ""
}
if ($PythonVersion -ne "3.11") {
    $Python = "py"
    $PythonArgs = @("-3.11")
} else {
    $PythonArgs = @()
}

Write-Host "==> Creating virtual environment"
if (!(Test-Path ".venv-win")) {
    & $Python @PythonArgs -m venv .venv-win
}

Write-Host "==> Installing Python dependencies"
& .\.venv-win\Scripts\python.exe -m pip install --upgrade pip
& .\.venv-win\Scripts\pip.exe install -r requirements.txt -r requirements-desktop.txt pyinstaller

Write-Host "==> Cleaning previous build"
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist\CRMBarcodeQuery") { Remove-Item -Recurse -Force "dist\CRMBarcodeQuery" }

Write-Host "==> Generating app icon"
& .\.venv-win\Scripts\python.exe scripts\generate_app_icon.py

Write-Host "==> Building exe"
& .\.venv-win\Scripts\pyinstaller.exe `
    --noconfirm `
    --onedir `
    --windowed `
    --name "CRMBarcodeQuery" `
    --icon "build\app_icon.ico" `
    --add-data "templates;templates" `
    --add-data "static;static" `
    --add-data "build\app_icon.png;." `
    --add-data "config.example.json;." `
    --add-data "config.docker.example.json;." `
    --collect-all playwright `
    --collect-all webview `
    --collect-all pystray `
    --hidden-import openpyxl.cell._writer `
    app_launcher.py

Write-Host "==> Installing Chromium into the exe folder"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $ProjectRoot "dist\CRMBarcodeQuery\ms-playwright"
for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
    & .\.venv-win\Scripts\python.exe -m playwright install chromium
    if ($LASTEXITCODE -eq 0) {
        break
    }
    if ($Attempt -eq 5) {
        throw "Playwright browser download failed after 5 attempts"
    }
    $Delay = $Attempt * 10
    Write-Host "Playwright browser download failed, retrying in $Delay seconds..."
    Start-Sleep -Seconds $Delay
}

Write-Host "==> Creating writable data folders"
New-Item -ItemType Directory -Force "dist\CRMBarcodeQuery\barcode" | Out-Null
New-Item -ItemType Directory -Force "dist\CRMBarcodeQuery\results" | Out-Null
New-Item -ItemType Directory -Force "dist\CRMBarcodeQuery\session" | Out-Null

Write-Host ""
Write-Host "Build complete:"
Write-Host "  dist\CRMBarcodeQuery\CRMBarcodeQuery.exe"
Write-Host ""
Write-Host "Copy the whole dist\CRMBarcodeQuery folder to the Windows computer, then double-click CRMBarcodeQuery.exe."
