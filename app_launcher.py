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
CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = int(os.environ.get("CRM_DESKTOP_CONTROL_PORT", "51241"))
CONTROL_TOKEN = "CRMBarcodeQuery:show"

APP_WINDOW = None
TRAY_ICON = None
MAC_STATUS_ITEM = None
MAC_STATUS_TARGET = None
CONTROL_SOCKET = None
EXIT_REQUESTED = False
SHOW_PENDING = False
WINDOW_LOCK = threading.Lock()


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


def _notify_existing_instance():
    try:
        with socket.create_connection((CONTROL_HOST, CONTROL_PORT), timeout=1) as sock:
            sock.sendall(CONTROL_TOKEN.encode("utf-8"))
        return True
    except Exception:
        return False


def _control_loop(sock):
    while True:
        try:
            conn, _addr = sock.accept()
        except OSError:
            return
        with conn:
            try:
                data = conn.recv(1024).decode("utf-8", errors="ignore")
            except Exception:
                data = ""
        if CONTROL_TOKEN in data:
            _request_show_window()


def _start_single_instance_server():
    global CONTROL_SOCKET
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((CONTROL_HOST, CONTROL_PORT))
    except OSError:
        sock.close()
        if _notify_existing_instance():
            _log("existing instance notified; exiting this launcher")
            return False
        raise RuntimeError("CRM 条码查询可能已经在运行，但无法唤醒现有窗口")
    sock.listen(5)
    CONTROL_SOCKET = sock
    threading.Thread(target=_control_loop, args=(sock,), daemon=True).start()
    return True


def _request_show_window():
    global SHOW_PENDING
    with WINDOW_LOCK:
        window = APP_WINDOW
        if not window:
            SHOW_PENDING = True
            return
    try:
        if sys.platform == "darwin":
            _activate_macos_app()
        if hasattr(window, "restore"):
            window.restore()
        if hasattr(window, "bring_to_front"):
            window.bring_to_front()
        window.show()
        if sys.platform == "darwin":
            _activate_macos_app()
        _log("window shown")
    except Exception:
        _log("show window failed")
        _log(traceback.format_exc())


def _activate_macos_app():
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication  # type: ignore
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass


def _set_app_window(window):
    global APP_WINDOW, SHOW_PENDING
    with WINDOW_LOCK:
        APP_WINDOW = window
        should_show = SHOW_PENDING
        SHOW_PENDING = False
    if should_show:
        _request_show_window()


def _resource_path(filename):
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        path = os.path.join(sys._MEIPASS, filename)
        if os.path.exists(path):
            return path
    path = os.path.join(BASE_DIR, filename)
    if os.path.exists(path):
        return path
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "build", filename)


