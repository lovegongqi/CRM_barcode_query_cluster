#!/usr/bin/env python3
"""Desktop launcher for the CRM barcode query tool."""
import os
import sys
import threading
import time
import webbrowser


def _app_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _app_base_dir()
os.chdir(BASE_DIR)
LOCAL_BROWSER_DIR = os.path.join(BASE_DIR, "ms-playwright")
if os.path.isdir(LOCAL_BROWSER_DIR):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", LOCAL_BROWSER_DIR)

from app import BARCODE_DIR, app  # noqa: E402


def _open_browser():
    time.sleep(1.2)
    webbrowser.open("http://127.0.0.1:5001/product-library")


if __name__ == "__main__":
    os.makedirs(BARCODE_DIR, exist_ok=True)
    threading.Thread(target=_open_browser, daemon=True).start()
    print("=" * 60)
    print("CRM 条码查询工具已启动")
    print("请勿关闭此窗口；关闭窗口后工具会停止运行。")
    print("访问地址: http://127.0.0.1:5001/product-library")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5001, debug=False)
