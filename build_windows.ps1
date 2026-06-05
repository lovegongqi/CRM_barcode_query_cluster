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
& .\.venv-win\Scripts\pip.exe install -r requirements.txt pyinstaller

Write-Host "==> Building exe"
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist\CRM条码查询") { Remove-Item -Recurse -Force "dist\CRM条码查询" }

& .\.venv-win\Scripts\pyinstaller.exe `
    --noconfirm `
    --onedir `
    --console `
    --name "CRM条码查询" `
    --add-data "templates;templates" `
    --add-data "static;static" `
    --add-data "config.example.json;." `
    --add-data "config.docker.example.json;." `
    --collect-all playwright `
    --hidden-import openpyxl.cell._writer `
    app_launcher.py

Write-Host "==> Installing Chromium into the exe folder"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $ProjectRoot "dist\CRM条码查询\ms-playwright"
& .\.venv-win\Scripts\python.exe -m playwright install chromium

Write-Host "==> Creating writable data folders"
New-Item -ItemType Directory -Force "dist\CRM条码查询\barcode" | Out-Null
New-Item -ItemType Directory -Force "dist\CRM条码查询\results" | Out-Null
New-Item -ItemType Directory -Force "dist\CRM条码查询\session" | Out-Null

Write-Host ""
Write-Host "Build complete:"
Write-Host "  dist\CRM条码查询\CRM条码查询.exe"
Write-Host ""
Write-Host "Copy the whole dist\CRM条码查询 folder to the Windows computer, then double-click CRM条码查询.exe."