def _tray_image():
    try:
        from PIL import Image, ImageDraw
        icon_path = _resource_path("app_icon.png")
        if os.path.exists(icon_path):
            return Image.open(icon_path)
        image = Image.new("RGBA", (64, 64), (17, 126, 160, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((14, 16, 50, 32), fill=(255, 255, 255, 255))
        for x in (20, 26, 35, 43):
            draw.rectangle((x, 18, x + 3, 30), fill=(8, 70, 95, 255))
        draw.text((15, 40), "CRM", fill=(255, 255, 255, 255))
        return image
    except Exception:
        return None


def _shutdown_backend():
    try:
        from app import crm_pool  # noqa: E402
        crm_pool.shutdown()
        _log("CRM worker pool shutdown complete")
    except Exception:
        _log("CRM worker pool shutdown failed")
        _log(traceback.format_exc())


def _quit_app(icon=None, item=None):
    global EXIT_REQUESTED
    if EXIT_REQUESTED:
        return
    EXIT_REQUESTED = True
    _log("quit requested")
    try:
        if icon:
            icon.visible = False
            icon.stop()
    except Exception:
        pass
    _shutdown_backend()
    with WINDOW_LOCK:
        window = APP_WINDOW
    if window:
        try:
            window.destroy()
            return
        except Exception:
            _log("destroy window failed")
            _log(traceback.format_exc())
    os._exit(0)


def _start_tray_icon():
    global TRAY_ICON
    if sys.platform == "darwin" and _start_macos_status_item():
        return True
    try:
        import pystray
        image = _tray_image()
        if image is None:
            return False
        menu = pystray.Menu(
            pystray.MenuItem("打开窗口", lambda icon, item: _request_show_window(), default=True),
            pystray.MenuItem("退出应用", _quit_app),
        )
        TRAY_ICON = pystray.Icon(APP_NAME, image, "CRM 条码查询", menu)
        TRAY_ICON.run_detached()
        return True
    except Exception:
        TRAY_ICON = None
        _log("tray icon failed")
        _log(traceback.format_exc())
        return False


def _start_macos_status_item():
    global MAC_STATUS_ITEM, MAC_STATUS_TARGET
    try:
        from AppKit import NSImage, NSMakeSize, NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength  # type: ignore
        from Foundation import NSObject  # type: ignore

        class MacStatusTarget(NSObject):
            def openWindow_(self, sender):
                _request_show_window()

            def quitApp_(self, sender):
                _quit_app()

        target = MacStatusTarget.alloc().init()
        status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = status_item.button()
        icon_path = _resource_path("app_icon.png")
        if button and os.path.exists(icon_path):
            image = NSImage.alloc().initWithContentsOfFile_(icon_path)
            if image:
                image.setSize_(NSMakeSize(18, 18))
                button.setImage_(image)
            else:
                button.setTitle_("CRM")
        elif button:
            button.setTitle_("CRM")

        menu = NSMenu.alloc().init()
        open_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("打开窗口", "openWindow:", "")
        open_item.setTarget_(target)
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("退出应用", "quitApp:", "")
        quit_item.setTarget_(target)
        menu.addItem_(open_item)
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(quit_item)
        status_item.setMenu_(menu)

        MAC_STATUS_ITEM = status_item
        MAC_STATUS_TARGET = target
        _log("macOS status item started")
        return True
    except Exception:
        MAC_STATUS_ITEM = None
        MAC_STATUS_TARGET = None
        _log("macOS status item failed")
        _log(traceback.format_exc())
        return False


def _on_window_closing():
    if EXIT_REQUESTED:
        return True
    with WINDOW_LOCK:
        window = APP_WINDOW
    if sys.platform == "darwin" and window:
        if not MAC_STATUS_ITEM:
            _log("macOS status item unavailable; closing app instead of hiding")
            return True
        try:
            if hasattr(window, "minimize"):
                window.minimize()
                _log("window minimized; macOS status item remains available")
                return False
            if hasattr(window, "hide"):
                window.hide()
                _log("window hidden to macOS status item")
                return False
            _log("no hide/minimize support; closing app")
            return True
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception:
            _log("minimize/hide window failed")
            _log(traceback.format_exc())
            return True
    if not TRAY_ICON:
        return True
    if window:
        try:
            window.hide()
            _log("window hidden to tray")
        except Exception:
            _log("hide window failed")
            _log(traceback.format_exc())
            return True
    return False


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
        window = webview.create_window(
            "CRM 条码查询",
            url,
            width=1280,
            height=900,
            min_size=(960, 700),
        )
        _set_app_window(window)
        window.events.closing += _on_window_closing
        _start_tray_icon()
        webview.start(debug=False)
        if not EXIT_REQUESTED:
            _shutdown_backend()
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

        if not _start_single_instance_server():
            sys.exit(0)

        from app import BARCODE_DIR  # noqa: E402

        os.makedirs(BARCODE_DIR, exist_ok=True)
        flask_thread = threading.Thread(target=_run_flask, args=(PORT,), daemon=True)
        flask_thread.start()
        if _open_native_window(PORT):
            sys.exit(0)
        window_process = _launch_app_window(PORT)
        if window_process:
            window_process.wait()
            _shutdown_backend()
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
