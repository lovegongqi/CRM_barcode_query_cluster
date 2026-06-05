#!/usr/bin/env python3
"""Desktop launcher for the CRM barcode query tool."""
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser


APP_NAME = "CRMBarcodeQuery"


def _user_data_dir():
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(root, APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~/Library/Application Support"), APP_NAME)
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), APP_NAME)


def _app_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _app_base_dir()
os.chdir(BASE_DIR)
DATA_DIR = os.path.join(_user_data_dir(), "data")
SESSION_DIR = os.path.join(_user_data_dir(), "session")
LOG_FILE = os.path.join(_user_data_dir(), "launcher.log")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SESSION_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.environ.setdefault("CRM_DATA_DIR", DATA_DIR)
os.environ.setdefault("CRM_SESSION_BASE", SESSION_DIR)
LOCAL_BROWSER_DIR = os.path.join(BASE_DIR, "ms-playwright")
if os.path.isdir(LOCAL_BROWSER_DIR):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", LOCAL_BROWSER_DIR)


def _find_free_port(start=5001, end=5099):
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("未找到可用端口，请关闭占用 5001-5099 的程序后重试")


PORT = _find_free_port()


def _open_browser(port):
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{port}/product-library")


if __name__ == "__main__":
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as log:
            log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] launcher start\n")
            log.write(f"BASE_DIR={BASE_DIR}\nDATA_DIR={DATA_DIR}\nSESSION_DIR={SESSION_DIR}\nPORT={PORT}\n")

        from app import BARCODE_DIR, app  # noqa: E402

        os.makedirs(BARCODE_DIR, exist_ok=True)
        threading.Thread(target=_open_browser, args=(PORT,), daemon=True).start()
        print("=" * 60)
        print("CRM 条码查询工具已启动")
        print("请勿关闭此窗口；关闭窗口后工具会停止运行。")
        print(f"访问地址: http://127.0.0.1:{PORT}/product-library")
        print(f"日志文件: {LOG_FILE}")
        print("=" * 60)
        app.run(host="127.0.0.1", port=PORT, debug=False)
    except Exception:
        error = traceback.format_exc()
        with open(LOG_FILE, "a", encoding="utf-8") as log:
            log.write(error)
        print("启动失败，错误已写入日志：")
        print(LOG_FILE)
        print(error)
        try:
            input("按回车退出...")
        except Exception:
            time.sleep(10)
