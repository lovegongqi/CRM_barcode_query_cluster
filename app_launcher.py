#!/usr/bin/env python3
"""Desktop launcher for the CRM barcode query tool."""
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
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
os.environ.setdefault("CRM_DESKTOP_APP", "1")
os.environ.setdefault("CRM_DISABLE_DATA_MIGRATION", "1")
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


def _log(message):
    with open(LOG_FILE, "a", encoding="utf-8") as log:
        log.write(f"{message}\n")


def _wait_for_server(port, timeout=20):
    url = f"http://127.0.0.1:{port}/product-library"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.2)
    return False


def _find_packaged_chromium():
    candidates = []
    if sys.platform == "win32":
        candidates.append(os.path.join(LOCAL_BROWSER_DIR, "chromium-*", "chrome-win", "chrome.exe"))
    elif sys.platform == "darwin":
        candidates.append(os.path.join(
            LOCAL_BROWSER_DIR,
            "chromium-*",
            "chrome-mac*",
            "Google Chrome for Testing.app",
            "Contents",
            "MacOS",
            "Google Chrome for Testing",
        ))
        candidates.append(os.path.join(
            LOCAL_BROWSER_DIR,
            "chromium-*",
            "chrome-mac*",
            "Chromium.app",
            "Contents",
            "MacOS",
            "Chromium",
        ))
    else:
        candidates.append(os.path.join(LOCAL_BROWSER_DIR, "chromium-*", "chrome-linux", "chrome"))

    import glob
    for pattern in candidates:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    return ""


def _launch_app_window(port):
    if not _wait_for_server(port):
        _log("server did not respond before UI launch; trying to open UI anyway")

    url = f"http://127.0.0.1:{port}/product-library"
    chromium = _find_packaged_chromium()
    if not chromium:
        _log("packaged Chromium not found, falling back to system browser")
        webbrowser.open(url)
        return None

    ui_profile = os.path.join(SESSION_DIR, "app-window")
    os.makedirs(ui_profile, exist_ok=True)
    args = [
        chromium,
        f"--app={url}",
        f"--user-data-dir={ui_profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--disable-extensions",
        "--disable-features=Translate",
        "--window-size=1280,900",
    ]
    creationflags = 0
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW
    _log(f"launching app window: {chromium}")
    return subprocess.Popen(args, creationflags=creationflags)


def _open_native_window(port):
    if os.environ.get("CRM_FORCE_CHROMIUM_UI"):
        return False
    if not _wait_for_server(port):
        _log("server did not respond before native window launch; trying to open UI anyway")

    url = f"http://127.0.0.1:{port}/product-library"
    try:
        import webview
        _log("launching native webview window")
        webview.create_window(
            "CRM 条码查询",
            url,
            width=1280,
            height=900,
            min_size=(960, 700),
        )
        webview.start(debug=False)
        return True
    except Exception:
        _log("native webview failed, falling back to packaged Chromium")
        _log(traceback.format_exc())
        return False


def _run_flask(port):
    from app import app  # noqa: E402
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


def _show_startup_error(message):
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("CRM Barcode Query 启动失败", message)
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        _log(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] launcher start")
        _log(f"BASE_DIR={BASE_DIR}")
        _log(f"DATA_DIR={DATA_DIR}")
        _log(f"SESSION_DIR={SESSION_DIR}")
        _log(f"PORT={PORT}")

        from app import BARCODE_DIR  # noqa: E402

        os.makedirs(BARCODE_DIR, exist_ok=True)
        flask_thread = threading.Thread(target=_run_flask, args=(PORT,), daemon=True)
        flask_thread.start()
        if _open_native_window(PORT):
            sys.exit(0)
        window_process = _launch_app_window(PORT)
        if window_process:
            window_process.wait()
        else:
            while True:
                time.sleep(3600)
    except Exception:
        error = traceback.format_exc()
        _log(error)
        _show_startup_error(f"错误已写入日志：\n{LOG_FILE}\n\n{error}")
        if not getattr(sys, "frozen", False):
            print("启动失败，错误已写入日志：")
            print(LOG_FILE)
            print(error)
