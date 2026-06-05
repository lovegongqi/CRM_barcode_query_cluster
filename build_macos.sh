#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_DISPLAY_NAME="CRM条码查询"
RUNTIME_NAME="CRMBarcodeQuery"
RUNTIME_DIST_DIR="dist/runtime"
RUNTIME_DIR="${RUNTIME_DIST_DIR}/${RUNTIME_NAME}"
APP_BUNDLE="dist/${APP_DISPLAY_NAME}.app"

echo "==> Creating virtual environment"
if [ ! -d ".venv-macos" ]; then
  "$PYTHON_BIN" -m venv .venv-macos
fi

echo "==> Installing Python dependencies"
./.venv-macos/bin/python -m pip install --upgrade pip
./.venv-macos/bin/pip install -r requirements.txt -r requirements-desktop.txt pyinstaller

echo "==> Cleaning previous build"
rm -rf build "$RUNTIME_DIST_DIR" "$APP_BUNDLE"

echo "==> Generating app icon"
./.venv-macos/bin/python scripts/generate_app_icon.py

echo "==> Building executable"
./.venv-macos/bin/pyinstaller \
  --noconfirm \
  --onedir \
  --console \
  --distpath "$RUNTIME_DIST_DIR" \
  --name "$RUNTIME_NAME" \
  --icon "build/app_icon.icns" \
  --add-data "templates:templates" \
  --add-data "static:static" \
  --add-data "build/app_icon.png:." \
  --add-data "config.example.json:." \
  --add-data "config.docker.example.json:." \
  --collect-all playwright \
  --collect-all webview \
  --collect-all pystray \
  --hidden-import openpyxl.cell._writer \
  app_launcher.py

echo "==> Installing Chromium into the package folder"
export PLAYWRIGHT_BROWSERS_PATH="$PROJECT_ROOT/$RUNTIME_DIR/ms-playwright"
for attempt in 1 2 3 4 5; do
  if ./.venv-macos/bin/python -m playwright install chromium; then
    break
  fi
  if [ "$attempt" = "5" ]; then
    exit 1
  fi
  echo "Playwright browser download failed, retrying in $((attempt * 10)) seconds..."
  sleep $((attempt * 10))
done

echo "==> Creating writable data folders"
mkdir -p "$RUNTIME_DIR/barcode" "$RUNTIME_DIR/results" "$RUNTIME_DIR/session"

echo "==> Wrapping package as macOS app"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"
cp -R "$RUNTIME_DIR" "$APP_BUNDLE/Contents/Resources/$RUNTIME_NAME"
cp "build/app_icon.icns" "$APP_BUNDLE/Contents/Resources/AppIcon.icns"

cat > "$APP_BUNDLE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>zh_CN</string>
  <key>CFBundleDisplayName</key>
  <string>CRM条码查询</string>
  <key>CFBundleExecutable</key>
  <string>CRMBarcodeQuery</string>
  <key>CFBundleIdentifier</key>
  <string>cn.ecowater.crmbarcodequery</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>CRM条码查询</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>11.0</string>
</dict>
</plist>
PLIST

cat > "$APP_BUNDLE/Contents/MacOS/CRMBarcodeQuery" <<'SH'
#!/bin/sh
APP_ROOT="$(cd "$(dirname "$0")/../Resources/CRMBarcodeQuery" && pwd)"
LOG_DIR="$HOME/Library/Application Support/CRMBarcodeQuery"
mkdir -p "$LOG_DIR"
cd "$APP_ROOT" || exit 1
exec "$APP_ROOT/CRMBarcodeQuery" >> "$LOG_DIR/launcher.log" 2>&1
SH
chmod +x "$APP_BUNDLE/Contents/MacOS/CRMBarcodeQuery"

echo ""
echo "Build complete:"
echo "  $APP_BUNDLE"
echo ""
echo "Open the app bundle on the Mac, or put it in a DMG for distribution."
