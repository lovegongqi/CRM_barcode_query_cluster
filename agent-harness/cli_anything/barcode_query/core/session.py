"""CRM session management via Playwright"""
import os
import sys
import time
import json
import threading
from pathlib import Path

# Add parent directory to path for original app imports
app_dir = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(app_dir))

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


class CRMSessionManager:
    """Manages CRM browser session via Playwright"""

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.lock = threading.Lock()
        self.logged_in = False
        self.config = self._load_config()
        self.accounts = self._load_accounts()

    def _load_config(self):
        config_path = Path(__file__).parent.parent.parent.parent / 'config.json'
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def _load_accounts(self):
        accounts_path = Path(__file__).parent.parent.parent.parent / 'accounts.json'
        if accounts_path.exists():
            with open(accounts_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def is_alive(self):
        try:
            if self.browser and self.page:
                self.page.url
                return True
        except Exception:
            pass
        return False

    def login(self, username, password):
        if not HAS_PLAYWRIGHT:
            return {'success': False, 'error': 'Playwright 未安装'}

        with self.lock:
            try:
                if self.is_alive():
                    self._close_browser()

                if username not in self.accounts:
                    return {'success': False, 'error': f'账号 {username} 不在 accounts.json 中'}
                saved_pw = self.accounts[username].get('password', '')
                if password != saved_pw:
                    return {'success': False, 'error': '密码不匹配'}

                session_dir = self.config.get('session', {}).get('state_path', './session')
                os.makedirs(session_dir, exist_ok=True)

                self.playwright = sync_playwright().start()
                viewport = self.config.get('browser', {}).get('viewport', {'width': 1920, 'height': 1080})

                self.context = self.playwright.chromium.launch_persistent_context(
                    user_data_dir=session_dir,
                    headless=False,
                    viewport=viewport
                )
                self.page = self.context.pages[0] if self.context.pages else None
                if not self.page:
                    return {'success': False, 'error': '浏览器启动失败'}

                website_url = self.config.get('website', {}).get('url', '')
                self.page.goto(website_url, timeout=30000)
                time.sleep(5)

                url = self.page.url.lower()
                body_text = ""
                try:
                    body_text = self.page.inner_text("body")
                except:
                    pass

                if "login" not in url and ("退出" in body_text or "首页" in body_text or "报表" in body_text):
                    self.logged_in = True
                    return {'success': True, 'message': '已登录（会话有效）'}

                return {'success': True, 'message': '请手动完成登录'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

    def _close_browser(self):
        try:
            if self.context:
                self.context.close()
                self.context = None
            if self.playwright:
                self.playwright.stop()
                self.playwright = None
            self.browser = None
            self.page = None
        except Exception:
            pass

    def logout(self):
        with self.lock:
            self._close_browser()
            self.logged_in = False
            return {'success': True}

    def get_status(self):
        return {
            'success': True,
            'browser_running': self.is_alive(),
            'logged_in': self.logged_in
        }
