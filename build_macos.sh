#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
PACKAGE_NAME="CRM条码查询"
DIST_DIR="dist/${PACKAGE_NAME}"

echo "==> Creating virtual environment"
if [ ! -d ".venv-macos" ]; then
  "$PYTHON_BIN" -m venv .venv-macos
fi

echo "==> Installing Python dependencies"
./.venv-macos/bin/python -m pip install --upgrade pip
./.venv-macos/bin/pip install -r requirements.txt pyinstaller

echo "==> Building executable"
rm -rf build "$DIST_DIR"

./.venv-macos/bin/pyinstaller \
  --noconfirm \
  --onedir \
  --console \
  --name "$PACKAGE_NAME" \
  --add-data "templates:templates" \
  --add-data "config.example.json:." \
  --add-data "config.docker.example.json:." \
  --collect-all playwright \
  --hidden-import openpyxl.cell._writer \
  app_launcher.py

echo "==> Installing Chromium into the package folder"
export PLAYWRIGHT_BROWSERS_PATH="$PROJECT_ROOT/$DIST_DIR/ms-playwright"
./.venv-macos/bin/python -m playwright install chromium

echo "==> Creating writable data folders"
mkdir -p "$DIST_DIR/barcode" "$DIST_DIR/results" "$DIST_DIR/session"

echo ""
echo "Build complete:"
echo "  $DIST_DIR/$PACKAGE_NAME"
echo ""
echo "Copy the whole $DIST_DIR folder to the Mac, then run $PACKAGE_NAME."
