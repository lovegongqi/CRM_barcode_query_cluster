#!/usr/bin/env python3
"""Flask Web 应用 - 条码查询结果展示"""
import os
import sys

# 在导入任何其他模块之前设置环境变量来禁用 asyncio
os.environ['EVENTLET_NO_GREENDNS'] = '1'
os.environ['ASYNCIO_CORE_EVENT_LOOP'] = '0'

import re
import json
import time
import html as html_mod
import threading
import queue
import uuid
from collections import OrderedDict
from flask import Flask, render_template, request, jsonify, send_from_directory, Response, session, redirect
from datetime import datetime

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

RUNTIME_BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(__file__)
RESOURCE_BASE_DIR = getattr(sys, "_MEIPASS", RUNTIME_BASE_DIR)
CRM_CONFIG_PATH = os.path.join(RUNTIME_BASE_DIR, "config.json")

def load_crm_config():
    config_paths = [
        CRM_CONFIG_PATH,
    ]
    if os.path.exists("/.dockerenv"):
        config_paths.append(os.path.join(RESOURCE_BASE_DIR, "config.docker.example.json"))
    config_paths.append(os.path.join(RESOURCE_BASE_DIR, "config.example.json"))
    for path in config_paths:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError("未找到 config.json 或示例配置文件")

class CRMSession:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.lock = threading.Lock()
        self.logged_in = False
        self.needs_navigation = True  # 标记是否需要导航到报表页面
        self.last_report_error = ""

    def is_alive(self):
        try:
            if self.context and self.page:
                self.page.url
                return True
        except Exception:
            pass
        return False

    def _ensure_browser(self):
        """启动或复用浏览器实例"""
        cfg = load_crm_config()
        browser_cfg = cfg.get("browser", {})
        session_dir = cfg["session"]["state_path"]
        os.makedirs(session_dir, exist_ok=True)

        if self.is_alive():
            try:
                # 浏览器已经打开，保持使用
                return True
            except Exception as e:
                print(f"  [DEBUG] is_alive check failed: {e}")
                self._close_browser()

        # 关闭可能占用 session 的浏览器进程
        self._close_browser()
        self._cleanup_singleton_lock(session_dir)

        try:
            self.playwright = sync_playwright().start()
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=session_dir,
                headless=browser_cfg.get("headless", True),
                viewport=browser_cfg["viewport"],
                user_agent=browser_cfg.get("user_agent"),
                locale=browser_cfg.get("locale", "zh-CN"),
                timezone_id=browser_cfg.get("timezone_id", "Asia/Shanghai"),
                args=browser_cfg.get("args", [])
            )
        except Exception as e:
            error_msg = str(e)
            print(f"  [DEBUG] Browser launch error: {error_msg[:200]}")
            if any(key in error_msg for key in [
                "ProcessSingleton",
                "Failed to create a ProcessSingleton",
                "profile appears to be in use",
                "Chromium has locked the profile",
                "SingletonLock",
            ]):
                # 锁文件导致的错误，清理后重试
                self._close_browser()
                self._cleanup_singleton_lock(session_dir)
                self.playwright = sync_playwright().start()
                self.context = self.playwright.chromium.launch_persistent_context(
                    user_data_dir=session_dir,
                    headless=browser_cfg.get("headless", True),
                    viewport=browser_cfg["viewport"],
                    user_agent=browser_cfg.get("user_agent"),
                    locale=browser_cfg.get("locale", "zh-CN"),
                    timezone_id=browser_cfg.get("timezone_id", "Asia/Shanghai"),
                    args=browser_cfg.get("args", [])
                )
            else:
                raise

        self.page = self.context.pages[0] if self.context.pages else None
        if not self.page:
            return False
        self.page.goto(cfg["website"]["url"], timeout=30000)
        time.sleep(3)
        return True

    def _cleanup_singleton_lock(self, session_dir):
        """删除 Chromium 残留的单实例锁文件。"""
        singleton_names = {"SingletonLock", "SingletonSocket", "SingletonCookie"}
        profile_lock_paths = {
            os.path.abspath(os.path.join(session_dir, "LOCK")),
            os.path.abspath(os.path.join(session_dir, "Default", "LOCK")),
        }
        for root, _, files in os.walk(session_dir):
            for filename in files:
                lock_path = os.path.join(root, filename)
                if filename not in singleton_names and os.path.abspath(lock_path) not in profile_lock_paths:
                    continue
                if not os.path.lexists(lock_path):
                    continue
                try:
                    os.remove(lock_path)
                    print(f"  [DEBUG] 已清理浏览器锁文件: {lock_path}")
                except Exception as e:
                    print(f"  [DEBUG] 清理浏览器锁文件失败: {lock_path} {e}")

    def _is_current_page_logged_in(self):
        try:
            if not self.page:
                return False
            url = self.page.url.lower()
            body_text = self.page.inner_text("body")
            return (
                "login" not in url and
                (
                    "退出" in body_text or
                    "注销" in body_text or
                    "首页" in body_text or
                    "报表" in body_text or
                    "home" in url
                )
            )
        except Exception:
            return False

    def _wait_until_logged_in(self, timeout=18):
        end = time.time() + timeout
        while time.time() < end:
            if self._is_current_page_logged_in():
                self.logged_in = True
                self.needs_navigation = True
                return True
            time.sleep(0.8)
        return False

    def _reset_unfinished_login(self):
        """新登录开始前清掉半截验证码页或异常页，避免只能重启 Docker。"""
        if not self.is_alive():
            self.logged_in = False
            self.needs_navigation = True
            return False

        if self._is_current_page_logged_in():
            self.logged_in = True
            return True

        print("  [登录] 检测到未完成/异常登录状态，重开 CRM 登录入口...")
        self._close_browser()
        self.logged_in = False
        self.needs_navigation = True
        return False

    def login_step1(self, username, password):
        """Step1: 填账号密码 → 点登录 → 点发送验证码（你收到短信）"""
        if not HAS_PLAYWRIGHT:
            return False, "Playwright 未安装"
        with self.lock:
            try:
                if self._reset_unfinished_login():
                    return True, "已登录（会话有效）"
                if not self._ensure_browser():
                    return False, "浏览器启动失败"

                # 检查是否已登录（会话有效）
                time.sleep(1)
                if self._is_current_page_logged_in():
                    self.logged_in = True
                    return True, "已登录（会话有效）"

                # 在登录页，填账号密码
                time.sleep(1)

                # 填用户名
                for selector in [
                    "input[name='username']", "input[name='user']",
                    "input[name='logonUsername']", "#username", "#user", "input[type='text']",
                ]:
                    try:
                        el = self.page.query_selector(selector)
                        if el and el.is_visible():
                            el.click(); time.sleep(0.3)
                            el.press("Control+a"); time.sleep(0.1)
                            el.type(username, delay=100); time.sleep(0.5)
                            break
                    except:
                        continue

                # 填密码
                for selector in [
                    "input[name='password']", "input[name='pwd']",
                    "#password", "#pwd", "input[type='password']",
                ]:
                    try:
                        el = self.page.query_selector(selector)
                        if el and el.is_visible():
                            el.click(); time.sleep(0.3)
                            el.press("Control+a"); time.sleep(0.1)
                            el.type(password, delay=100); time.sleep(0.5)
                            break
                    except:
                        continue

                # 点击登录按钮
                for selector in [
                    "button[type='submit']", "input[type='submit']",
                    "#loginBtn", ".login-btn",
                    "button:has-text('登录')", "a:has-text('登录')",
                ]:
                    try:
                        el = self.page.query_selector(selector)
                        if el and el.is_visible():
                            el.click()
                            time.sleep(2)
                            break
                    except:
                        continue

                # 点击发送验证码
                for selector in [
                    "button:has-text('发送验证码')", "button:has-text('获取验证码')",
                    "a:has-text('发送验证码')", "a:has-text('获取验证码')",
                    "text=发送验证码",
                ]:
                    try:
                        el = self.page.query_selector(selector)
                        if el and el.is_visible():
                            el.click()
                            time.sleep(2)
                            return True, "captcha_sent"
                    except:
                        continue

                return True, "captcha_not_found"

            except Exception as e:
                return False, str(e)

    def _visible(self, el):
        try:
            return el and el.is_visible()
        except Exception:
            return False

    def _page_contexts(self):
        contexts = []
        pages = self.context.pages if self.context else []
        for page in reversed(pages):
            contexts.append((page, page))
            for frame in page.frames:
                if frame != page.main_frame:
                    contexts.append((page, frame))
        return contexts

    def _find_captcha_input(self):
        selectors = [
            "input[placeholder*='验证码']",
            "input[placeholder*='短信']",
            "input[name*='verify']",
            "input[name*='captcha']",
            "input[name*='code']",
            "input[id*='verify']",
            "input[id*='captcha']",
            "input[id*='code']",
        ]
        for page, scope in self._page_contexts():
            for selector in selectors:
                try:
                    el = scope.query_selector(selector)
                    if self._visible(el):
                        self.page = page
                        page.bring_to_front()
                        return el, scope
                except Exception:
                    pass

            try:
                inputs = scope.query_selector_all("input")
            except Exception:
                inputs = []
            for el in inputs:
                if not self._visible(el):
                    continue
                try:
                    attrs = " ".join([
                        el.get_attribute("id") or "",
                        el.get_attribute("name") or "",
                        el.get_attribute("placeholder") or "",
                        el.get_attribute("class") or "",
                    ]).lower()
                    input_type = (el.get_attribute("type") or "text").lower()
                    maxlength = el.get_attribute("maxlength") or ""
                    if input_type in ("password", "hidden"):
                        continue
                    if any(k in attrs for k in ["验证码", "verify", "captcha", "sms", "code", "auth"]):
                        self.page = page
                        page.bring_to_front()
                        return el, scope
                    if input_type in ("text", "tel", "number") and maxlength.isdigit() and int(maxlength) <= 8:
                        self.page = page
                        page.bring_to_front()
                        return el, scope
                except Exception:
                    pass
        return None, None

    def _click_confirm_near_captcha(self, scope, captcha_input=None):
        try:
            buttons = self.page.query_selector_all("button")
            for i, btn in enumerate(buttons):
                try:
                    if btn.is_visible():
                        text = btn.inner_text().strip()
                        if "确" in text and "定" in text:
                            box = btn.bounding_box()
                            if box:
                                x = box["x"] + box["width"] / 2
                                y = box["y"] + box["height"] / 2
                                self.page.mouse.click(x, y)
                            else:
                                btn.click()
                            print(f"  [OK] 已鼠标点击'确定' (索引{i}, 文本='{text}')")
                            return True
                except Exception:
                    pass
        except Exception as e:
            print(f"  [失败] 点击确定: {e}")
        return False

    def login_step2(self, captcha):
        """Step2: 收到验证码 → 等弹窗 → 填入 → 点确定 → 完成登录"""
        if not captcha:
            return False, "验证码不能为空"
        with self.lock:
            try:
                # 确保浏览器仍然活跃（可能是 step1 后重建的页面）
                if not self._ensure_browser():
                    return False, "浏览器启动失败"

                # 等待验证码输入框出现（最多10秒）
                captcha_input = None
                captcha_scope = None
                for _ in range(20):
                    captcha_input, captcha_scope = self._find_captcha_input()
                    if captcha_input:
                        break
                    time.sleep(0.5)

                if not captcha_input:
                    if self._wait_until_logged_in(timeout=5):
                        return True, "登录成功"
                    return False, "验证码输入框未出现，请点击重新登录后再获取验证码"

                captcha_input.click(); time.sleep(0.3)
                captcha_input.press("Control+a"); time.sleep(0.1)
                captcha_input.press("Backspace"); time.sleep(0.1)
                captcha_input.type(captcha, delay=150)
                time.sleep(0.5)

                # 点确定
                if not self._click_confirm_near_captcha(captcha_scope, captcha_input):
                    return False, "验证码已填入，但未找到确定按钮"
                if self._wait_until_logged_in(timeout=18):
                    return True, "登录成功"
                return False, "验证码可能错误，请重试，或点击重新登录"

            except Exception as e:
                return False, str(e)

    def login(self, username, password, captcha=None):
        """统一登录：可选传入验证码（有就自动填入并点确定）"""
        if not HAS_PLAYWRIGHT:
            return False, "Playwright 未安装"
        with self.lock:
            try:
                if not captcha and self._reset_unfinished_login():
                    return True, "已登录（会话有效）"

                if not self._ensure_browser():
                    return False, "浏览器启动失败"

                # 检查是否已登录
                time.sleep(1)
                if self._is_current_page_logged_in():
                    self.logged_in = True
                    return True, "已登录（会话有效）"

                # 在登录页，填账号密码
                time.sleep(1)

                # 填用户名
                for selector in [
                    "input[name='username']", "input[name='user']",
                    "input[name='logonUsername']", "#username", "#user", "input[type='text']",
                ]:
                    try:
                        el = self.page.query_selector(selector)
                        if el and el.is_visible():
                            el.click(); time.sleep(0.3)
                            el.press("Control+a"); time.sleep(0.1)
                            el.type(username, delay=100); time.sleep(0.5)
                            break
                    except:
                        continue

                # 填密码
                for selector in [
                    "input[name='password']", "input[name='pwd']",
                    "#password", "#pwd", "input[type='password']",
                ]:
                    try:
                        el = self.page.query_selector(selector)
                        if el and el.is_visible():
                            el.click(); time.sleep(0.3)
                            el.press("Control+a"); time.sleep(0.1)
                            el.type(password, delay=100); time.sleep(0.5)
                            break
                    except:
                        continue

                # 点击登录按钮
                for selector in [
                    "button[type='submit']", "input[type='submit']",
                    "#loginBtn", ".login-btn",
                    "button:has-text('登录')", "a:has-text('登录')",
                ]:
                    try:
                        el = self.page.query_selector(selector)
                        if el and el.is_visible():
                            el.click()
                            time.sleep(2)
                            break
                    except:
                        continue

                # ── 有验证码：直接填入并提交（step2不再重复点发送验证码） ──
                if captcha:
                    # 等待验证码输入框出现（最多10秒）
                    captcha_input = None
                    for _ in range(20):
                        for selector in [
                            "input[placeholder='验证码']",
                            "input[name='verifyCode']", "input[name='captcha']",
                        ]:
                            try:
                                el = self.page.query_selector(selector)
                                if el and el.is_visible():
                                    captcha_input = el
                                    break
                            except:
                                continue
                        if captcha_input:
                            break
                        time.sleep(0.5)
                    if captcha_input:
                        captcha_input.click(); time.sleep(0.3)
                        captcha_input.press("Control+a"); time.sleep(0.1)
                        captcha_input.press("Backspace"); time.sleep(0.1)
                        captcha_input.type(captcha, delay=150)
                        time.sleep(0.5)

                    # 点确定
                    buttons = self.page.query_selector_all("button")
                    for btn in buttons:
                        try:
                            if btn.is_visible():
                                text = btn.inner_text().strip()
                                if "确" in text and "定" in text:
                                    btn.click()
                                    time.sleep(5)  # 等待页面跳转
                                    break
                        except:
                            continue

                    if self._wait_until_logged_in(timeout=18):
                        return True, "登录成功"
                    return False, "验证码可能错误，请重试"

                # ── 无验证码：先点发送验证码，等验证码输入框出现 ──
                # 点击发送验证码
                for selector in [
                    "button:has-text('发送验证码')", "button:has-text('获取验证码')",
                    "a:has-text('发送验证码')", "a:has-text('获取验证码')",
                    "text=发送验证码",
                ]:
                    try:
                        el = self.page.query_selector(selector)
                        if el and el.is_visible():
                            el.click()
                            time.sleep(2)
                            break
                    except:
                        continue

                # 等待验证码输入框出现
                for _ in range(10):
                    for selector in [
                        "input[placeholder='验证码']",
                        "input[name='verifyCode']", "input[name='captcha']",
                    ]:
                        try:
                            el = self.page.query_selector(selector)
                            if el and el.is_visible():
                                return True, "captcha_required"
                        except:
                            continue
                    time.sleep(0.5)

                # 无验证码，检查是否直接登录成功
                url = self.page.url.lower()
                if "login" not in url:
                    self.logged_in = True
                    return True, "登录成功"

                return True, "please_login_manually"

            except Exception as e:
                return False, str(e)

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
            self.needs_navigation = True  # 重置导航标记
            return True

    def cancel_login(self):
        """取消未完成的登录流程，避免下次登录接着半截验证码页面执行。"""
        with self.lock:
            if not self.logged_in:
                self._close_browser()
                self.needs_navigation = True
            return True

    def check_login_status(self):
        """检查浏览器当前是否已登录"""
        with self.lock:
            if not self.is_alive():
                return False, "浏览器未启动"
            try:
                url = self.page.url.lower()
                if "login" in url or "登录" in url:
                    self.logged_in = False
                    return False, "仍在登录页，未登录"
                time.sleep(1)
                body_text = self.page.inner_text("body")
                if "退出" in body_text or "注销" in body_text:
                    self.logged_in = True
                    return True, "已登录"
                else:
                    self.logged_in = False
                    return False, "无法确认登录状态，请检查浏览器"
            except Exception as e:
                self.logged_in = False
                return False, str(e)

    def switch_to_report_tab(self):
        """切换到报表标签页"""
        try:
            pages = self.context.pages
            print(f"  [INFO] 当前共 {len(pages)} 个标签页")
            if len(pages) == 0:
                return False

            report_page = None
            saw_report_page = False
            for i, p in enumerate(pages):
                try:
                    print(f"    标签页{i+1}: {p.url}")
                    if "/EcoCrystalReports/EcoCrystalRp" in p.url:
                        saw_report_page = True
                        error = self._get_report_page_error(p)
                        if error:
                            self.last_report_error = error
                            print(f"  [关闭] 标签页{i+1} 是报表错误页: {error}")
                            try:
                                p.close()
                            except Exception:
                                pass
                            continue
                        input_box = self._find_barcode_input(p)
                        if input_box:
                            self.last_report_error = ""
                            report_page = p
                            print(f"  [找到] 标签页{i+1} 包含可用报表输入框")
                            break
                        report_page = p
                        print(f"  [找到] 标签页{i+1} 包含报表 URL，继续等待输入框")
                except Exception:
                    pass

            if report_page:
                self.page = report_page
                self.page.bring_to_front()
                print("  [切换] 已切换到报表标签页")
                time.sleep(2)
                return True

            if saw_report_page:
                print("  [失败] 报表标签页存在，但没有可用页面")
                return False

            pages = self.context.pages
            if len(pages) == 1:
                print("  [失败] 没有新标签页被打开")
                return False

            self.page = pages[-1]
            self.page.bring_to_front()
            print("  [切换] 已切换到最后一个标签页")
            time.sleep(2)
            try:
                content = self.page.inner_text("body")
                if "输入 barcode" in content or "确定" in content or "EcoCrystalRp" in self.page.url:
                    print("  [成功] 当前标签页是报表页")
                    return True
                else:
                    print(f"  [警告] 当前标签页可能不是报表，URL: {self.page.url}")
            except Exception as e:
                print(f"  [错误] 检查标签页内容失败: {e}")
            return False
        except Exception as e:
            print(f"  [错误] switch_to_report_tab失败: {e}")
            return False

    def close_report_tab(self):
        """关闭报表标签页，只保留CRM列表页"""
        try:
            pages = self.context.pages
            print(f"  [DEBUG] 关闭前共有 {len(pages)} 个标签页")
            pages_to_close = []
            crm_list_page = None

            for p in pages:
                try:
                    url = p.url
                    if "crmportal.ecowaterchina" in url and "/report/reportlist" in url:
                        crm_list_page = p
                    else:
                        pages_to_close.append(p)
                except Exception:
                    pages_to_close.append(p)

            for p in pages_to_close:
                try:
                    print(f"  [DEBUG] 已关闭标签页: {p.url}")
                    p.close()
                except Exception:
                    pass
            time.sleep(1)

            if crm_list_page:
                self.page = crm_list_page
                self.page.bring_to_front()
                print("  [DEBUG] 已切换到CRM列表页")
                time.sleep(2)
            elif self.context.pages:
                self.page = self.context.pages[0]
                self.page.bring_to_front()
        except Exception as e:
            print(f"  [错误] close_report_tab失败: {e}")

    def _find_barcode_input(self, page=None):
        try:
            target_page = page or self.page
            inputs = target_page.query_selector_all("input[name='CrystalReportViewer1_p0DiscreteValue']")
            for el in inputs:
                try:
                    if el.is_visible():
                        return el
                except Exception:
                    pass
            return inputs[0] if inputs else None
        except Exception:
            return None

    def _get_report_page_error(self, page=None):
        try:
            target_page = page or self.page
            if "/EcoCrystalReports/EcoCrystalRp" not in target_page.url:
                return ""
            body_text = target_page.inner_text("body", timeout=2000).strip()
        except Exception:
            return ""

        compact_text = re.sub(r"\s+", " ", body_text)
        if (
            "CrystalReportViewer1.ReportSourceID" in body_text or
            "CrystalReportSource" in body_text or
            "找不到由" in body_text
        ):
            return compact_text[:160]
        if compact_text.startswith("错误"):
            return compact_text[:160]
        return ""

    def _find_open_report_page(self, require_input=False, close_errors=False):
        report_page = None
        try:
            for p in reversed(self.context.pages):
                try:
                    if "/EcoCrystalReports/EcoCrystalRp" not in p.url:
                        continue

                    error = self._get_report_page_error(p)
                    if error:
                        self.last_report_error = error
                        print(f"  [关闭] 报表错误页: {error}")
                        if close_errors:
                            try:
                                p.close()
                            except Exception:
                                pass
                        continue

                    input_box = self._find_barcode_input(p)
                    if input_box:
                        self.last_report_error = ""
                        return p
                    if not require_input and not report_page:
                        report_page = p
                except Exception:
                    pass
        except Exception:
            pass
        return report_page

    def _reload_report_for_input(self):
        """刷新当前报表页，尝试回到条码输入界面"""
        try:
            if "/EcoCrystalReports/EcoCrystalRp" not in self.page.url:
                return None
            print("  [复用] 刷新当前报表标签页，等待条码输入框...")
            self.page.reload(wait_until="domcontentloaded", timeout=30000)
            for i in range(20):
                time.sleep(1)
                error = self._get_report_page_error()
                if error:
                    self.last_report_error = error
                    print(f"  [失败] 报表刷新后是错误页: {error}")
                    return None
                input_box = self._find_barcode_input()
                try:
                    if input_box and input_box.is_visible():
                        print(f"  [成功] 刷新后找到条码输入框（等待{i+1}秒）")
                        return input_box
                except Exception:
                    pass
            print("  [失败] 刷新后仍未找到条码输入框")
        except Exception as e:
            print(f"  [错误] 刷新报表页失败: {e}")
        return None

    def _find_input_in_open_report_tabs(self):
        """逐个复用已打开的报表标签页，只有都不可用时才让上层重走流程"""
        try:
            report_pages = []
            for p in self.context.pages:
                try:
                    if "/EcoCrystalReports/EcoCrystalRp" in p.url:
                        report_pages.append(p)
                except Exception:
                    pass

            for p in reversed(report_pages):
                try:
                    self.page = p
                    self.page.bring_to_front()
                    time.sleep(1)
                    error = self._get_report_page_error()
                    if error:
                        self.last_report_error = error
                        print(f"  [关闭] 已打开的报表标签页是错误页: {error}")
                        try:
                            p.close()
                        except Exception:
                            pass
                        continue
                    input_box = self._find_barcode_input()
                    if input_box and input_box.is_visible():
                        print("  [复用] 已在打开的报表标签页找到条码输入框")
                        return input_box

                    input_box = self._reload_report_for_input()
                    if input_box and input_box.is_visible():
                        return input_box
                except Exception as e:
                    print(f"  [错误] 报表标签页不可用: {e}")
            print("  [失败] 所有已打开报表标签页都不可用")
        except Exception as e:
            print(f"  [错误] 检查已打开报表标签页失败: {e}")
        return None

    def _open_report_from_current_list(self):
        """从当前报表列表页双击打开'查询条码所有信息'"""
        try:
            list_page = self.page
            self.page = list_page
            self.page.bring_to_front()
            self.last_report_error = ""
            time.sleep(1)

            pages_before = len(self.context.pages)
            print(f"  [INFO] 打开前有 {pages_before} 个标签页")

            try:
                report_link = self.page.get_by_text("查询条码所有信息", exact=True).first
                report_link.scroll_into_view_if_needed(timeout=5000)
                box = report_link.bounding_box()
                if not box:
                    print("  [失败] 未找到'查询条码所有信息'位置")
                    return False
                x = box['x'] + box['width'] / 2
                y = box['y'] + box['height'] / 2
                self.page.mouse.dblclick(x, y)
                print("  [触发] mouse.dblclick 已触发，等待报表输入框...")
            except Exception as e:
                print(f"  [错误] mouse双击失败: {e}")
                return False

            report_page = None
            for i in range(18):
                time.sleep(1)
                report_page = self._find_open_report_page(require_input=True, close_errors=True)
                if report_page:
                    print(f"  [成功] 已发现可用报表标签页（当前共{len(self.context.pages)}个）")
                    break
                print(f"  [等待] 第{i+1}秒，等待报表标签页出现...")

            if not report_page:
                try:
                    result = self.page.evaluate("""() => {
                        const elements = document.querySelectorAll('*');
                        for (const el of elements) {
                            if (el.textContent.trim() === '查询条码所有信息') {
                                const dblclickEvent = new MouseEvent('dblclick', {
                                    bubbles: true, cancelable: true, view: window
                                });
                                el.dispatchEvent(dblclickEvent);
                                return true;
                            }
                        }
                        return false;
                    }""")
                    if result:
                        print("  [兜底] JS dblclick 已触发，继续等待报表输入框...")
                    else:
                        print("  [失败] JS未找到'查询条码所有信息'文字")
                except Exception as e:
                    print(f"  [错误] JS兜底双击失败: {e}")

                for i in range(15):
                    time.sleep(1)
                    report_page = self._find_open_report_page(require_input=True, close_errors=True)
                    if report_page:
                        print(f"  [成功] 兜底后发现可用报表标签页（当前共{len(self.context.pages)}个）")
                        break
                    print(f"  [等待] 兜底第{i+1}秒，等待报表标签页出现...")

            if not report_page:
                print("  [失败] 未能打开'查询条码所有信息'新标签页")
                return False

            self.page = report_page
            self.page.bring_to_front()
            self.last_report_error = ""
            time.sleep(2)
            return True
        except Exception as e:
            print(f"  [错误] 打开报表失败: {e}")
            return False

    def prepare_next_report(self):
        """优先复用当前CRM报表列表页，必要时再完整导航"""
        try:
            if (
                "crmportal.ecowaterchina" in self.page.url.lower() and
                "/report/reportlist" in self.page.url.lower()
            ):
                print("  [导航] 已在CRM报表列表页，优先复用当前页面...")
                time.sleep(2)

                try:
                    if "查询条码所有信息" in self.page.inner_text("body"):
                        print("  [成功] 当前列表页已有'查询条码所有信息'，直接打开")
                        return self._open_report_from_current_list()
                except Exception:
                    pass

                page2_ok = False
                try:
                    page2_link = self.page.get_by_text("2", exact=True).first
                    if page2_link:
                        page2_link.click()
                        time.sleep(2)
                        if "查询条码所有信息" in self.page.inner_text("body"):
                            print("  [成功] 点击页码'2'成功")
                            page2_ok = True
                except Exception as e:
                    print(f"  [失败] 页码'2'链接方式失败: {e}")

                if not page2_ok:
                    try:
                        next_btn = self.page.query_selector("button:has-text('下一页'), a:has-text('下一页')")
                        if next_btn:
                            next_btn.click()
                            time.sleep(2)
                            if "查询条码所有信息" in self.page.inner_text("body"):
                                print("  [成功] 点击'下一页'成功")
                                page2_ok = True
                    except Exception as e:
                        print(f"  [失败] '下一页'按钮方式失败: {e}")

                if page2_ok:
                    return self._open_report_from_current_list()

                print("  [导航] 当前列表页未找到目标报表，改走完整导航")

            return self.navigate_to_report()
        except Exception as e:
            print(f"  [错误] 准备报表失败: {e}")
            return False

    def navigate_to_report(self):
        """导航到报表查询页面"""
        try:
            cfg = load_crm_config()

            print("  ========== 开始导航 ==========")
            time.sleep(5)
            print(f"  [OK] 当前URL: {self.page.url}")

            if "crmportal.ecowaterchina" not in self.page.url.lower():
                print("  [跳转] 当前不在 CRM 主页，正在跳转...")
                self.page.goto(cfg["website"]["url"], timeout=30000)
                time.sleep(3)

            # 点击报表管理
            print("  [步骤1] 点击'报表管理'")
            report_menu_ok = False
            for selector in ["text=报表管理", "a:has-text('报表管理')"]:
                try:
                    self.page.click(selector, timeout=5000)
                    time.sleep(3)
                    page_text = self.page.inner_text("body")
                    if "报表管理" in page_text:
                        print("  [成功] 点击'报表管理'成功")
                        report_menu_ok = True
                        break
                except Exception as e:
                    print(f"  [失败] selector={selector}, 错误: {e}")
            if not report_menu_ok:
                print("  [失败] 点击'报表管理'失败")
                return False

            # 点击水晶报表查看
            print("  [步骤2] 点击'水晶报表查看'")
            crystal_ok = False
            url_before = self.page.url
            for _ in range(10):
                try:
                    if self.page.is_visible("text=水晶报表查看"):
                        break
                except Exception:
                    pass
                time.sleep(1)
            for selector in ["text=水晶报表查看", "a:has-text('水晶报表查看')"]:
                try:
                    self.page.click(selector, timeout=10000)
                    time.sleep(3)
                    url_after = self.page.url
                    page_text = self.page.inner_text("body")
                    if url_after != url_before or "水晶报表" in page_text or "Crystal" in page_text:
                        print("  [成功] 点击'水晶报表查看'成功")
                        crystal_ok = True
                        break
                except Exception as e:
                    print(f"  [失败] selector={selector}, 错误: {e}")
            if not crystal_ok:
                print("  [失败] 点击'水晶报表查看'失败")
                return False

            # 这里保持在水晶报表列表页，继续翻到第2页。
            # main.py 也是点击“水晶报表查看”后直接在当前页翻页。
            time.sleep(2)

            # 翻到第2页，找到"查询条码所有信息"
            print("  [步骤3] 翻到第2页（查找'查询条码所有信息'）")
            time.sleep(3)
            page2_ok = False
            try:
                if "查询条码所有信息" in self.page.inner_text("body"):
                    page2_ok = True
                    print("  [成功] 已找到'查询条码所有信息'")
            except Exception:
                pass

            if not page2_ok:
                try:
                    spinbuttons = self.page.query_selector_all("spinbutton, input[type='number']")
                    for sb in spinbuttons:
                        try:
                            if sb.get_attribute("value") == "1":
                                sb.fill("2")
                                sb.press("Enter")
                                time.sleep(2)
                                if "查询条码所有信息" in self.page.inner_text("body"):
                                    print("  [成功] 输入页码2成功")
                                    page2_ok = True
                                    break
                        except Exception:
                            pass
                except Exception as e:
                    print(f"  [失败] 页码输入框方式失败: {e}")

            if not page2_ok:
                try:
                    next_btn = self.page.query_selector("button:has-text('下一页'), a:has-text('下一页')")
                    if next_btn:
                        next_btn.click()
                        time.sleep(2)
                        if "查询条码所有信息" in self.page.inner_text("body"):
                            print("  [成功] 点击'下一页'成功")
                            page2_ok = True
                except Exception as e:
                    print(f"  [失败] '下一页'按钮方式失败: {e}")

            if not page2_ok:
                try:
                    page2_link = self.page.get_by_text("2", exact=True).first
                    if page2_link:
                        page2_link.click()
                        time.sleep(2)
                        if "查询条码所有信息" in self.page.inner_text("body"):
                            print("  [成功] 点击页码'2'成功")
                            page2_ok = True
                except Exception as e:
                    print(f"  [失败] 页码'2'链接方式失败: {e}")

            if not page2_ok:
                print("  [失败] 无法自动翻到包含'查询条码所有信息'的页面")
                return False

            # 双击打开"查询条码所有信息"
            print("  [步骤4] 双击打开'查询条码所有信息'报表")
            if not self._open_report_from_current_list():
                return False
            print("  ========== 导航完成 ==========")
            return True

        except Exception as e:
            print(f"  [错误] 导航到报表失败: {e}")
            return False

    def query_barcode(self, barcode, log=None):
        def emit(message, level='info'):
            if log:
                log(message, level)

        with self.lock:
            if not self.is_alive():
                return False, "浏览器未启动"
            try:
                # 导航到报表页面（只在第一次查询时）
                if self.needs_navigation:
                    emit("准备打开条码报表页面")
                    print("  [导航] 准备打开报表页面...")
                    if not self.prepare_next_report():
                        if self.last_report_error:
                            return False, f"报表页面加载错误: {self.last_report_error}"
                        return False, "打开查询条码所有信息报表失败"
                    self.needs_navigation = False

                # 切换到报表标签页
                emit("切换到条码报表页")
                if not self.switch_to_report_tab():
                    emit("未找到已打开的条码报表页，重新打开条码报表", "warn")
                    self.needs_navigation = True
                    if not self.prepare_next_report():
                        if self.last_report_error:
                            return False, f"报表页面加载错误: {self.last_report_error}"
                        return False, "打开查询条码所有信息报表失败"
                    self.needs_navigation = False
                    if not self.switch_to_report_tab():
                        if self.last_report_error:
                            return False, f"报表页面加载错误: {self.last_report_error}"
                        return False, "未找到报表标签页"
                time.sleep(2)

                # 查找条码输入框
                print(f"  [DEBUG] 当前页面URL: {self.page.url}")
                page_text = self.page.inner_text("body")[:200]
                print(f"  [DEBUG] 页面内容: {page_text}")
                input_box = self._find_barcode_input()

                # 如果找不到，尝试重新导航
                if not input_box or not input_box.is_visible():
                    emit("未找到条码输入框，尝试刷新报表页", "warn")
                    input_box = self._reload_report_for_input()
                    if not input_box or not input_box.is_visible():
                        emit("刷新后仍未找到输入框，检查已打开报表标签", "warn")
                        input_box = self._find_input_in_open_report_tabs()
                        if not input_box or not input_box.is_visible():
                            emit("报表标签不可用，重新打开条码报表", "warn")
                            print("  [导航] 所有查询标签页不可用，关闭后重走流程...")
                            self.close_report_tab()
                            self.needs_navigation = True
                            if not self.prepare_next_report():
                                if self.last_report_error:
                                    return False, f"报表页面加载错误: {self.last_report_error}"
                                return False, "打开查询条码所有信息报表失败"
                            if not self.switch_to_report_tab():
                                if self.last_report_error:
                                    return False, f"报表页面加载错误: {self.last_report_error}"
                                return False, "未找到报表标签页"
                            time.sleep(5)
                            print(f"  [DEBUG] 重新导航后URL: {self.page.url}")
                            input_box = self._find_barcode_input()
                            if not input_box or not input_box.is_visible():
                                return False, "未找到条码输入框"

                for _ in range(30):
                    try:
                        imgs = self.page.query_selector_all("img")
                        has_loading = False
                        for img in imgs:
                            try:
                                if img.get_attribute("src") and "wait" in img.get_attribute("src"):
                                    parent = self.page.evaluate("(el) => el.offsetParent !== null", img)
                                    if parent:
                                        has_loading = True
                                        break
                            except:
                                pass
                        if not has_loading:
                            break
                    except:
                        break
                    time.sleep(1)

                input_box.click()
                time.sleep(0.3)
                input_box.press("Control+a")
                time.sleep(0.1)
                input_box.press("Backspace")
                time.sleep(0.1)
                emit(f"输入条码：{barcode}")
                input_box.type(barcode, delay=100)
                print(f"  已输入条码: {barcode}")

                emit("提交报表查询")
                print("  提交查询...")
                clicked = False
                try:
                    self.page.evaluate("if(typeof CrystalReportViewer1_submit === 'function') { CrystalReportViewer1_submit(); }")
                    clicked = True
                    time.sleep(1)
                except:
                    pass

                if not clicked:
                    try:
                        confirm_button = self.page.get_by_text("确定", exact=True).first
                        if confirm_button:
                            confirm_button.click()
                            clicked = True
                    except:
                        pass

                if not clicked:
                    try:
                        for link in self.page.query_selector_all("a"):
                            try:
                                if "确定" in (link.inner_text() or "").strip():
                                    link.click()
                                    clicked = True
                                    break
                            except:
                                pass
                    except:
                        pass

                if not clicked:
                    return False, "提交查询失败"

                emit("等待 CRM 生成报表")
                print("  等待报表处理...")
                max_wait = 60
                data_ready = False
                for wait_i in range(max_wait):
                    time.sleep(1)
                    try:
                        js_check = self.page.evaluate("""() => {
                            const iframes = document.querySelectorAll('iframe');
                            for (const iframe of iframes) {
                                try {
                                    const doc = iframe.contentDocument || iframe.contentWindow?.document;
                                    if (doc && doc.body) {
                                        const text = doc.body.innerText || '';
                                        if (text.length > 500 && !text.includes('正在处理') && !text.includes('请稍候')) {
                                            return { ready: true, length: text.length };
                                        }
                                    }
                                } catch(e) {}
                            }
                            return { ready: false, length: 0 };
                        }""")
                        if js_check.get('ready'):
                            data_ready = True
                            emit("报表数据已加载")
                            print(f"  报表已加载完成（{js_check['length']} 字符）")
                            break
                        elif (wait_i + 1) % 10 == 0:
                            emit(f"报表仍在加载，已等待 {wait_i + 1} 秒", "dim")
                            print(f"  ...已等待 {wait_i + 1} 秒，继续等待...")
                    except:
                        pass

                if not data_ready:
                    print("  等待超时，尝试提取可能的数据...")

                html_content = ""
                try:
                    html_content = self.page.evaluate("""() => {
                        const iframes = document.querySelectorAll('iframe');
                        let html = '';
                        for (const iframe of iframes) {
                            try {
                                const doc = iframe.contentDocument || iframe.contentWindow?.document;
                                if (doc && doc.body) html += doc.body.innerHTML;
                            } catch(e) {}
                        }
                        return html;
                    }""")
                except:
                    pass

                if html_content and len(html_content.strip()) > 1000:
                    emit("保存条码查询结果")
                    html_dir = BARCODE_DIR
                    os.makedirs(html_dir, exist_ok=True)
                    output_file = os.path.join(html_dir, f"{barcode}.html")
                    with open(output_file, "w", encoding="utf-8") as f:
                        f.write(html_content)
                    try:
                        fields = extract_fields_from_html(output_file)
                        refresh_product_library_from_query_fields(barcode, fields, emit)
                    except Exception as e:
                        emit(f"条码匹配自动更新失败：{e}", "warn")
                    self.needs_navigation = False
                    return True, barcode
                else:
                    self.needs_navigation = False
                    return False, "查询结果为空"

            except Exception as e:
                self.needs_navigation = True
                return False, str(e)

    def _move_create_url(self):
        cfg = load_crm_config()
        base = cfg["website"]["url"].rstrip("/")
        return f"{base}/?#/moveRelated/create"

    def _move_list_url(self):
        cfg = load_crm_config()
        base = cfg["website"]["url"].rstrip("/")
        return f"{base}/?#/moveRelated/list"

    def _has_transfer_create_form(self):
        try:
            body_text = self.page.inner_text("body", timeout=3000)
            return "移库类型" in body_text and bool(self._input_by_label("移库类型"))
        except Exception:
            return False

    def _return_to_move_list(self):
        try:
            self.page.goto(self._move_list_url(), wait_until="domcontentloaded", timeout=20000)
            time.sleep(1)
            self.needs_navigation = True
            return True
        except Exception:
            self.needs_navigation = True
            return False

    def _open_transfer_create_form(self, emit):
        emit("打开 CRM 移库单新增页面...")
        self._return_to_move_list()
        try:
            self.page.wait_for_function(
                "() => document.body && /移库单/.test(document.body.innerText || '')",
                timeout=10000
            )
        except Exception:
            pass
        if not self._click_top_button("新增"):
            self.page.goto(self._move_create_url(), wait_until="domcontentloaded", timeout=30000)
        for _ in range(12):
            time.sleep(0.8)
            if self._has_transfer_create_form():
                return True
        body_text = ""
        try:
            body_text = re.sub(r"\s+", " ", self.page.inner_text("body", timeout=2000))[:180]
        except Exception:
            pass
        emit(f"未识别到新增移库表单，当前页面：{body_text or self.page.url}", "warn")
        return False

    def _set_input_by_label(self, label, value):
        return self.page.evaluate("""({ label, value }) => {
            const clean = (text) => (text || '').replace(/\\s+/g, '');
            const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            const wanted = clean(label);
            const candidates = Array.from(document.querySelectorAll('label, span, div, p, td')).reverse();
            for (const node of candidates) {
                if (!visible(node)) continue;
                const text = clean(node.innerText || node.textContent || '');
                if (!text || !text.includes(wanted)) continue;
                const roots = [];
                const closest = node.closest('.el-form-item, .ant-form-item, tr, .form-group');
                if (closest) roots.push(closest);
                for (let root = node.parentElement, i = 0; i < 5 && root; i++, root = root.parentElement) {
                    if (!roots.includes(root)) roots.push(root);
                }
                for (const root of roots) {
                    const input = Array.from(root.querySelectorAll('input:not([disabled]), textarea:not([disabled])')).find(visible);
                    if (!input) continue;
                    input.focus();
                    input.value = value;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            }
            return false;
        }""", {"label": label, "value": str(value)})

    def _input_by_label(self, label):
        handle = self.page.evaluate_handle("""(label) => {
            const clean = (text) => (text || '').replace(/\\s+/g, '');
            const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            const wanted = clean(label);
            const formItems = Array.from(document.querySelectorAll('.el-form-item, .ant-form-item, .form-group, tr'))
                .filter(visible)
                .reverse();
            for (const item of formItems) {
                const labelEl = item.querySelector('.el-form-item__label, label, th, td:first-child');
                const labelText = clean(labelEl ? (labelEl.innerText || labelEl.textContent || '') : '');
                if (!labelText || !labelText.includes(wanted)) continue;
                const input = Array.from(item.querySelectorAll('input:not([disabled]), textarea:not([disabled])')).find(visible);
                if (input) return input;
            }
            const candidates = Array.from(document.querySelectorAll('label, span, div, p, td')).reverse();
            for (const node of candidates) {
                if (!visible(node)) continue;
                const text = clean(node.innerText || node.textContent || '');
                if (!text || !text.includes(wanted)) continue;
                const roots = [];
                const closest = node.closest('.el-form-item, .ant-form-item, tr, .form-group');
                if (closest) roots.push(closest);
                for (let root = node.parentElement, i = 0; i < 5 && root; i++, root = root.parentElement) {
                    if (!roots.includes(root)) roots.push(root);
                }
                for (const root of roots) {
                    const inputs = Array.from(root.querySelectorAll('input:not([disabled]), textarea:not([disabled])'));
                    const input = inputs.find(visible);
                    if (input) return input;
                }
            }
            return null;
        }""", label)
        element = handle.as_element()
        return element if element else None

    def _click_form_control_by_label(self, label):
        try:
            return self.page.evaluate("""(label) => {
                const clean = (text) => (text || '').replace(/\\s+/g, '');
                const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                const wanted = clean(label);
                const items = Array.from(document.querySelectorAll('.el-form-item, .ant-form-item, .form-group, tr'))
                    .filter(visible)
                    .reverse();
                for (const item of items) {
                    const labelEl = item.querySelector('.el-form-item__label, label, th, td:first-child');
                    const labelText = clean(labelEl ? (labelEl.innerText || labelEl.textContent || '') : '');
                    if (!labelText || !labelText.includes(wanted)) continue;
                    const target = Array.from(item.querySelectorAll('.el-select, .el-input, input, textarea'))
                        .find(visible);
                    if (!target) return false;
                    target.scrollIntoView({ block: 'center', inline: 'center' });
                    target.click();
                    return true;
                }
                return false;
            }""", label)
        except Exception:
            return False

    def _input_value_by_label(self, label):
        try:
            return self.page.evaluate("""(label) => {
                const clean = (text) => (text || '').replace(/\\s+/g, '');
                const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                const wanted = clean(label);
                const items = Array.from(document.querySelectorAll('.el-form-item, .ant-form-item, .form-group, tr'))
                    .filter(visible)
                    .reverse();
                for (const item of items) {
                    const labelEl = item.querySelector('.el-form-item__label, label, th, td:first-child');
                    const labelText = clean(labelEl ? (labelEl.innerText || labelEl.textContent || '') : '');
                    if (!labelText || !labelText.includes(wanted)) continue;
                    const input = Array.from(item.querySelectorAll('input, textarea, .el-select__tags-text')).find(visible);
                    if (!input) return '';
                    return clean(input.value || input.innerText || input.textContent || '');
                }
                return '';
            }""", label)
        except Exception:
            return ""

    def _click_dropdown_text(self, text, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            try:
                clicked = self.page.evaluate("""(text) => {
                    const nodes = Array.from(document.querySelectorAll(
                        '.el-select-dropdown__item, .el-autocomplete-suggestion li, li, [role="option"]'
                    ));
                    const target = nodes.find(el => {
                        const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        return visible && (el.innerText || el.textContent || '').trim().includes(text);
                    });
                    if (!target) return false;
                    target.click();
                    return true;
                }""", text)
                if clicked:
                    time.sleep(0.5)
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _select_input_by_label(self, label, value):
        input_el = self._input_by_label(label)
        if not input_el:
            return False
        try:
            input_el.scroll_into_view_if_needed(timeout=3000)
            input_el.click(timeout=5000)
        except Exception:
            clicked = self.page.evaluate("""(el) => {
                if (!el) return false;
                el.focus();
                el.click();
                return true;
            }""", input_el)
            if not clicked:
                return False
        time.sleep(0.2)
        input_el.press("Control+a")
        input_el.type(str(value), delay=80)
        time.sleep(1.5)
        if self._click_dropdown_text(str(value), timeout=8):
            return True
        if self._click_form_control_by_label(label):
            time.sleep(0.5)
            input_el = self._input_by_label(label)
            if input_el:
                try:
                    input_el.press("Control+a")
                    input_el.type(str(value), delay=80)
                    time.sleep(1.2)
                except Exception:
                    pass
            if self._click_dropdown_text(str(value), timeout=5):
                return True
        input_el.press("ArrowDown")
        time.sleep(0.3)
        input_el.press("Enter")
        time.sleep(0.5)
        return bool(self._input_value_by_label(label))

    def _norm_code(self, value):
        return re.sub(r"\s+", "", _clean_export_value(value)).upper()

    def _click_field_action_by_label(self, label):
        clicked = self.page.evaluate("""(label) => {
            const clean = (text) => (text || '').replace(/\\s+/g, '');
            const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            const wanted = clean(label);
            const dialogs = Array.from(document.querySelectorAll('.el-dialog, [role="dialog"], .modal'))
                .filter(visible)
                .reverse();
            dialogs.push(document.body);
            for (const scope of dialogs) {
                const items = Array.from(scope.querySelectorAll('.el-form-item, .ant-form-item, .form-group, tr'))
                    .filter(visible)
                    .reverse();
                for (const item of items) {
                    const labelEl = item.querySelector('.el-form-item__label, label, th, td:first-child');
                    const labelText = clean(labelEl ? (labelEl.innerText || labelEl.textContent || '') : '');
                    if (!labelText || !labelText.includes(wanted)) continue;
                    const targets = Array.from(item.querySelectorAll(
                        'button:not([disabled]), a, .el-input__suffix, .el-input-group__append, .el-icon-search, i[class*="search"], svg'
                    )).filter(visible);
                    const target = targets.find(el => !['INPUT', 'TEXTAREA'].includes(el.tagName)) || targets[targets.length - 1];
                    if (target) {
                        target.click();
                        return true;
                    }
                }
            }
            return false;
        }""", label)
        if clicked:
            time.sleep(1)
        return bool(clicked)

    def _set_dialog_input_by_label(self, label, value):
        return self.page.evaluate("""({ label, value }) => {
            const clean = (text) => (text || '').replace(/\\s+/g, '');
            const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            const setValue = (input, nextValue) => {
                const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                input.focus();
                if (setter) setter.call(input, nextValue);
                else input.value = nextValue;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                input.blur();
            };
            const wanted = clean(label);
            const dialogs = Array.from(document.querySelectorAll('.el-dialog, [role="dialog"], .modal'))
                .filter(visible)
                .reverse();
            for (const dialog of dialogs) {
                const items = Array.from(dialog.querySelectorAll('.el-form-item, .ant-form-item, .form-group, tr'))
                    .filter(visible)
                    .reverse();
                for (const item of items) {
                    const labelEl = item.querySelector('.el-form-item__label, label, th, td:first-child');
                    const labelText = clean(labelEl ? (labelEl.innerText || labelEl.textContent || '') : '');
                    if (!labelText || !labelText.includes(wanted)) continue;
                    const input = Array.from(item.querySelectorAll('input:not([disabled]), textarea:not([disabled])')).find(visible);
                    if (!input) continue;
                    setValue(input, value);
                    return true;
                }
            }
            return false;
        }""", {"label": label, "value": str(value)})

    def _set_any_dialog_input(self, value):
        return self.page.evaluate("""(value) => {
            const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            const setValue = (input, nextValue) => {
                const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                input.focus();
                if (setter) setter.call(input, nextValue);
                else input.value = nextValue;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                input.blur();
            };
            const dialogs = Array.from(document.querySelectorAll('.el-dialog, [role="dialog"], .modal'))
                .filter(visible)
                .reverse();
            for (const dialog of dialogs) {
                const input = Array.from(dialog.querySelectorAll('input:not([disabled]), textarea:not([disabled])'))
                    .filter(visible)
                    .find(el => {
                        const type = (el.getAttribute('type') || 'text').toLowerCase();
                        return !['hidden', 'checkbox', 'radio'].includes(type);
                    });
                if (!input) continue;
                setValue(input, value);
                return true;
            }
            return false;
        }""", str(value))

    def _clear_dialog_inputs(self):
        try:
            self.page.evaluate("""() => {
                const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                const setValue = (input, nextValue) => {
                    const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                    if (setter) setter.call(input, nextValue);
                    else input.value = nextValue;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                };
                const dialogs = Array.from(document.querySelectorAll('.el-dialog, [role="dialog"], .modal'))
                    .filter(visible)
                    .reverse();
                const dialog = dialogs[0];
                if (!dialog) return;
                Array.from(dialog.querySelectorAll('input:not([disabled]), textarea:not([disabled])'))
                    .filter(visible)
                    .forEach(input => {
                        const type = (input.getAttribute('type') || 'text').toLowerCase();
                        if (!['hidden', 'checkbox', 'radio'].includes(type)) setValue(input, '');
                    });
            }""")
        except Exception:
            pass

    def _click_dialog_search_button(self):
        clicked = self.page.evaluate("""() => {
            const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            const dialogs = Array.from(document.querySelectorAll('.el-dialog, [role="dialog"], .modal'))
                .filter(visible)
                .reverse();
            for (const dialog of dialogs) {
                const nodes = Array.from(dialog.querySelectorAll(
                    'button:not([disabled]), a, .el-button, .el-input-group__append, .el-icon-search, i[class*="search"], svg'
                )).filter(visible);
                const textTarget = nodes.find(el => {
                    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, '');
                    return text && ['查询', '搜索', '查找', '检索'].some(word => text.includes(word));
                });
                const iconTarget = nodes.find(el => {
                    const cls = el.getAttribute('class') || '';
                    const title = el.getAttribute('title') || '';
                    const aria = el.getAttribute('aria-label') || '';
                    return /search|查询|搜索|查找|检索/i.test(cls + title + aria);
                });
                const target = (textTarget || iconTarget)?.closest?.('button,a,.el-button,.el-input-group__append') || textTarget || iconTarget;
                if (target) {
                    target.click();
                    return true;
                }
            }
            return false;
        }""")
        if clicked:
            time.sleep(1)
            return True
        try:
            self.page.keyboard.press("Enter")
            time.sleep(1)
            return True
        except Exception:
            return False

    def _set_dialog_search_keyword(self, value):
        return self.page.evaluate("""(value) => {
            const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            const setValue = (input, nextValue) => {
                const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                input.focus();
                if (setter) setter.call(input, nextValue);
                else input.value = nextValue;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
            };
            const dialogs = Array.from(document.querySelectorAll('.el-dialog, [role="dialog"], .modal'))
                .filter(visible)
                .reverse();
            for (const dialog of dialogs) {
                const inputs = Array.from(dialog.querySelectorAll('input:not([disabled]), textarea:not([disabled])'))
                    .filter(visible)
                    .filter(input => {
                        const type = (input.getAttribute('type') || 'text').toLowerCase();
                        return !['hidden', 'checkbox', 'radio'].includes(type);
                    });
                const keywordInput = inputs.find(input => /搜索|查找|记录/.test(input.getAttribute('placeholder') || '')) || inputs[0];
                if (!keywordInput) continue;
                setValue(keywordInput, value);
                return true;
            }
            return false;
        }""", str(value))

    def _select_dialog_search_field(self, field_text):
        clicked = self.page.evaluate("""() => {
            const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            const dialogs = Array.from(document.querySelectorAll('.el-dialog, [role="dialog"], .modal'))
                .filter(visible)
                .reverse();
            for (const dialog of dialogs) {
                const selects = Array.from(dialog.querySelectorAll('.el-select, [class*="select"]'))
                    .filter(visible);
                const target = selects.find(el => {
                    const input = el.querySelector('input');
                    const placeholder = input?.getAttribute('placeholder') || '';
                    return /请选择|产品|编码|名称/.test(placeholder) || input?.readOnly;
                }) || selects[0];
                if (!target) continue;
                target.scrollIntoView({ block: 'center', inline: 'center' });
                target.click();
                return true;
            }
            return false;
        }""")
        if not clicked:
            return False
        time.sleep(0.5)
        return self._click_dropdown_text(field_text, timeout=5)

    def _search_lookup_dialog(self, keyword, field_text="产品编码"):
        try:
            dialog = self.page.locator(".el-dialog:visible, [role='dialog']:visible, .modal:visible").last
            field_input = dialog.locator("input[placeholder*='请选择']").first
            if field_input.count():
                field_input.click(timeout=3000, force=True)
                time.sleep(0.5)
                if not self._click_dropdown_text(field_text, timeout=5):
                    return False
            keyword_input = dialog.locator("input[placeholder*='搜索'], input[placeholder*='查找'], input").first
            keyword_input.fill(str(keyword), timeout=3000)
            keyword_input.press("Enter")
            time.sleep(0.5)
            search_button = dialog.locator("button:has-text('搜索'), .el-button:has-text('搜索'), button:has-text('查询'), .el-button:has-text('查询')").first
            if search_button.count():
                search_button.click(timeout=3000, force=True)
            else:
                self._click_dialog_search_button()
            time.sleep(1.5)
            return True
        except Exception:
            pass
        if not self._select_dialog_search_field(field_text):
            return False
        time.sleep(0.3)
        if not self._set_dialog_search_keyword(keyword):
            return False
        self._click_dialog_search_button()
        time.sleep(1.5)
        return True

    def _click_product_search_result(self, product_code, product_name=""):
        clicked = self.page.evaluate("""({ productCode, productName }) => {
            const clean = (text) => (text || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            const dialogs = Array.from(document.querySelectorAll('.el-dialog, [role="dialog"], .modal'))
                .filter(visible)
                .reverse();
            for (const dialog of dialogs) {
                const rows = Array.from(dialog.querySelectorAll('tbody tr, .el-table__body tr, tr')).filter(visible);
                let fallback = null;
                for (const row of rows) {
                    const text = clean(row.innerText || row.textContent || '');
                    if (!text) continue;
                    if (productCode && text.includes(productCode)) {
                        row.click();
                        return true;
                    }
                    if (!fallback && productName && text.includes(productName)) {
                        fallback = row;
                    }
                }
                if (fallback) {
                    fallback.click();
                    return true;
                }
            }
            return false;
        }""", {"productCode": str(product_code or ""), "productName": str(product_name or "")})
        if clicked:
            time.sleep(0.8)
        return bool(clicked)

    def _visible_dialog_text(self):
        try:
            return self.page.evaluate("""() => {
                const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                const dialogs = Array.from(document.querySelectorAll('.el-dialog, [role="dialog"], .modal'))
                    .filter(visible)
                    .reverse();
                return dialogs
                    .map(dialog => (dialog.innerText || dialog.textContent || '').replace(/\\s+/g, ' ').trim())
                    .filter(Boolean)
                    .slice(0, 2)
                    .join(' | ')
                    .slice(0, 500);
            }""") or ""
        except Exception:
            return ""

    def _dialog_inputs_snapshot(self):
        try:
            return self.page.evaluate("""() => {
                const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                const clean = (text) => (text || '').replace(/\\s+/g, ' ').trim();
                const dialogs = Array.from(document.querySelectorAll('.el-dialog, [role="dialog"], .modal'))
                    .filter(visible)
                    .reverse();
                const dialog = dialogs[0];
                if (!dialog) return '';
                return Array.from(dialog.querySelectorAll('input:not([disabled]), textarea:not([disabled])'))
                    .filter(visible)
                    .map((input, idx) => {
                        const item = input.closest('.el-form-item, .ant-form-item, .form-group, tr');
                        const labelEl = item?.querySelector?.('.el-form-item__label, label, th, td:first-child');
                        const label = clean(labelEl ? (labelEl.innerText || labelEl.textContent || '') : '');
                        return `${idx + 1}.label=${label || '-'},placeholder=${input.getAttribute('placeholder') || '-'},name=${input.getAttribute('name') || '-'},value=${input.value || '-'}`;
                    })
                    .join('；');
            }""") or ""
        except Exception:
            return ""

    def _search_product_by_code(self, product_code, product_name=""):
        if not product_code:
            return False, "缺少产品编码，无法按编码搜索产品"
        if not self._click_field_action_by_label("产品名称"):
            return False, "未找到产品名称右侧搜索按钮"
        if self._search_lookup_dialog(product_code, "产品编码"):
            if self._click_product_search_result(product_code, product_name):
                self._click_dialog_button("确定")
                time.sleep(0.8)
                return True, ""
        if product_name and self._search_lookup_dialog(product_name, "产品名称"):
            if self._click_product_search_result(product_code, product_name):
                self._click_dialog_button("确定")
                time.sleep(0.8)
                return True, ""
        search_attempts = [
            ("产品编码", product_code),
            ("物料编码", product_code),
            ("产品代码", product_code),
            ("物料代码", product_code),
            ("编码", product_code),
            ("物料名称", product_name),
            ("产品名称", product_name),
            ("名称", product_name),
        ]
        tried = []
        for label, value in search_attempts:
            value = _clean_export_value(value)
            if not value:
                continue
            tried.append(f"{label}={value}")
            self._clear_dialog_inputs()
            if not self._set_dialog_input_by_label(label, value):
                if label == "编码" and not self._set_any_dialog_input(value):
                    continue
                if label != "编码":
                    continue
            self._click_dialog_search_button()
            time.sleep(1.5)
            if self._click_product_search_result(product_code, product_name):
                self._click_dialog_button("确定")
                time.sleep(0.8)
                return True, ""
        dialog_text = self._visible_dialog_text()
        suffix = f"，弹窗内容：{dialog_text}" if dialog_text else ""
        inputs = self._dialog_inputs_snapshot()
        input_suffix = f"，输入框：{inputs}" if inputs else ""
        return False, f"产品搜索结果未找到编码 {product_code}（已尝试 {'；'.join(tried)}）{input_suffix}{suffix}"

    def _select_product_with_code_check(self, product_name, product_code, emit=None):
        if not self._select_input_by_label("产品名称", product_name):
            return False, "未找到产品名称输入框"
        expected_code = self._norm_code(product_code)
        if not expected_code:
            return True, ""
        time.sleep(0.8)
        actual_code = self._norm_code(self._input_value_by_label("产品编码"))
        if actual_code == expected_code:
            return True, ""
        if emit:
            emit(f"产品名称带出的编码 {actual_code or '空'} 与条码编码 {expected_code} 不一致，改用编码搜索", "warn")
        ok, msg = self._search_product_by_code(expected_code, product_name)
        if not ok:
            return False, msg
        actual_code = self._norm_code(self._input_value_by_label("产品编码"))
        if actual_code and actual_code != expected_code:
            return False, f"编码搜索后仍不一致：当前 {actual_code}，应为 {expected_code}"
        return True, ""

    def _select_transfer_type(self, transfer_type):
        if not self._click_form_control_by_label("移库类型"):
            input_el = self._input_by_label("移库类型")
            if not input_el:
                return False
            try:
                input_el.scroll_into_view_if_needed(timeout=3000)
                input_el.click(timeout=5000)
            except Exception:
                clicked = self.page.evaluate("""(el) => {
                    if (!el) return false;
                    el.focus();
                    el.click();
                    return true;
                }""", input_el)
                if not clicked:
                    return False
        time.sleep(0.5)
        if self._click_dropdown_text(transfer_type, timeout=5):
            time.sleep(0.5)
            return self._input_value_by_label("移库类型") == transfer_type
        input_el = self._input_by_label("移库类型")
        if not input_el:
            return False
        input_el.press("ArrowDown")
        time.sleep(0.2)
        input_el.press("Enter")
        time.sleep(0.5)
        return self._input_value_by_label("移库类型") == transfer_type

    def _click_top_button(self, text):
        try:
            btn = self.page.get_by_text(text, exact=True).first
            btn.click(timeout=5000)
            time.sleep(1)
            return True
        except Exception:
            pass
        return self.page.evaluate("""(text) => {
            const nodes = Array.from(document.querySelectorAll('button, a, span'));
            const target = nodes.find(el => {
                const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                return visible && (el.innerText || el.textContent || '').trim().includes(text);
            });
            if (!target) return false;
            target.click();
            return true;
        }""", text)

    def _click_section_action(self, section_title, action_text):
        clicked = self.page.evaluate("""({ sectionTitle, actionText }) => {
            const clean = (text) => (text || '').replace(/\\s+/g, '');
            const sections = Array.from(document.querySelectorAll('*')).filter(el => {
                const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                return visible && clean(el.innerText || el.textContent || '') === clean(sectionTitle);
            });
            for (const section of sections) {
                let root = section.parentElement;
                for (let i = 0; i < 6 && root; i++, root = root.parentElement) {
                    const buttons = Array.from(root.querySelectorAll('button, a'));
                    const target = buttons.find(btn => {
                        const visible = !!(btn.offsetWidth || btn.offsetHeight || btn.getClientRects().length);
                        return visible && (btn.innerText || btn.textContent || '').includes(actionText);
                    });
                    if (target) {
                        target.click();
                        return true;
                    }
                }
            }
            return false;
        }""", {"sectionTitle": section_title, "actionText": action_text})
        if clicked:
            time.sleep(1)
        return clicked

    def _click_dialog_button(self, text):
        clicked = self.page.evaluate("""(text) => {
            const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            const dialogs = Array.from(document.querySelectorAll('.el-dialog, [role="dialog"], .modal'))
                .filter(visible)
                .reverse();
            dialogs.push(document.body);
            for (const dialog of dialogs) {
                const buttons = Array.from(dialog.querySelectorAll('button, a'));
                const target = buttons.reverse().find(btn => {
                    return visible(btn) && (btn.innerText || btn.textContent || '').trim().includes(text);
                });
                if (target) {
                    target.click();
                    return true;
                }
            }
            return false;
        }""", text)
        if clicked:
            time.sleep(1.2)
        return clicked

    def _visible_message(self):
        try:
            text = self.page.evaluate("""() => {
                const selectors = ['.el-message', '.el-notification', '.el-form-item__error', '.el-dialog', '.toast'];
                return selectors.flatMap(sel => Array.from(document.querySelectorAll(sel)))
                    .filter(el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length))
                    .map(el => (el.innerText || el.textContent || '').trim())
                    .filter(Boolean)
                    .join(' | ');
            }""")
            return re.sub(r"\s+", " ", text or "").strip()
        except Exception:
            return ""

    def _form_diagnostics(self):
        try:
            return self.page.evaluate("""() => {
                const clean = (text) => (text || '').replace(/\\s+/g, ' ').trim();
                const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                const errors = Array.from(document.querySelectorAll('.el-form-item__error, .is-error .el-form-item__label'))
                    .filter(visible)
                    .map(el => clean(el.innerText || el.textContent || ''))
                    .filter(Boolean);
                const fields = Array.from(document.querySelectorAll('.el-form-item, tr, .form-group, .ant-form-item'))
                    .filter(visible)
                    .map(root => {
                        const label = clean((root.querySelector('label, .el-form-item__label, th, td:first-child') || {}).innerText || '');
                        if (!label) return null;
                        const valueNode = root.querySelector('input, textarea, .el-input__inner, .el-select__tags-text, .el-select .el-input__inner');
                        const value = valueNode ? clean(valueNode.value || valueNode.innerText || valueNode.textContent || '') : '';
                        const errorNode = root.querySelector('.el-form-item__error');
                        const error = errorNode ? clean(errorNode.innerText || errorNode.textContent || '') : '';
                        return { label, value, error };
                    })
                    .filter(row => row && (row.label || row.value || row.error))
                    .slice(0, 30);
                return { errors, fields };
            }""")
        except Exception:
            return {}

    def _format_form_diagnostics(self):
        diag = self._form_diagnostics() or {}
        parts = []
        errors = [str(x) for x in diag.get("errors", []) if str(x).strip()]
        if errors:
            parts.append("页面错误：" + " / ".join(errors[:8]))
        field_parts = []
        for row in diag.get("fields", [])[:16]:
            label = str(row.get("label") or "").strip()
            value = str(row.get("value") or "").strip()
            error = str(row.get("error") or "").strip()
            if not label:
                continue
            field = f"{label}={value or '空'}"
            if error:
                field += f"({error})"
            field_parts.append(field)
        if field_parts:
            parts.append("字段：" + "；".join(field_parts))
        return "；".join(parts)

    def _order_number(self):
        try:
            return self.page.evaluate("""() => {
                const clean = (text) => (text || '').replace(/\\s+/g, '');
                const candidates = Array.from(document.querySelectorAll('label, span, div, p, td'));
                for (const node of candidates) {
                    if (!clean(node.innerText || node.textContent || '').includes('移库单号')) continue;
                    let root = node;
                    for (let i = 0; i < 8 && root; i++, root = root.parentElement) {
                        const input = root.querySelector('input');
                        if (input && input.value) return input.value.trim();
                    }
                }
                return '';
            }""")
        except Exception:
            return ""

    def _save_transfer_header(self):
        if not self._click_top_button("保存"):
            return False, "未找到保存按钮"
        for _ in range(20):
            time.sleep(0.8)
            order_no = self._order_number()
            if order_no:
                return True, order_no
            msg = self._visible_message()
            if msg and any(key in msg for key in ["成功", "保存"]):
                order_no = self._order_number()
                return True, order_no or "已保存"
            if msg and any(key in msg for key in ["失败", "错误", "必填", "请选择", "不能为空"]):
                diag = self._format_form_diagnostics()
                return False, f"{msg}；{diag}" if diag else msg
        msg = self._visible_message() or "保存后未获取到移库单号"
        diag = self._format_form_diagnostics()
        return False, f"{msg}；{diag}" if diag else msg

    def _add_transfer_detail(self, group, emit=None):
        if not self._click_section_action("移库明细", "新增"):
            return False, "未找到移库明细新增按钮"
        ok, msg = self._select_product_with_code_check(
            group.get("product_name", ""),
            group.get("product_code", ""),
            emit
        )
        if not ok:
            return False, f"移库明细产品选择失败：{msg}"
        self._set_input_by_label("移库数量", group["quantity"])
        if not self._click_dialog_button("确定"):
            return False, "移库明细未找到确定按钮"
        msg = self._visible_message()
        if msg and any(key in msg for key in ["失败", "错误", "必填", "请选择", "不能为空"]):
            return False, msg
        return True, ""

    def _add_barcode_detail(self, detail, emit=None):
        if not self._click_section_action("条码明细", "新增"):
            return False, "未找到条码明细新增按钮"
        product_name = detail.get("product_name", "")
        product_code = detail.get("product_code", "")
        if product_name:
            ok, msg = self._select_product_with_code_check(product_name, product_code, emit)
            if not ok:
                return False, f"条码明细产品选择失败：{msg}"
        filled = False
        for label in ["产品条码", "条码"]:
            if self._set_input_by_label(label, detail["barcode"]):
                filled = True
                break
        if not filled:
            return False, "条码明细未找到产品条码输入框"
        if not self._click_dialog_button("确定"):
            return False, "条码明细未找到确定按钮"
        msg = self._visible_message()
        if msg and any(key in msg for key in ["失败", "错误", "必填", "请选择", "不能为空", "已安装"]):
            return False, msg
        return True, ""

    def _confirm_transfer(self):
        if not self._click_top_button("确认"):
            return False, "未找到确认按钮"
        for _ in range(3):
            time.sleep(0.8)
            if not self._visible_message() and not self.page.locator(".el-dialog:visible").count():
                break
            self._click_dialog_button("确定")

        for _ in range(15):
            time.sleep(0.8)
            msg = self._visible_message()
            if msg and any(key in msg for key in ["失败", "错误", "必填", "请选择", "不能为空", "已安装"]):
                return False, msg
            if msg and any(key in msg for key in ["成功", "已确认", "移库成功"]):
                return True, msg
        return False, self._visible_message() or "确认后未检测到成功提示"

    def create_transfer(self, summary, distributor, transfer_type="移出", remark="", log=None):
        def emit(message, level='info'):
            if log:
                log(message, level)

        with self.lock:
            if not self.is_alive():
                return False, "浏览器未启动，请先登录 CRM"
            try:
                def fail(message):
                    self._return_to_move_list()
                    return False, message

                if not self._open_transfer_create_form(emit):
                    return fail("打开移库单页面后未识别到新增表单")

                if transfer_type not in ("移入", "移出"):
                    transfer_type = "移出"
                emit(f"选择移库类型：{transfer_type}")
                if not self._select_transfer_type(transfer_type):
                    return fail("未找到移库类型输入框，已返回移库单列表")
                emit(f"选择目标分销商：{distributor}")
                if not self._select_input_by_label("分销商", distributor):
                    return fail("未找到分销商输入框，已返回移库单列表")
                if remark:
                    emit("填写备注")
                    self._set_input_by_label("备注", remark)

                emit("保存移库单表头，等待单号...")
                ok, result = self._save_transfer_header()
                if not ok:
                    return fail(result)
                order_no = result
                emit(f"移库单已保存：{order_no}", "success")

                added_products = []
                groups = summary.get("groups", [])
                for idx, group in enumerate(groups, start=1):
                    emit(f"添加移库明细 {idx}/{len(groups)}：{group['product_name']} × {group['quantity']}")
                    ok, msg = self._add_transfer_detail(group, emit)
                    if not ok:
                        return fail(f"添加移库明细失败：{msg}")
                    added_products.append({
                        "product_name": group["product_name"],
                        "product_code": group["product_code"],
                        "quantity": group["quantity"],
                    })

                added_barcodes = []
                details = summary.get("details", [])
                for idx, detail in enumerate(details, start=1):
                    emit(f"添加条码明细 {idx}/{len(details)}：{detail['barcode']}")
                    ok, msg = self._add_barcode_detail(detail, emit)
                    if not ok:
                        return fail(f"添加条码明细失败：{msg}")
                    added_barcodes.append(detail["barcode"])

                if distributor == FROZEN_WAREHOUSE_NAME:
                    emit("目标为江西天麓冻结仓库，移库单只保存不确认，等待审批", "success")
                    self._return_to_move_list()
                    return True, {
                        "order_no": order_no,
                        "products": added_products,
                        "barcodes": added_barcodes,
                        "confirmed": False,
                        "pending_approval": True,
                        "message": "移库单已保存，江西天麓冻结仓库需审批，未点击确认",
                    }

                emit("点击确认移库，等待 CRM 提示...")
                ok, msg = self._confirm_transfer()
                if not ok:
                    return fail(f"确认移库失败：{msg}")
                emit(msg or "CRM 已提示移库成功", "success")
                self._return_to_move_list()

                return True, {
                    "order_no": order_no,
                    "products": added_products,
                    "barcodes": added_barcodes,
                    "confirmed": True,
                    "pending_approval": False,
                    "message": msg or self._visible_message(),
                }
            except Exception as e:
                return False, str(e)

class CRMWorker:
    """把所有 Playwright 操作固定到同一个线程里执行。"""
    def __init__(self):
        self.tasks = queue.Queue()
        self.state_lock = threading.Lock()
        self.browser_running = False
        self.logged_in_cache = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _update_state(self, session):
        try:
            browser_running = session.is_alive()
            logged_in = session.logged_in
        except Exception:
            browser_running = False
            logged_in = False
        with self.state_lock:
            self.browser_running = browser_running
            self.logged_in_cache = logged_in

    def _run(self):
        session = CRMSession()
        while True:
            method_name, args, kwargs, result_queue = self.tasks.get()
            try:
                result = getattr(session, method_name)(*args, **kwargs)
                self._update_state(session)
                result_queue.put((True, result))
            except Exception as e:
                self._update_state(session)
                result_queue.put((False, str(e)))

    def _call(self, method_name, *args, **kwargs):
        result_queue = queue.Queue(maxsize=1)
        self.tasks.put((method_name, args, kwargs, result_queue))
        success, result = result_queue.get()
        if success:
            return result
        raise RuntimeError(result)

    def is_alive(self):
        with self.state_lock:
            return self.browser_running

    @property
    def logged_in(self):
        with self.state_lock:
            return self.logged_in_cache

    def login(self, username, password, captcha=None):
        return self._call("login", username, password, captcha)

    def login_step1(self, username, password):
        return self._call("login_step1", username, password)

    def login_step2(self, captcha):
        return self._call("login_step2", captcha)

    def logout(self):
        result = self._call("logout")
        with self.state_lock:
            self.browser_running = False
            self.logged_in_cache = False
        return result

    def cancel_login(self):
        result = self._call("cancel_login")
        with self.state_lock:
            if not self.logged_in_cache:
                self.browser_running = False
        return result

    def check_login_status(self):
        return self._call("check_login_status")

    def query_barcode(self, barcode, log=None):
        if log:
            log(f"CRM 查询任务已加入队列：{barcode}", "dim")
        return self._call("query_barcode", barcode, log)

    def create_transfer(self, summary, distributor, transfer_type="移出", remark="", log=None):
        return self._call("create_transfer", summary, distributor, transfer_type, remark, log)

crm_session = CRMWorker()

DEFAULT_BATCH_RETRY_LIMIT = 5
MAX_BATCH_RETRY_LIMIT = 5

BATCH_LOG_LIMIT = 5000

batch_job_lock = threading.Lock()
batch_job = {
    'running': False,
    'stop_requested': False,
    'barcodes': [],
    'total': 0,
    'current': 0,
    'success': 0,
    'failed': 0,
    'retry_limit': DEFAULT_BATCH_RETRY_LIMIT,
    'log_seq': 0,
    'logs': [],
    'results': [],
}

library_query_lock = threading.Lock()
library_query_job = {
    'running': False,
    'done': False,
    'success': False,
    'barcode': '',
    'error': '',
    'logs': [],
    'started_at': '',
    'finished_at': '',
}

transfer_job_lock = threading.Lock()
transfer_job = {
    'running': False,
    'done': False,
    'success': False,
    'error': '',
    'result': None,
    'summary': None,
    'distributor': '',
    'transfer_type': '',
    'remark': '',
    'logs': [],
    'started_at': '',
    'finished_at': '',
}

summary_job_lock = threading.Lock()
summary_job = {
    'running': False,
    'done': False,
    'success': False,
    'error': '',
    'summary': None,
    'logs': [],
    'started_at': '',
    'finished_at': '',
}

def _normalize_retry_limit(value):
    try:
        retry_limit = int(value)
    except (TypeError, ValueError):
        retry_limit = DEFAULT_BATCH_RETRY_LIMIT
    return max(0, min(retry_limit, MAX_BATCH_RETRY_LIMIT))

def _brief_batch_error(error, limit=240):
    text = re.sub(r'\s+', ' ', str(error or '')).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."

def _append_batch_log_unlocked(message, level='dim'):
    batch_job['log_seq'] = int(batch_job.get('log_seq') or 0) + 1
    batch_job['logs'].append({
        'id': batch_job['log_seq'],
        'time': datetime.now().strftime('%H:%M:%S'),
        'message': message,
        'level': level,
    })
    batch_job['logs'] = batch_job['logs'][-BATCH_LOG_LIMIT:]

def _batch_log(message, level='dim'):
    with batch_job_lock:
        _append_batch_log_unlocked(message, level)

def _library_query_log(message, level='dim'):
    with library_query_lock:
        library_query_job['logs'].append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'message': message,
            'level': level,
        })
        library_query_job['logs'] = library_query_job['logs'][-300:]

def _run_library_query_job(barcode):
    if is_disassembly_barcode(barcode):
        _library_query_log(f"已跳过拆机条码：{barcode}，CRM 不查询", 'warn')
        with library_query_lock:
            library_query_job['running'] = False
            library_query_job['done'] = True
            library_query_job['success'] = True
            library_query_job['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            library_query_job['error'] = ''
        return
    _library_query_log(f"开始查询条码：{barcode}", 'info')
    try:
        success, result = crm_session.query_barcode(barcode, _library_query_log)
        with library_query_lock:
            library_query_job['running'] = False
            library_query_job['done'] = True
            library_query_job['success'] = bool(success)
            library_query_job['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if success:
                library_query_job['error'] = ''
            else:
                library_query_job['error'] = _brief_batch_error(result, 800)
        if success:
            _library_query_log(f"条码查询完成：{barcode}", 'success')
        else:
            _library_query_log(f"条码查询失败：{result}", 'error')
    except Exception as e:
        with library_query_lock:
            library_query_job['running'] = False
            library_query_job['done'] = True
            library_query_job['success'] = False
            library_query_job['error'] = _brief_batch_error(e, 800)
            library_query_job['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _library_query_log(f"条码查询出错：{e}", 'error')

def _transfer_log(message, level='dim'):
    with transfer_job_lock:
        transfer_job['logs'].append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'message': message,
            'level': level,
        })
        transfer_job['logs'] = transfer_job['logs'][-300:]

def _summary_log(message, level='dim'):
    with summary_job_lock:
        summary_job['logs'].append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'message': message,
            'level': level,
        })
        summary_job['logs'] = summary_job['logs'][-300:]

def _finish_summary_job(success, error='', summary=None):
    with summary_job_lock:
        summary_job['running'] = False
        summary_job['done'] = True
        summary_job['success'] = bool(success)
        summary_job['error'] = error
        summary_job['summary'] = summary
        summary_job['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def _summary_error_from_result(summary):
    if summary.get('missing'):
        return '部分条码没有查询结果，也没有匹配到条码前缀，请先维护条码匹配或查询一次该产品条码'
    if summary.get('incomplete'):
        return '部分条码缺少产品名称或产品编码，无法自动汇总'
    return ''

def _exclude_unmatched_transfer_barcodes(summary):
    missing = [_clean_export_value(x) for x in summary.get('missing', []) if _clean_export_value(x)]
    if not missing:
        return []
    missing_set = set(missing)
    summary['details'] = [
        row for row in summary.get('details', [])
        if _clean_export_value(row.get('barcode')) not in missing_set
    ]
    summary['groups'] = [
        row for row in summary.get('groups', [])
        if row.get('barcodes')
    ]
    summary['missing'] = []
    summary['total'] = len(summary.get('details', []))
    excluded = summary.setdefault('excluded', [])
    for barcode in missing:
        if barcode not in excluded:
            excluded.append(barcode)
    summary['excluded_unmatched'] = missing
    return missing

def _run_summary_job(barcodes, transfer_type, distributor, excluded=None):
    try:
        barcodes, filtered = filter_disassembly_barcodes(barcodes)
        excluded = list(excluded or []) + filtered
        if excluded:
            _summary_log(f"已排除拆机条码 {len(excluded)} 个，不查询不移库：{', '.join(excluded[:10])}", 'warn')
        if not barcodes:
            error = '输入的条码都是拆机条码，无需汇总或移库'
            _summary_log(error, 'warn')
            _finish_summary_job(False, error, {'total': 0, 'details': [], 'groups': [], 'missing': [], 'incomplete': [], 'blocked': [], 'excluded': excluded})
            return
        _summary_log(f"开始汇总预览，共 {len(barcodes)} 个条码", 'info')
        _summary_log("开始检查条码匹配前缀和已有查询结果", 'info')
        auto_library = {'queried': [], 'failed': []}
        representatives = _missing_product_library_representatives(barcodes)
        if representatives:
            items = "，".join([f"{prefix}←{barcode}" for prefix, barcode in representatives.items()])
            _summary_log(f"发现 {len(representatives)} 个缺失前缀：{items}", 'info')
            _summary_log("将自动逐个查询代表条码补充条码匹配；离开本页面不会停止后台汇总，可返回移库页查看日志", 'dim')
            with transfer_job_lock:
                if transfer_job['running']:
                    error = '已有移库任务正在执行，请等待完成后再汇总'
                    _summary_log(error, 'error')
                    _finish_summary_job(False, error)
                    return
            with batch_job_lock:
                if batch_job['running']:
                    error = '批量条码查询正在执行，请等待查询完成后再汇总'
                    _summary_log(error, 'error')
                    _finish_summary_job(False, error)
                    return
            ready, ready_message = _crm_ready_for_auto_query()
            if not ready:
                _summary_log(ready_message, 'error')
                _finish_summary_job(False, ready_message)
                return
            auto_library = ensure_product_library_for_barcodes(barcodes, _summary_log)
            if auto_library.get('failed'):
                failed_items = "，".join([
                    f"{row.get('prefix')}←{row.get('barcode')}"
                    for row in auto_library.get('failed', [])
                ])
                _summary_log(f"仍有前缀自动补充失败：{failed_items}", 'warn')
        else:
            _summary_log("条码匹配已覆盖所有条码前缀，不需要自动查询", 'success')

        _summary_log("开始按产品名称和编码汇总移库明细", 'info')
        summary = build_transfer_summary(barcodes, transfer_type, distributor)
        summary['excluded'] = excluded
        summary['auto_library'] = auto_library
        excluded_unmatched = _exclude_unmatched_transfer_barcodes(summary)
        if excluded_unmatched:
            _summary_log(
                f"已临时排除 {len(excluded_unmatched)} 个查不到产品信息的条码：{', '.join(excluded_unmatched[:10])}",
                'warn'
            )
        if not summary.get('groups'):
            error = '本次没有可移库条码，未匹配到产品信息的条码已临时排除'
            _summary_log(error, 'error')
            _finish_summary_job(False, error, summary)
            return
        error = _summary_error_from_result(summary)
        if error:
            _summary_log(error, 'error')
            _finish_summary_job(False, error, summary)
            return
        _summary_log(f"汇总完成：产品 {len(summary.get('groups', []))} 条，条码 {summary.get('total', 0)} 个", 'success')
        _finish_summary_job(True, '', summary)
    except Exception as e:
        error = _brief_batch_error(e, 800)
        _summary_log(f"汇总预览出错：{error}", 'error')
        _finish_summary_job(False, error)

def _run_transfer_job(summary, distributor, transfer_type, remark):
    _transfer_log(f"开始提交移库：{transfer_type}，分销商 {distributor}", 'info')
    try:
        success, result = crm_session.create_transfer(summary, distributor, transfer_type, remark, _transfer_log)
        with transfer_job_lock:
            transfer_job['running'] = False
            transfer_job['done'] = True
            transfer_job['success'] = bool(success)
            transfer_job['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if success:
                transfer_job['result'] = result
                transfer_job['error'] = ''
            else:
                transfer_job['error'] = _brief_batch_error(result, 800)
        if success:
            save_distributor_history(distributor)
            order_no = result.get('order_no') if isinstance(result, dict) else ''
            if isinstance(result, dict) and result.get('pending_approval'):
                _transfer_log(f"移库单已保存待审批：{order_no or '已保存'}", 'success')
            else:
                _apply_transfer_local_dealer(summary, transfer_type, distributor)
                _transfer_log(f"移库完成：{order_no or '已完成'}", 'success')
        else:
            _transfer_log(f"移库失败：{result}", 'error')
    except Exception as e:
        with transfer_job_lock:
            transfer_job['running'] = False
            transfer_job['done'] = True
            transfer_job['success'] = False
            transfer_job['error'] = _brief_batch_error(e, 800)
            transfer_job['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _transfer_log(f"移库出错：{e}", 'error')

def _batch_stop_requested(idx):
    with batch_job_lock:
        if not batch_job['stop_requested']:
            return False
        batch_job['running'] = False
        batch_job['stop_requested'] = False
    _batch_log(f"已停止，停在第 {idx} 个条码", 'warn')
    return True

def _run_batch_job(barcodes, retry_limit=DEFAULT_BATCH_RETRY_LIMIT, excluded=None):
    retry_limit = _normalize_retry_limit(retry_limit)
    barcodes, filtered = filter_disassembly_barcodes(barcodes)
    excluded = list(excluded or []) + filtered
    retry_text = f"，失败最多重试 {retry_limit} 次" if retry_limit else ""
    if excluded:
        _batch_log(f"已排除拆机条码 {len(excluded)} 个，不查询：{', '.join(excluded[:10])}", 'warn')
    if not barcodes:
        with batch_job_lock:
            batch_job['running'] = False
            batch_job['stop_requested'] = False
        _batch_log("输入的条码都是拆机条码，无需查询", 'warn')
        return
    _batch_log(f"开始批量查询 {len(barcodes)} 个条码{retry_text}...", 'info')
    for idx, barcode in enumerate(barcodes, start=1):
        with batch_job_lock:
            if batch_job['stop_requested']:
                batch_job['running'] = False
                batch_job['stop_requested'] = False
                stopped_at = idx
                should_stop = True
            else:
                batch_job['current'] = idx
                should_stop = False
        if should_stop:
            _batch_log(f"已停止，停在第 {stopped_at} 个条码", 'warn')
            return

        success = False
        result = ""
        attempts = retry_limit + 1
        for attempt in range(1, attempts + 1):
            if _batch_stop_requested(idx):
                return
            if attempt == 1:
                _batch_log(f"正在查询第 {idx}/{len(barcodes)} 个：{barcode}", 'info')
            else:
                _batch_log(f"{barcode} 第 {attempt - 1}/{retry_limit} 次重试中...", 'warn')

            success, result = crm_session.query_barcode(barcode, _batch_log)
            if success:
                break

            if attempt <= retry_limit:
                _batch_log(
                    f"{barcode} 查询失败，将重试 {attempt}/{retry_limit}: {_brief_batch_error(result)}",
                    'warn'
                )
                time.sleep(2)

        with batch_job_lock:
            if success:
                batch_job['success'] += 1
                batch_job['results'].append({
                    'barcode': barcode,
                    'success': True,
                    'attempts': attempt,
                    'view_url': f'/barcode/{barcode}.html',
                })
                _append_batch_log_unlocked(
                    f"✓ {barcode} 查询成功" + (f"（重试第 {attempt - 1} 次）" if attempt > 1 else ""),
                    'success'
                )
            else:
                batch_job['failed'] += 1
                batch_job['results'].append({
                    'barcode': barcode,
                    'success': False,
                    'attempts': attempts,
                    'error': _brief_batch_error(result),
                })
                _append_batch_log_unlocked(
                    f"✗ {barcode} 查询失败（已重试 {retry_limit} 次）: {_brief_batch_error(result)}",
                    'error'
                )

    with batch_job_lock:
        batch_job['running'] = False
        batch_job['stop_requested'] = False
    _batch_log(
        f"批量查询完成，成功 {batch_job['success']} 个，失败 {batch_job['failed']} 个",
        'success'
    )

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

app = Flask(__name__, template_folder=os.path.join(RESOURCE_BASE_DIR, "templates"))
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "crm-barcode-query-local-secret")

BARCODE_DIR = os.path.join(RUNTIME_BASE_DIR, "barcode")
ARCHIVE_DIR = os.path.join(BARCODE_DIR, "archived")
DATA_FILE = os.path.join(BARCODE_DIR, "barcode_data.json")
PRODUCT_LIBRARY_FILE = os.path.join(BARCODE_DIR, "product_library.json")
ACCOUNTS_FILE = os.path.join(BARCODE_DIR, "accounts.json")
DISTRIBUTOR_HISTORY_FILE = os.path.join(BARCODE_DIR, "distributor_history.json")
OWN_DEALER_NAME = "江西省天麓工贸有限公司"
FROZEN_WAREHOUSE_NAME = "江西天麓冻结仓库"

FIELD_IDS = {
    'newisclosed1': '结单状态',
    'SHIPSTATUS1': '装箱单状态',
    'instlled1': '是否安装',
    'newstatus1': '产品状态',
    'typestr1': '服务类型',
    'statustr1': '服务单状态',
    'newname1': '条码号',
    'zxd1': '装箱单号',
    'shipdate1': '发货日期',
    'newerpshipno1': '发货单号',
    'ProductNumber1': '物料编码',
    'newproductidName1': '机型',
    'newordsalesorderidName1': '订单号',
    'servno1': '服务单号',
    'name1': '客户',
    'newaddress1': '地址',
    'newtelephone1': '电话',
    'newstationidName1': '服务站',
    'newdealername1': '服务经销商',
    'dealername1': '所属经销商',
    'buno1': '调拨单号',
    'transstockdate1': '移库日期',
    'newproductname1': '物料名称',
    'newaccountidName1': '装箱单经销商',
    'newtype1': '装箱类型',
    'newdeposit1': '押金',
    'returned1': '返修',
    'newpresaledealername1': '售前经销商',
    'underlinestr1': '线下带货上门',
    'productname1': '物料描述',
    'productdealernumber1': '所属经销商代码',
    'depositdealer1': '押金经销商',
    'depositdealernumber1': '押金经销商代码',
    'isonline1': '线上产品',
    'transdate1': '调拨日期',
    'serno1': '条码',
    'productcode1': '物料编码',
    'dealercustcode1': '经销商代码',
    'dealercustname1': '经销商名称',
    'distributorcustcode1': '分销商代码',
    'distributorcustname1': '分销商名称',
    'type1': '移库类型',
    'state1': '状态',
    'statuscode1': '是否可用',
    'installDate1': '安装日期',
    'myproductdealer1': '所属经销商',
    'customer1': '客户',
    'newphone1': '电话',
    'newaddress1': '地址',
    'installdate1': '安装日期',
    'warrantyid1': '保卡条码',
    'scandate1': '扫描时间',
    'returndepositid1': '押金单号',
    'returndepositamount1': '押金金额',
    'servdate1': '服务单日期',
    'returndealer1': '押金经销商',
    'returndate1': '返还日期',
    'returnstatus1': '状态',
    'adjustid1': '库存调整单号',
    'adjustdate1': '调整日期',
    'adjustdirection1': '交易方向',
    'adjuststatus1': '状态',
    'adjustdealer1': '经销商',
    'moveid1': '移机单号',
    'movedate1': '移机日期',
    'oldcontact1': '原联系人',
    'oldtelephone1': '原联系电话',
    'oldaddress1': '原联系地址',
    'newdealer1': '关联经销商',
    'olddealer1': '原经销商',
}

FILTER_FIELDS = {
    'myproductdealer1_sr5': '所属经销商',
    'newdealername1_sr2': '服务经销商',
    'newisclosed1_sr2': '结单状态',
}

SUBREPORT_NAMES = {
    1: '装箱单',
    2: '服务单',
    3: '保卡扫描',
    4: '押金返还',
    5: '产品档案',
    6: '库存调整',
    7: '调拨单',
    8: '移库单',
    9: '移机单',
    10: '库存状态',
}

SUBREPORT_FIELD_MAP = {
    1: ['SHIPSTATUS1', 'zxd1', 'shipdate1', 'newerpshipno1', 'ProductNumber1',
        'newproductidName1', 'newordsalesorderidName1', 'newname1', 'newproductname1',
        'newtype1', 'newdeposit1', 'newaccountidName1', 'newpayaccountidName1'],
    2: ['servno1', 'underlinestr1', 'statustr1', 'newproductname1', 'newisclosed1',
        'typestr1', 'newproductidName1', 'newaddress1', 'newtelephone1', 'name1',
        'newstationidName1', 'newdealername1', 'newpresaledealername1'],
    3: ['warrantyid1', 'scandate1'],
    4: ['returndepositid1', 'returndepositamount1', 'servno1', 'servdate1',
        'newdealername1', 'returndealer1', 'returndate1', 'returnstatus1'],
    5: ['newname1', 'newproductname1', 'productname1', 'instlled1', 'returned1',
        'newdeposit1', 'isonline1', 'myproductdealer1', 'productdealernumber1',
        'depositdealernumber1', 'depositdealer1', 'customer1', 'newphone1',
        'newaddress1', 'installdate1'],
    6: ['adjustid1', 'adjustdate1', 'serno1', 'productname1',
        'adjustdirection1', 'adjuststatus1', 'adjustdealer1'],
    7: ['buno1', 'transdate1', 'serno1', 'productcode1', 'productname1',
        'accountincustcustname1', 'accountoutcustcode1', 'accountoutcustname1', 'transstatus1'],
    8: ['buno1', 'transstockdate1', 'serno1', 'productcode1', 'productname1',
        'dealercustcode1', 'dealercustname1', 'distributorcustcode1',
        'distributorcustname1', 'type1', 'state1'],
    9: ['moveid1', 'movedate1', 'serno1', 'productcode1', 'productname1',
        'oldcontact1', 'oldtelephone1', 'oldaddress1', 'newcontact1',
        'newtelephone1', 'newaddress1', 'newdealer1', 'olddealer1'],
    10: ['newname1', 'newproductname1', 'newproductidName1', 'newstatus1',
         'statuscode1', 'dealername1', 'newdeposit1'],
}

def extract_fields_from_html(filepath):
    result = {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            html = f.read()

        sr_pattern = re.compile(r'<div id="Subreport(\d+)"')
        sr_matches = list(sr_pattern.finditer(html))

        field_pattern_cache = {}
        for field_id in FIELD_IDS:
            field_pattern_cache[field_id] = rf'id="{field_id}"[^>]*>.*?<span[^>]*>([^<]+)</span>'

        for i, m in enumerate(sr_matches):
            sr_num = int(m.group(1))
            if sr_num not in SUBREPORT_FIELD_MAP:
                continue

            sr_start = m.start()
            sr_end = sr_matches[i + 1].start() if i + 1 < len(sr_matches) else len(html)
            sr_body = html[sr_start:sr_end]

            sr_key = f'sr{sr_num}'
            raw_field_values = {}

            for fid in SUBREPORT_FIELD_MAP[sr_num]:
                if fid not in field_pattern_cache:
                    continue
                pattern = field_pattern_cache[fid]
                matches = re.findall(pattern, sr_body, re.DOTALL)
                vals = []
                for match in matches:
                    value = html_mod.unescape(match).strip()
                    if value and value not in ['¥.00', '.00', '', ' ', '***']:
                        vals.append(value)
                if vals:
                    raw_field_values[fid] = vals

            if not raw_field_values:
                continue

            record_count = max(len(v) for v in raw_field_values.values())

            if record_count == 1:
                result[sr_key] = {fid: raw_field_values[fid][0] for fid in raw_field_values}
            else:
                result[sr_key] = [
                    {fid: raw_field_values.get(fid, [''])[j] if j < len(raw_field_values.get(fid, [''])) else '' for fid in raw_field_values}
                    for j in range(record_count)
                ]

    except Exception:
        pass

    return result

def _get_field(fields, field_id):
    import re as re_mod
    m = re_mod.search(r'_sr(\d+)$', field_id)
    if not m:
        return fields.get(field_id, '')
    sr_num = m.group(1)
    sr_key = f'sr{sr_num}'
    real_fid = field_id.rsplit('_sr', 1)[0]
    sub = fields.get(sr_key, {})
    if isinstance(sub, list) and sub:
        sub = sub[0]
    if isinstance(sub, dict):
        return sub.get(real_fid, '')
    return ''

def _clean_export_value(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return html_mod.unescape(str(value)).replace('\xa0', ' ').strip()

def is_disassembly_barcode(barcode):
    barcode = _clean_export_value(barcode)
    return bool(barcode) and barcode[0].upper() in ("A", "C")

def filter_disassembly_barcodes(barcodes):
    kept = []
    excluded = []
    for barcode in barcodes or []:
        barcode = _clean_export_value(barcode)
        if not barcode:
            continue
        if is_disassembly_barcode(barcode):
            excluded.append(barcode)
        else:
            kept.append(barcode)
    return kept, excluded

def _records_for_export(fields, sr_key):
    raw = fields.get(sr_key)
    if isinstance(raw, list):
        records = raw
    elif isinstance(raw, dict):
        records = [raw]
    else:
        return []

    result = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        cleaned = OrderedDict()
        for key, value in record.items():
            cell_value = _clean_export_value(value)
            if cell_value:
                cleaned[key] = cell_value
        if not cleaned:
            continue
        signature = json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        result.append(cleaned)
    return result

def _prepare_export_rows(selected_barcodes):
    export_rows = []
    for item in selected_barcodes:
        fields = item.get('fields') or {}
        records_by_sr = {}
        for sr_num in sorted(SUBREPORT_FIELD_MAP):
            sr_key = f'sr{sr_num}'
            records = _records_for_export(fields, sr_key)
            if records:
                records_by_sr[sr_key] = records

        barcode = _clean_export_value(item.get('barcode')) or _get_field(fields, 'newname1_sr1')
        export_rows.append({
            'barcode': barcode,
            'remark': _clean_export_value(item.get('remark')),
            'time': _clean_export_value(item.get('time') or item.get('archiveTime')),
            'records_by_sr': records_by_sr,
        })
    return export_rows

def _build_export_columns(export_rows):
    columns = [
        {'group': '基础信息', 'label': '条码', 'type': 'base', 'key': 'barcode'},
        {'group': '基础信息', 'label': '备注', 'type': 'base', 'key': 'remark'},
        {'group': '基础信息', 'label': '查询时间', 'type': 'base', 'key': 'time'},
    ]

    for sr_num in sorted(SUBREPORT_FIELD_MAP):
        sr_key = f'sr{sr_num}'
        max_count = max((len(row['records_by_sr'].get(sr_key, [])) for row in export_rows), default=0)
        if max_count == 0:
            continue

        present_fields = OrderedDict()
        for row in export_rows:
            for record in row['records_by_sr'].get(sr_key, []):
                for field_id in record:
                    present_fields[field_id] = True

        preferred = [field_id for field_id in SUBREPORT_FIELD_MAP.get(sr_num, []) if field_id in present_fields]
        extras = [field_id for field_id in present_fields if field_id not in preferred]
        field_ids = preferred + extras
        group_name = SUBREPORT_NAMES.get(sr_num, f'子报表{sr_num}')

        for record_index in range(max_count):
            group_label = f'{group_name} {record_index + 1}' if max_count > 1 else group_name
            for field_id in field_ids:
                columns.append({
                    'group': group_label,
                    'label': FIELD_IDS.get(field_id, field_id),
                    'type': 'field',
                    'sr_key': sr_key,
                    'record_index': record_index,
                    'field_id': field_id,
                })
    return columns

def _export_cell_value(row, column):
    if column['type'] == 'base':
        return row.get(column['key'], '')
    records = row['records_by_sr'].get(column['sr_key'], [])
    if column['record_index'] >= len(records):
        return ''
    return records[column['record_index']].get(column['field_id'], '')

def _display_width(value):
    text = str(value or '')
    return sum(2 if ord(ch) > 127 else 1 for ch in text)

def _suggest_column_width(label, values):
    max_width = _display_width(label)
    for value in values:
        max_width = max(max_width, _display_width(value))
    if '地址' in label:
        return min(max(max_width + 2, 24), 42)
    if any(word in label for word in ['名称', '经销商', '客户', '机型', '物料描述', '备注']):
        return min(max(max_width + 2, 18), 34)
    if any(word in label for word in ['日期', '时间']):
        return min(max(max_width + 2, 14), 20)
    return min(max(max_width + 2, 12), 28)

def get_filter_options(barcodes):
    options = {}
    for field_id, label in FILTER_FIELDS.items():
        values = set()
        for b in barcodes:
            val = _get_field(b['fields'], field_id)
            if val:
                values.add(val)
        options[field_id] = {
            'label': label,
            'field_id': field_id,
            'options': sorted(values)
        }
    return options

def _first_record(fields, sr_key):
    sub = fields.get(sr_key, {})
    if isinstance(sub, list):
        return sub[0] if sub else {}
    if isinstance(sub, dict):
        return sub
    return {}

def product_prefix_from_barcode(barcode):
    barcode = _clean_export_value(barcode)
    if len(barcode) > 10:
        return barcode[:-10]
    return barcode[:2]

def load_product_library():
    if os.path.exists(PRODUCT_LIBRARY_FILE):
        try:
            with open(PRODUCT_LIBRARY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}

def save_product_library(data):
    os.makedirs(BARCODE_DIR, exist_ok=True)
    with open(PRODUCT_LIBRARY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def upsert_product_library(prefix, product_code, product_name, source_barcode=""):
    prefix = _clean_export_value(prefix)
    product_code = _clean_export_value(product_code)
    product_name = _clean_export_value(product_name)
    if not prefix or not product_code or not product_name:
        return False
    data = load_product_library()
    data[prefix] = {
        'prefix': prefix,
        'product_code': product_code,
        'product_name': product_name,
        'source_barcode': _clean_export_value(source_barcode),
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    save_product_library(data)
    return True

def update_product_library_from_info(info):
    barcode = _clean_export_value(info.get('barcode'))
    prefix = product_prefix_from_barcode(barcode)
    return upsert_product_library(prefix, info.get('product_code'), info.get('product_name'), barcode)

def match_product_library(barcode):
    barcode = _clean_export_value(barcode)
    data = load_product_library()
    for prefix in sorted(data.keys(), key=len, reverse=True):
        if barcode.startswith(prefix):
            row = data[prefix] or {}
            return {
                'prefix': prefix,
                'product_code': _clean_export_value(row.get('product_code')),
                'product_name': _clean_export_value(row.get('product_name')),
            }
    return None

def lookup_product_by_barcode(barcode):
    barcode = _clean_export_value(barcode)
    if not barcode:
        return None
    for item in scan_barcodes():
        if item.get('barcode') == barcode:
            info = _barcode_product_info(item)
            if info.get('product_code') and info.get('product_name'):
                update_product_library_from_info(info)
                info['matched_prefix'] = product_prefix_from_barcode(barcode)
                return info
            break
    return _barcode_product_info_from_library(barcode)

def _barcode_product_info(item):
    fields = item.get('fields') or {}
    sr10 = _first_record(fields, 'sr10')
    sr5 = _first_record(fields, 'sr5')
    sr2 = _first_record(fields, 'sr2')
    sr1 = _first_record(fields, 'sr1')

    product_code = (
        _clean_export_value(sr10.get('newproductname1')) or
        _clean_export_value(sr5.get('newproductname1')) or
        _clean_export_value(sr1.get('ProductNumber1'))
    )
    product_name = (
        _clean_export_value(sr10.get('newproductidName1')) or
        _clean_export_value(sr5.get('productname1')) or
        _clean_export_value(sr1.get('newproductidName1')) or
        _clean_export_value(sr1.get('newproductname1'))
    )
    current_dealer = (
        _clean_export_value(item.get('currentDealerOverride')) or
        _clean_export_value(sr10.get('dealername1')) or
        _clean_export_value(sr5.get('myproductdealer1')) or
        _clean_export_value(sr1.get('newaccountidName1'))
    )
    installed = _clean_export_value(sr5.get('instlled1'))
    product_status = _clean_export_value(sr10.get('newstatus1'))
    service_dealer = _clean_export_value(sr2.get('newdealername1'))

    info = {
        'barcode': _clean_export_value(item.get('barcode')),
        'product_code': product_code,
        'product_name': product_name,
        'current_dealer': current_dealer,
        'service_dealer': service_dealer,
        'installed': installed,
        'product_status': product_status,
        'source': 'query_result',
    }
    return info

def refresh_product_library_from_query_fields(barcode, fields, log=None):
    info = _barcode_product_info({
        'barcode': barcode,
        'fields': fields,
    })
    prefix = product_prefix_from_barcode(barcode)
    if update_product_library_from_info(info):
        if log:
            log(
                f"条码匹配已按本次查询刷新：前缀 {prefix}，{info.get('product_code')} / {info.get('product_name')}",
                "success"
            )
        return True
    if log:
        log(f"条码匹配未刷新：条码 {barcode} 缺少产品编码或产品名称", "warn")
    return False

def _barcode_product_info_from_library(barcode):
    matched = match_product_library(barcode)
    if not matched:
        return {
            'barcode': _clean_export_value(barcode),
            'product_code': '',
            'product_name': '',
        'current_dealer': '',
        'service_dealer': '',
        'installed': '',
        'product_status': '',
        'source': 'unmatched',
            'matched_prefix': '',
        }
    return {
        'barcode': _clean_export_value(barcode),
        'product_code': matched['product_code'],
        'product_name': matched['product_name'],
        'current_dealer': '',
        'service_dealer': '',
        'installed': '',
        'product_status': '',
        'source': 'product_library',
        'matched_prefix': matched['prefix'],
    }

def _missing_product_library_representatives(selected_barcodes):
    wanted = OrderedDict()
    for barcode in selected_barcodes:
        barcode = _clean_export_value(barcode)
        if is_disassembly_barcode(barcode):
            continue
        if barcode and barcode not in wanted:
            wanted[barcode] = True

    all_items = {item['barcode']: item for item in scan_barcodes()}
    groups = OrderedDict()
    for barcode in wanted:
        if match_product_library(barcode):
            continue

        item = all_items.get(barcode)
        if item:
            info = _barcode_product_info(item)
            if info.get('product_code') and info.get('product_name'):
                update_product_library_from_info(info)
                continue

        prefix = product_prefix_from_barcode(barcode)
        if prefix and prefix not in groups:
            groups[prefix] = barcode
    return groups

def ensure_product_library_for_barcodes(selected_barcodes, log=None):
    representatives = _missing_product_library_representatives(selected_barcodes)
    result = {'queried': [], 'failed': []}
    if not representatives:
        return result

    def emit(message, level='info'):
        if log:
            log(message, level)

    total = len(representatives)
    emit(f"发现 {total} 个产品前缀未维护，先各查询 1 个代表条码补充条码匹配")
    for index, (prefix, barcode) in enumerate(representatives.items(), 1):
        emit(f"自动补充 {index}/{total}：前缀 {prefix}，代表条码 {barcode}", "info")
        emit(f"准备查询代表条码 {barcode}，用于补充前缀 {prefix}", "dim")
        success, message = crm_session.query_barcode(barcode, log)
        emit(f"代表条码 {barcode} 查询返回：{'成功' if success else '失败'}", "success" if success else "warn")
        if success and match_product_library(barcode):
            result['queried'].append({'prefix': prefix, 'barcode': barcode})
            matched = match_product_library(barcode) or {}
            emit(
                f"前缀 {prefix} 已写入条码匹配：{matched.get('product_code', '')} / {matched.get('product_name', '')}",
                'success'
            )
        else:
            error = _brief_batch_error(message, 300)
            result['failed'].append({'prefix': prefix, 'barcode': barcode, 'error': error})
            emit(f"前缀 {prefix} 条码匹配补充失败：{error}", 'warn')
    emit(
        f"自动补充完成：成功 {len(result['queried'])} 个，失败 {len(result['failed'])} 个",
        "success" if not result['failed'] else "warn"
    )
    return result

def _crm_ready_for_auto_query():
    if not crm_session.is_alive():
        return False, "CRM 浏览器未启动，请先到在线查询页面登录 CRM"
    if not crm_session.logged_in:
        return False, "CRM 当前未登录，请先到在线查询页面完成登录"
    return True, ""

def _replace_html_field_values(html, field_ids, value):
    escaped_value = html_mod.escape(_clean_export_value(value), quote=False)
    changed = False
    for field_id in field_ids:
        pattern = re.compile(
            rf'(<div\s+id="{re.escape(field_id)}"[^>]*>.*?<span[^>]*>)([^<]*)(</span>)',
            re.DOTALL
        )

        def repl(match):
            nonlocal changed
            changed = True
            return match.group(1) + escaped_value + match.group(3)

        html = pattern.sub(repl, html)
    return html, changed

def _apply_dealer_to_fields(fields, dealer):
    dealer = _clean_export_value(dealer)
    if not dealer:
        return fields
    if isinstance(fields.get('sr5'), dict):
        fields['sr5']['myproductdealer1'] = dealer
    if isinstance(fields.get('sr10'), dict):
        fields['sr10']['dealername1'] = dealer
    return fields

def _barcode_html_path(barcode):
    filename = barcode + '.html'
    for directory in (BARCODE_DIR, ARCHIVE_DIR):
        filepath = os.path.join(directory, filename)
        if os.path.exists(filepath):
            return filepath
    return ''

def _sync_barcode_html_dealer(barcode, dealer):
    filepath = _barcode_html_path(barcode)
    if not filepath:
        return False
    try:
        original_mtime = os.path.getmtime(filepath)
        with open(filepath, 'r', encoding='utf-8') as f:
            html = f.read()
        updated_html, changed = _replace_html_field_values(
            html,
            ["myproductdealer1", "dealername1"],
            dealer
        )
        if changed:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(updated_html)
            os.utime(filepath, (original_mtime, original_mtime))
        return changed
    except Exception:
        return False

def _apply_transfer_local_dealer(summary, transfer_type, distributor):
    new_dealer = OWN_DEALER_NAME if transfer_type == "移入" else distributor
    if not new_dealer:
        return
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    data = load_data()
    for detail in summary.get('details', []):
        barcode = _clean_export_value(detail.get('barcode'))
        if not barcode:
            continue
        info = data.get(barcode, {'remark': '', 'archived': False, 'archiveTime': '', 'archivedBy': ''})
        info['currentDealerOverride'] = new_dealer
        info['transferUpdatedAt'] = now
        info['transferType'] = transfer_type
        info['transferDistributor'] = distributor
        data[barcode] = info
        _sync_barcode_html_dealer(barcode, new_dealer)
    save_data(data)

def build_transfer_summary(selected_barcodes, transfer_type="移出", distributor=""):
    wanted = OrderedDict()
    excluded = []
    for barcode in selected_barcodes:
        barcode = _clean_export_value(barcode)
        if is_disassembly_barcode(barcode):
            excluded.append(barcode)
            continue
        if barcode and barcode not in wanted:
            wanted[barcode] = True

    all_items = {item['barcode']: item for item in scan_barcodes()}
    details = []
    missing = []
    incomplete = []
    blocked = []
    grouped = OrderedDict()

    for barcode in wanted:
        item = all_items.get(barcode)
        if not item:
            info = _barcode_product_info_from_library(barcode)
        else:
            info = _barcode_product_info(item)

        if info.get('source') == 'unmatched':
            missing.append(barcode)
            details.append(info)
            continue

        details.append(info)
        if not info['product_code'] or not info['product_name']:
            incomplete.append(barcode)
            continue
        if transfer_type == "移出" and info.get('current_dealer') and info['current_dealer'] != OWN_DEALER_NAME:
            blocked.append({
                'barcode': barcode,
                'reason': f"当前所属为 {info['current_dealer']}，不是 {OWN_DEALER_NAME}，请确认后再移出",
            })
        if transfer_type == "移入" and distributor and info.get('current_dealer') and info['current_dealer'] != distributor:
            blocked.append({
                'barcode': barcode,
                'reason': f"当前所属为 {info['current_dealer']}，不是所选分销商 {distributor}，请确认后再移入",
            })

        key = f"{info['product_code']}|{info['product_name']}"
        if key not in grouped:
            grouped[key] = {
                'product_code': info['product_code'],
                'product_name': info['product_name'],
                'quantity': 0,
                'barcodes': [],
            }
        grouped[key]['quantity'] += 1
        grouped[key]['barcodes'].append(barcode)

    return {
        'total': len(wanted),
        'details': details,
        'groups': list(grouped.values()),
        'missing': missing,
        'incomplete': incomplete,
        'blocked': blocked,
        'excluded': excluded,
    }

def queried_dealer_history():
    dealers = OrderedDict()
    for item in scan_barcodes():
        info = _barcode_product_info(item)
        for key in ("current_dealer", "service_dealer"):
            dealer = _clean_export_value(info.get(key))
            if dealer and dealer != OWN_DEALER_NAME:
                dealers[dealer] = True
    return list(dealers.keys())

def load_distributor_history():
    if os.path.exists(DISTRIBUTOR_HISTORY_FILE):
        try:
            with open(DISTRIBUTOR_HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return [
                _clean_export_value(row)
                for row in (data if isinstance(data, list) else [])
                if _clean_export_value(row) and _clean_export_value(row) != OWN_DEALER_NAME
            ]
        except Exception:
            pass
    return []

def save_distributor_history(distributor):
    distributor = _clean_export_value(distributor)
    if not distributor or distributor == OWN_DEALER_NAME:
        return
    rows = [distributor] + [row for row in load_distributor_history() if row != distributor]
    os.makedirs(BARCODE_DIR, exist_ok=True)
    with open(DISTRIBUTOR_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(rows[:100], f, ensure_ascii=False, indent=2)

def save_distributor_history_many(distributors):
    rows = OrderedDict()
    for distributor in distributors:
        distributor = _clean_export_value(distributor)
        if distributor and distributor != OWN_DEALER_NAME:
            rows[distributor] = True
    for distributor in load_distributor_history():
        if distributor and distributor != OWN_DEALER_NAME:
            rows[distributor] = True
    os.makedirs(BARCODE_DIR, exist_ok=True)
    with open(DISTRIBUTOR_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(rows.keys())[:100], f, ensure_ascii=False, indent=2)

def combined_distributor_history():
    save_distributor_history_many(queried_dealer_history())
    dealers = OrderedDict()
    for dealer in load_distributor_history():
        dealer = _clean_export_value(dealer)
        if dealer and dealer != OWN_DEALER_NAME:
            dealers[dealer] = True
    return list(dealers.keys())

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_archived_set():
    data = load_data()
    return {bc for bc, info in data.items() if info.get('archived')}

def get_barcode_info(barcode):
    data = load_data()
    return data.get(barcode, {'remark': '', 'archived': False, 'archiveTime': '', 'archivedBy': ''})

def update_barcode_info(barcode, info):
    data = load_data()
    data[barcode] = info
    save_data(data)

def load_accounts():
    default_admin = {
        'id': 'admin',
        'username': 'admin',
        'display_name': '管理员',
        'password': '88293529',
        'permissions': ['crm', 'results', 'transfer', 'accounts', 'product-library'],
        'updated_at': '',
    }
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            accounts = data if isinstance(data, list) else []
            if not any(row.get('username') == 'admin' for row in accounts):
                accounts.insert(0, default_admin)
                save_accounts(accounts)
            return accounts
        except Exception:
            pass
    save_accounts([default_admin])
    return [default_admin]

def save_accounts(accounts):
    os.makedirs(BARCODE_DIR, exist_ok=True)
    with open(ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)

def account_public(row):
    return {
        'id': row.get('id', ''),
        'username': row.get('username', ''),
        'display_name': row.get('display_name', ''),
        'permissions': row.get('permissions', []),
        'updated_at': row.get('updated_at', ''),
        'is_admin': row.get('username') == 'admin',
    }

def current_account():
    username = session.get('account_username')
    if not username:
        return None
    return next((row for row in load_accounts() if row.get('username') == username), None)

def current_account_public():
    row = current_account()
    return account_public(row) if row else None

PAGE_LINKS = [
    {'permission': 'crm', 'label': '在线查询', 'href': '/crm'},
    {'permission': 'results', 'label': '结果管理', 'href': '/'},
    {'permission': 'transfer', 'label': '移库', 'href': '/transfer'},
    {'permission': 'accounts', 'label': '账号管理', 'href': '/accounts'},
    {'permission': 'product-library', 'label': '条码匹配', 'href': '/product-library'},
]

def visible_page_links():
    row = current_account()
    if not row:
        return []
    if row.get('username') == 'admin':
        return PAGE_LINKS
    permissions = set(row.get('permissions') or [])
    links = [link for link in PAGE_LINKS if link['permission'] in permissions]
    if not any(link['permission'] == 'accounts' for link in links):
        links.append({'permission': 'account-self', 'label': '账号管理', 'href': '/accounts'})
    return links

def is_admin_account():
    row = current_account()
    return bool(row and row.get('username') == 'admin')

def account_has_permission(permission):
    row = current_account()
    if not row:
        return False
    if row.get('username') == 'admin':
        return True
    return permission in (row.get('permissions') or [])

def required_permission_for_path(path):
    if path == "/" or path.startswith("/barcode/"):
        return "results"
    if path == "/crm":
        return "crm"
    if path == "/transfer" or path.startswith("/api/transfer") or path.startswith("/api/crm/transfer"):
        return "transfer"
    if path.startswith("/api/distributor-history"):
        return "transfer"
    if path == "/product-library" or path.startswith("/api/product-library"):
        return "product-library"
    if path == "/accounts":
        return "account-self"
    if path.startswith("/api/barcodes") or path.startswith("/api/filter-options") or path.startswith("/api/export"):
        return "results"
    if path.startswith("/api/crm"):
        return "crm"
    return None

@app.before_request
def require_app_login():
    path = request.path
    if path.startswith("/api/app-auth"):
        return None
    if path == "/login":
        return None
    if path == "/product-library":
        return None
    if path == "/api/product-library" and request.method == "GET":
        return None
    if path == "/api/product-library/lookup":
        return None
    if path == "/api/product-library/query/start":
        return None
    if path == "/api/product-library/query/status":
        return None
    if path.startswith("/api/accounts"):
        if not current_account():
            return jsonify({'success': False, 'error': '请先登录工具账号'}), 401
        return None

    permission = required_permission_for_path(path)
    if not permission:
        return None

    if not current_account():
        if path.startswith("/api/"):
            return jsonify({'success': False, 'error': '请先登录工具账号'}), 401
        return redirect("/login?next=" + path)
    if permission != "account-self" and not account_has_permission(permission):
        if path.startswith("/api/"):
            return jsonify({'success': False, 'error': '当前账号无权访问该功能'}), 403
        return render_template("no_permission.html", account=current_account_public(), links=visible_page_links()), 403
    return None

def archive_barcode(barcode):
    src = os.path.join(BARCODE_DIR, barcode + '.html')
    if not os.path.exists(src):
        return False, '文件不存在'
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    dst = os.path.join(ARCHIVE_DIR, barcode + '.html')
    try:
        os.rename(src, dst)
        info = get_barcode_info(barcode)
        info['archived'] = True
        info['archiveTime'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        update_barcode_info(barcode, info)
        return True, '归档成功'
    except Exception as e:
        return False, str(e)

def unarchive_barcode(barcode):
    src = os.path.join(ARCHIVE_DIR, barcode + '.html')
    if not os.path.exists(src):
        return False, '归档文件不存在'
    dst = os.path.join(BARCODE_DIR, barcode + '.html')
    try:
        os.rename(src, dst)
        info = get_barcode_info(barcode)
        info['archived'] = False
        info['archiveTime'] = ''
        update_barcode_info(barcode, info)
        return True, '取消归档成功'
    except Exception as e:
        return False, str(e)

def scan_barcodes():
    barcodes = []
    seen = set()

    def add_barcode_file(filepath, filename):
        barcode = filename.replace('.html', '')
        if barcode in seen:
            return
        seen.add(barcode)
        mtime = os.path.getmtime(filepath)
        time_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        fields = extract_fields_from_html(filepath)
        info = get_barcode_info(barcode)
        fields = _apply_dealer_to_fields(fields, info.get('currentDealerOverride', ''))
        barcodes.append({
            'barcode': barcode,
            'filename': filename,
            'time': time_str,
            'mtime': mtime,
            'fields': fields,
            'currentDealerOverride': info.get('currentDealerOverride', ''),
            'transferUpdatedAt': info.get('transferUpdatedAt', ''),
            'remark': info.get('remark', ''),
        })

    for filename in os.listdir(BARCODE_DIR):
        if filename.endswith('.html'):
            add_barcode_file(os.path.join(BARCODE_DIR, filename), filename)

    if os.path.exists(ARCHIVE_DIR):
        for filename in os.listdir(ARCHIVE_DIR):
            if filename.endswith('.html'):
                add_barcode_file(os.path.join(ARCHIVE_DIR, filename), filename)

    barcodes.sort(key=lambda x: x['mtime'], reverse=True)
    return barcodes

def scan_archived():
    barcodes = []
    if not os.path.exists(ARCHIVE_DIR):
        return barcodes
    for filename in os.listdir(ARCHIVE_DIR):
        if filename.endswith('.html'):
            barcode = filename.replace('.html', '')
            filepath = os.path.join(ARCHIVE_DIR, filename)
            mtime = os.path.getmtime(filepath)
            time_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
            fields = extract_fields_from_html(filepath)
            info = get_barcode_info(barcode)
            barcodes.append({
                'barcode': barcode,
                'filename': filename,
                'time': time_str,
                'mtime': mtime,
                'fields': fields,
                'currentDealerOverride': info.get('currentDealerOverride', ''),
                'transferUpdatedAt': info.get('transferUpdatedAt', ''),
                'remark': info.get('remark', ''),
                'archiveTime': info.get('archiveTime', ''),
            })
    barcodes.sort(key=lambda x: x['mtime'], reverse=True)
    return barcodes

@app.route("/")
def index():
    return render_template("index.html", nav_links=visible_page_links())

@app.route("/api/barcodes", methods=["GET"])
def api_get_barcodes():
    barcodes = scan_barcodes()
    return jsonify({
        'success': True,
        'total': len(barcodes),
        'barcodes': [{
            'barcode': b['barcode'],
            'filename': b['filename'],
            'time': b['time'],
            'fields': b['fields'],
            'currentDealerOverride': b.get('currentDealerOverride', ''),
            'transferUpdatedAt': b.get('transferUpdatedAt', ''),
            'remark': b.get('remark', ''),
        } for b in barcodes]
    })

@app.route("/api/barcodes/archived", methods=["GET"])
def api_get_archived():
    barcodes = scan_archived()
    return jsonify({
        'success': True,
        'total': len(barcodes),
        'barcodes': [{
            'barcode': b['barcode'],
            'filename': b['filename'],
            'time': b['time'],
            'fields': b['fields'],
            'currentDealerOverride': b.get('currentDealerOverride', ''),
            'transferUpdatedAt': b.get('transferUpdatedAt', ''),
            'remark': b.get('remark', ''),
            'archiveTime': b.get('archiveTime', ''),
        } for b in barcodes]
    })

@app.route("/api/filter-options", methods=["GET"])
def api_get_filter_options():
    barcodes = scan_barcodes()
    options = get_filter_options(barcodes)
    return jsonify({
        'success': True,
        'filters': list(options.values()),
        'total': len(barcodes),
    })

@app.route("/api/barcodes/<barcode>", methods=["GET"])
def api_get_barcode_detail(barcode):
    filepath = _barcode_html_path(barcode)
    if not os.path.exists(filepath):
        return jsonify({'success': False, 'error': '文件不存在'})
    
    fields = extract_fields_from_html(filepath)
    info = get_barcode_info(barcode)
    fields = _apply_dealer_to_fields(fields, info.get('currentDealerOverride', ''))
    
    mtime = os.path.getmtime(filepath)
    time_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
    
    return jsonify({
        'success': True,
        'barcode': barcode,
        'time': time_str,
        'fields': fields
    })

@app.route("/api/export/xlsx", methods=["POST"])
def api_export_xlsx():
    data = request.get_json()
    selected_barcodes = data.get('barcodes', [])

    if not selected_barcodes:
        return jsonify({'success': False, 'error': '没有条码可导出'})

    if not HAS_OPENPYXL:
        return jsonify({
            'success': False,
            'error': '缺少 openpyxl 库，请运行: pip3 install openpyxl'
        })

    wb = Workbook()
    ws = wb.active
    ws.title = "条码查询结果"
    ws.sheet_view.showGridLines = False

    group_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    group_font = Font(color="FFFFFF", bold=True, size=12)
    header_font = Font(color="FFFFFF", bold=True, size=11)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    export_rows = _prepare_export_rows(selected_barcodes)
    columns = _build_export_columns(export_rows)

    for col_idx, column in enumerate(columns, 1):
        group_cell = ws.cell(row=1, column=col_idx, value=column['group'])
        group_cell.fill = group_fill
        group_cell.font = group_font
        group_cell.alignment = Alignment(horizontal='center', vertical='center')
        group_cell.border = thin_border

        cell = ws.cell(row=2, column=col_idx, value=column['label'])
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = thin_border

    merge_start = 1
    for col_idx in range(2, len(columns) + 2):
        if col_idx <= len(columns) and columns[col_idx - 1]['group'] == columns[merge_start - 1]['group']:
            continue
        if col_idx - 1 > merge_start:
            ws.merge_cells(start_row=1, start_column=merge_start, end_row=1, end_column=col_idx - 1)
        merge_start = col_idx

    for row_idx, export_row in enumerate(export_rows, 3):
        for col_idx, column in enumerate(columns, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=_export_cell_value(export_row, column))
            cell.border = thin_border
            cell.alignment = Alignment(vertical='top', wrap_text=True)

    for col_idx, column in enumerate(columns, 1):
        values = [_export_cell_value(row, column) for row in export_rows[:200]]
        ws.column_dimensions[get_column_letter(col_idx)].width = _suggest_column_width(column['label'], values)

    ws.row_dimensions[1].height = 24
    ws.row_dimensions[2].height = 28
    ws.freeze_panes = 'A3'
    if columns:
        ws.auto_filter.ref = f"A2:{get_column_letter(len(columns))}{len(export_rows) + 2}"

    output_path = os.path.join(BARCODE_DIR, 'export_result.xlsx')
    wb.save(output_path)

    return jsonify({
        'success': True,
        'message': f'已导出 {len(selected_barcodes)} 条记录',
        'filename': 'export_result.xlsx'
    })

@app.route("/api/transfer/summary", methods=["POST"])
def api_transfer_summary():
    data = request.get_json() or {}
    barcodes = data.get('barcodes') or []
    barcodes = [str(b).strip() for b in barcodes if str(b).strip()]
    barcodes, excluded = filter_disassembly_barcodes(barcodes)
    distributor = str(data.get('distributor') or '').strip()
    transfer_type = str(data.get('transfer_type') or '移出').strip()
    if not barcodes:
        return jsonify({'success': False, 'error': '输入的条码都是拆机条码，无需移库' if excluded else '请先选择要移库的条码', 'excluded': excluded})

    auto_library = {'queried': [], 'failed': []}
    representatives = _missing_product_library_representatives(barcodes)
    if representatives:
        with transfer_job_lock:
            if transfer_job['running']:
                return jsonify({'success': False, 'error': '已有移库任务正在执行，请等待完成后再汇总'})
        with batch_job_lock:
            if batch_job['running']:
                return jsonify({'success': False, 'error': '批量条码查询正在执行，请等待查询完成后再汇总'})
        ready, ready_message = _crm_ready_for_auto_query()
        if not ready:
            return jsonify({'success': False, 'error': ready_message})
        auto_library = ensure_product_library_for_barcodes(barcodes)

    summary = build_transfer_summary(barcodes, transfer_type, distributor)
    summary['excluded'] = excluded
    summary['auto_library'] = auto_library
    _exclude_unmatched_transfer_barcodes(summary)
    if not summary.get('groups'):
        return jsonify({
            'success': False,
            'error': '本次没有可移库条码，未匹配到产品信息的条码已临时排除',
            'summary': summary,
        })
    if summary['missing']:
        return jsonify({
            'success': False,
            'error': '部分条码没有查询结果，也没有匹配到条码前缀，请先维护条码匹配或查询一次该产品条码',
            'summary': summary,
        })
    if summary['incomplete']:
        return jsonify({
            'success': False,
            'error': '部分条码缺少产品名称或产品编码，无法自动汇总',
            'summary': summary,
        })

    return jsonify({'success': True, 'summary': summary})

@app.route("/api/distributor-history", methods=["GET"])
def api_distributor_history():
    return jsonify({
        'success': True,
        'dealers': combined_distributor_history(),
    })

@app.route("/api/distributor-history", methods=["POST"])
def api_save_distributor_history():
    data = request.get_json() or {}
    save_distributor_history(data.get('distributor'))
    return jsonify({
        'success': True,
        'dealers': combined_distributor_history(),
    })

@app.route("/api/transfer/summary/start", methods=["POST"])
def api_transfer_summary_start():
    data = request.get_json() or {}
    barcodes = data.get('barcodes') or []
    barcodes = [str(b).strip() for b in barcodes if str(b).strip()]
    barcodes, excluded = filter_disassembly_barcodes(barcodes)
    distributor = str(data.get('distributor') or '').strip()
    transfer_type = str(data.get('transfer_type') or '移出').strip()
    if not barcodes:
        return jsonify({'success': False, 'error': '输入的条码都是拆机条码，无需移库' if excluded else '请先选择要移库的条码', 'excluded': excluded})

    with summary_job_lock:
        if summary_job['running']:
            return jsonify({'success': False, 'error': '已有汇总预览正在执行，请等待完成'})
    with library_query_lock:
        if library_query_job['running']:
            return jsonify({'success': False, 'error': '条码匹配查询正在执行，请等待完成后再汇总'})
    with transfer_job_lock:
        if transfer_job['running']:
            return jsonify({'success': False, 'error': '已有移库任务正在执行，请等待完成后再汇总'})
    with batch_job_lock:
        if batch_job['running']:
            return jsonify({'success': False, 'error': '批量条码查询正在执行，请等待查询完成后再汇总'})

    with summary_job_lock:
        summary_job.update({
            'running': True,
            'done': False,
            'success': False,
            'error': '',
            'summary': None,
            'logs': [],
            'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'finished_at': '',
        })

    threading.Thread(
        target=_run_summary_job,
        args=(barcodes, transfer_type, distributor, excluded),
        daemon=True
    ).start()
    return jsonify({'success': True, 'message': '汇总预览已开始'})

@app.route("/api/transfer/summary/status", methods=["GET"])
def api_transfer_summary_status():
    with summary_job_lock:
        return jsonify({
            'success': True,
            'running': summary_job['running'],
            'done': summary_job['done'],
            'summary_success': summary_job['success'],
            'error': summary_job['error'],
            'summary': summary_job['summary'],
            'logs': list(summary_job['logs']),
            'started_at': summary_job['started_at'],
            'finished_at': summary_job['finished_at'],
        })

@app.route("/api/crm/transfer", methods=["POST"])
def api_crm_transfer():
    data = request.get_json() or {}
    barcodes = data.get('barcodes') or []
    barcodes = [str(b).strip() for b in barcodes if str(b).strip()]
    barcodes, excluded = filter_disassembly_barcodes(barcodes)
    distributor = str(data.get('distributor') or '').strip()
    transfer_type = str(data.get('transfer_type') or '移出').strip()
    remark = str(data.get('remark') or '').strip()

    if not barcodes:
        return jsonify({'success': False, 'error': '输入的条码都是拆机条码，无需移库' if excluded else '请先选择要移库的条码', 'excluded': excluded})
    if not distributor:
        return jsonify({'success': False, 'error': '目标分销商不能为空'})
    with library_query_lock:
        if library_query_job['running']:
            return jsonify({'success': False, 'error': '条码匹配查询正在执行，请等待完成后再移库'})

    representatives = _missing_product_library_representatives(barcodes)
    if representatives:
        with transfer_job_lock:
            if transfer_job['running']:
                return jsonify({'success': False, 'error': '已有移库任务正在执行，请等待完成后再提交'})
        with batch_job_lock:
            if batch_job['running']:
                return jsonify({'success': False, 'error': '批量条码查询正在执行，请等待查询完成后再移库'})
        ready, ready_message = _crm_ready_for_auto_query()
        if not ready:
            return jsonify({'success': False, 'error': ready_message})
        ensure_product_library_for_barcodes(barcodes)

    summary = build_transfer_summary(barcodes, transfer_type, distributor)
    summary['excluded'] = excluded
    _exclude_unmatched_transfer_barcodes(summary)
    if not summary.get('groups'):
        return jsonify({
            'success': False,
            'error': '本次没有可移库条码，未匹配到产品信息的条码已临时排除',
            'summary': summary,
        })
    if summary['missing']:
        return jsonify({'success': False, 'error': '部分条码没有查询结果，也没有匹配到条码前缀，请先维护条码匹配或查询一次该产品条码', 'summary': summary})
    if summary['incomplete']:
        return jsonify({'success': False, 'error': '部分条码缺少产品名称或产品编码，无法自动移库', 'summary': summary})

    with transfer_job_lock:
        if transfer_job['running']:
            return jsonify({'success': False, 'error': '已有移库任务正在执行，请等待完成后再提交'})
    with batch_job_lock:
        if batch_job['running']:
            return jsonify({'success': False, 'error': '批量条码查询正在执行，请等待查询完成后再移库'})
    with transfer_job_lock:
        transfer_job.update({
            'running': True,
            'done': False,
            'success': False,
            'error': '',
            'result': None,
            'summary': summary,
            'distributor': distributor,
            'transfer_type': transfer_type,
            'remark': remark,
            'logs': [],
            'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'finished_at': '',
        })

    threading.Thread(
        target=_run_transfer_job,
        args=(summary, distributor, transfer_type, remark),
        daemon=True
    ).start()

    return jsonify({
        'success': True,
        'started': True,
        'message': '移库任务已开始，请查看日志',
        'summary': summary,
        'transfer': {
            'dealer': '江西省天麓工贸有限公司',
            'distributor': distributor,
            'transfer_type': transfer_type,
            'remark': remark,
        },
    })

@app.route("/api/crm/transfer/status", methods=["GET"])
def api_crm_transfer_status():
    with transfer_job_lock:
        result = transfer_job.get('result') or {}
        order_no = result.get('order_no') if isinstance(result, dict) else ''
        if transfer_job['success'] and isinstance(result, dict) and result.get('pending_approval'):
            message = f"移库单已保存待审批：{order_no or '已保存'}"
        elif transfer_job['success']:
            message = f"移库单已创建：{order_no or '已保存'}"
        else:
            message = transfer_job['error']
        return jsonify({
            'success': True,
            'running': transfer_job['running'],
            'done': transfer_job['done'],
            'transfer_success': transfer_job['success'],
            'error': transfer_job['error'],
            'message': message,
            'result': transfer_job['result'],
            'summary': transfer_job['summary'],
            'transfer': {
                'dealer': '江西省天麓工贸有限公司',
                'distributor': transfer_job.get('distributor', ''),
                'transfer_type': transfer_job.get('transfer_type', ''),
                'remark': transfer_job.get('remark', ''),
            },
            'logs': list(transfer_job['logs']),
            'started_at': transfer_job['started_at'],
            'finished_at': transfer_job['finished_at'],
        })

@app.route("/barcode/<filename>")
def serve_barcode(filename):
    barcode = filename.rsplit('.', 1)[0]
    info = get_barcode_info(barcode)
    dealer = _clean_export_value(info.get('currentDealerOverride'))
    filepath = os.path.join(BARCODE_DIR, filename)
    if os.path.exists(filepath):
        if dealer:
            with open(filepath, 'r', encoding='utf-8') as f:
                html = f.read()
            html, _ = _replace_html_field_values(html, ["myproductdealer1", "dealername1"], dealer)
            return Response(html, mimetype='text/html')
        return send_from_directory(BARCODE_DIR, filename)
    filepath = os.path.join(ARCHIVE_DIR, filename)
    if dealer and os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            html = f.read()
        html, _ = _replace_html_field_values(html, ["myproductdealer1", "dealername1"], dealer)
        return Response(html, mimetype='text/html')
    return send_from_directory(ARCHIVE_DIR, filename)

@app.route("/barcode/archived/<filename>")
def serve_archived(filename):
    barcode = filename.rsplit('.', 1)[0]
    info = get_barcode_info(barcode)
    dealer = _clean_export_value(info.get('currentDealerOverride'))
    filepath = os.path.join(ARCHIVE_DIR, filename)
    if dealer and os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            html = f.read()
        html, _ = _replace_html_field_values(html, ["myproductdealer1", "dealername1"], dealer)
        return Response(html, mimetype='text/html')
    return send_from_directory(ARCHIVE_DIR, filename)

@app.route("/api/barcodes/<barcode>", methods=["DELETE"])
def api_delete_barcode(barcode):
    filepath = os.path.join(BARCODE_DIR, barcode + '.html')
    if not os.path.exists(filepath):
        filepath = os.path.join(ARCHIVE_DIR, barcode + '.html')

    if not os.path.exists(filepath):
        return jsonify({'success': False, 'error': '文件不存在'})

    try:
        os.remove(filepath)
        data = load_data()
        if barcode in data:
            del data[barcode]
            save_data(data)
        return jsonify({'success': True, 'message': f'已删除 {barcode}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route("/api/barcodes/<barcode>/remark", methods=["POST"])
def api_update_remark(barcode):
    data = request.get_json()
    remark = data.get('remark', '')
    info = get_barcode_info(barcode)
    info['remark'] = remark
    update_barcode_info(barcode, info)
    return jsonify({'success': True, 'remark': remark})

@app.route("/api/barcodes/<barcode>/archive", methods=["POST"])
def api_archive_barcode(barcode):
    success, msg = archive_barcode(barcode)
    return jsonify({'success': success, 'message': msg})

@app.route("/api/barcodes/<barcode>/unarchive", methods=["POST"])
def api_unarchive_barcode(barcode):
    success, msg = unarchive_barcode(barcode)
    return jsonify({'success': success, 'message': msg})

@app.route("/crm")
def crm_page():
    return render_template("crm.html", nav_links=visible_page_links())

@app.route("/transfer")
def transfer_page():
    return render_template("transfer.html", nav_links=visible_page_links())

@app.route("/product-library")
def product_library_page():
    return render_template("product_library.html", nav_links=visible_page_links(), account=current_account_public())

@app.route("/accounts")
def accounts_page():
    return render_template("accounts.html", nav_links=visible_page_links())

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/api/product-library", methods=["GET"])
def api_product_library():
    rows = sorted(load_product_library().values(), key=lambda row: row.get('prefix', ''))
    return jsonify({'success': True, 'products': rows, 'can_edit': is_admin_account(), 'account': current_account_public()})

@app.route("/api/product-library/lookup")
def api_product_library_lookup():
    barcode = str(request.args.get('barcode') or '').strip()
    if not barcode:
        return jsonify({'success': False, 'error': '请输入条码'})
    info = lookup_product_by_barcode(barcode)
    if not info or info.get('source') == 'unmatched':
        return jsonify({
            'success': False,
            'error': '条码匹配没有匹配到该条码前缀',
            'barcode': barcode,
            'prefix': product_prefix_from_barcode(barcode),
            'need_query': True,
        })
    return jsonify({'success': True, 'info': info})

@app.route("/api/product-library/query/start", methods=["POST"])
def api_product_library_query_start():
    data = request.get_json() or {}
    barcode = str(data.get('barcode') or '').strip()
    if not barcode:
        return jsonify({'success': False, 'error': '请输入条码'})
    if is_disassembly_barcode(barcode):
        return jsonify({'success': False, 'error': '这是拆机条码，CRM 不查询，也不写入条码匹配'})
    with transfer_job_lock:
        if transfer_job['running']:
            return jsonify({'success': False, 'error': '移库任务正在执行，请等待完成后再查询'})
    with batch_job_lock:
        if batch_job['running']:
            return jsonify({'success': False, 'error': '批量条码查询正在执行，请等待完成'})
    ready, _ready_message = _crm_ready_for_auto_query()
    if not ready:
        return jsonify({'success': False, 'error': '请先让管理员到在线查询页面登录 CRM 后再查询'})
    with library_query_lock:
        if library_query_job['running']:
            return jsonify({'success': False, 'error': '已有条码匹配查询正在执行'})
        library_query_job.update({
            'running': True,
            'done': False,
            'success': False,
            'barcode': barcode,
            'error': '',
            'logs': [],
            'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'finished_at': '',
        })
    threading.Thread(target=_run_library_query_job, args=(barcode,), daemon=True).start()
    return jsonify({'success': True, 'message': '条码查询已开始'})

@app.route("/api/product-library/query/status")
def api_product_library_query_status():
    with library_query_lock:
        return jsonify({
            'success': True,
            'running': library_query_job['running'],
            'done': library_query_job['done'],
            'query_success': library_query_job['success'],
            'barcode': library_query_job['barcode'],
            'error': library_query_job['error'],
            'logs': list(library_query_job['logs']),
            'started_at': library_query_job['started_at'],
            'finished_at': library_query_job['finished_at'],
        })

@app.route("/api/product-library", methods=["POST"])
def api_product_library_save():
    if not is_admin_account():
        return jsonify({'success': False, 'error': '只有管理员可以修改条码匹配'})
    data = request.get_json() or {}
    prefix = str(data.get('prefix') or '').strip()
    product_code = str(data.get('product_code') or '').strip()
    product_name = str(data.get('product_name') or '').strip()
    if not prefix or not product_code or not product_name:
        return jsonify({'success': False, 'error': '前缀、产品编码、产品名称都不能为空'})
    upsert_product_library(prefix, product_code, product_name, data.get('source_barcode') or '')
    return jsonify({'success': True})

@app.route("/api/product-library/<prefix>", methods=["DELETE"])
def api_product_library_delete(prefix):
    if not is_admin_account():
        return jsonify({'success': False, 'error': '只有管理员可以删除条码匹配规则'})
    data = load_product_library()
    if prefix in data:
        del data[prefix]
        save_product_library(data)
    return jsonify({'success': True})

@app.route("/api/accounts", methods=["GET"])
def api_accounts():
    if not is_admin_account():
        return jsonify({'success': False, 'error': '只有管理员可以查看账号列表', 'account': current_account_public(), 'accounts': []})
    return jsonify({'success': True, 'account': current_account_public(), 'accounts': [account_public(row) for row in load_accounts()]})

@app.route("/api/accounts", methods=["POST"])
def api_accounts_save():
    if not is_admin_account():
        return jsonify({'success': False, 'error': '只有管理员可以维护账号'})
    data = request.get_json() or {}
    account_id = str(data.get('id') or '').strip()
    username = str(data.get('username') or '').strip()
    display_name = str(data.get('display_name') or '').strip()
    password = str(data.get('password') or '').strip()
    permissions = data.get('permissions') or []
    if not username:
        return jsonify({'success': False, 'error': '账号不能为空'})
    if not isinstance(permissions, list):
        permissions = []
    allowed = {'crm', 'results', 'transfer', 'accounts', 'product-library'}
    permissions = [p for p in permissions if p in allowed]
    accounts = load_accounts()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if account_id:
        for row in accounts:
            if row.get('id') == account_id:
                row.update({
                    'username': username,
                    'display_name': display_name,
                    'permissions': permissions,
                    'updated_at': now,
                })
                if password:
                    row['password'] = password
                save_accounts(accounts)
                return jsonify({'success': True})
    if any(row.get('username') == username for row in accounts):
        return jsonify({'success': False, 'error': '账号已存在'})
    accounts.append({
        'id': uuid.uuid4().hex,
        'username': username,
        'display_name': display_name,
        'password': password,
        'permissions': permissions,
        'updated_at': now,
    })
    save_accounts(accounts)
    return jsonify({'success': True})

@app.route("/api/accounts/<account_id>", methods=["DELETE"])
def api_accounts_delete(account_id):
    if not is_admin_account():
        return jsonify({'success': False, 'error': '只有管理员可以删除账号'})
    if account_id == 'admin':
        return jsonify({'success': False, 'error': '默认管理员账号不能删除'})
    accounts = [row for row in load_accounts() if row.get('id') != account_id]
    save_accounts(accounts)
    return jsonify({'success': True})

@app.route("/api/app-auth/status")
def api_app_auth_status():
    return jsonify({'success': True, 'account': current_account_public(), 'is_admin': is_admin_account()})

@app.route("/api/app-auth/login", methods=["POST"])
def api_app_auth_login():
    data = request.get_json() or {}
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '')
    row = next((item for item in load_accounts() if item.get('username') == username), None)
    if not row or str(row.get('password') or '') != password:
        return jsonify({'success': False, 'error': '账号或密码错误'})
    session['account_username'] = username
    return jsonify({'success': True, 'account': account_public(row)})

@app.route("/api/app-auth/logout", methods=["POST"])
def api_app_auth_logout():
    session.pop('account_username', None)
    return jsonify({'success': True})

@app.route("/logout")
def app_logout_page():
    session.pop('account_username', None)
    return redirect("/login")

@app.route("/api/app-auth/password", methods=["POST"])
def api_app_auth_password():
    row = current_account()
    if not row:
        return jsonify({'success': False, 'error': '请先登录工具账号'})
    data = request.get_json() or {}
    old_password = str(data.get('old_password') or '')
    new_password = str(data.get('new_password') or '')
    if str(row.get('password') or '') != old_password:
        return jsonify({'success': False, 'error': '原密码不正确'})
    if not new_password:
        return jsonify({'success': False, 'error': '新密码不能为空'})
    accounts = load_accounts()
    for item in accounts:
        if item.get('username') == row.get('username'):
            item['password'] = new_password
            item['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            break
    save_accounts(accounts)
    return jsonify({'success': True})

@app.route("/api/crm/status")
def api_crm_status():
    return jsonify({
        'browser_running': crm_session.is_alive(),
        'logged_in': crm_session.logged_in
    })

@app.route("/api/crm/login", methods=["POST"])
def api_crm_login():
    """统一登录接口：自动填账号密码 → 点登录 → 点发送验证码 → 等待用户输入验证码填入 → 点确定"""
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    captcha = data.get('captcha', '').strip()

    if captcha:
        success, msg = crm_session.login_step2(captcha)
    else:
        success, msg = crm_session.login(username, password)
    return jsonify({'success': success, 'message': msg})

@app.route("/api/crm/login-step1", methods=["POST"])
def api_crm_login_step1():
    """Step1: 填账号密码 → 点登录 → 点发送验证码 → 返回给前端"""
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    success, msg = crm_session.login_step1(username, password)
    return jsonify({'success': success, 'message': msg})

@app.route("/api/crm/login-step2", methods=["POST"])
def api_crm_login_step2():
    """Step2: 收到验证码后提交"""
    data = request.get_json()
    captcha = data.get('captcha', '').strip()
    success, msg = crm_session.login_step2(captcha)
    return jsonify({'success': success, 'message': msg})

@app.route("/api/crm/logout", methods=["POST"])
def api_crm_logout():
    crm_session.logout()
    return jsonify({'success': True})

@app.route("/api/crm/login/cancel", methods=["POST"])
def api_crm_login_cancel():
    crm_session.cancel_login()
    return jsonify({'success': True})

@app.route("/api/crm/check-login", methods=["POST"])
def api_crm_check_login():
    """手动完成登录后调用此接口，后端检查浏览器状态并更新登录状态"""
    success, msg = crm_session.check_login_status()
    return jsonify({'success': success, 'message': msg, 'logged_in': crm_session.logged_in})

@app.route("/api/crm/query", methods=["POST"])
def api_crm_query():
    data = request.get_json()
    barcode = data.get('barcode', '').strip()
    if not barcode:
        return jsonify({'success': False, 'error': '条码不能为空'})
    if is_disassembly_barcode(barcode):
        return jsonify({'success': False, 'error': '这是拆机条码，CRM 不查询'})
    with transfer_job_lock:
        if transfer_job['running']:
            return jsonify({'success': False, 'error': '移库任务正在执行，请等待完成后再查询条码'})
    with library_query_lock:
        if library_query_job['running']:
            return jsonify({'success': False, 'error': '条码匹配查询正在执行，请等待完成后再查询'})
    success, result = crm_session.query_barcode(barcode)
    if success:
        return jsonify({
            'success': True,
            'barcode': result,
            'view_url': f'/barcode/{barcode}.html'
        })
    else:
        return jsonify({'success': False, 'error': result})

@app.route("/api/crm/batch/start", methods=["POST"])
def api_crm_batch_start():
    data = request.get_json()
    barcodes = data.get('barcodes') or []
    barcodes = [str(b).strip() for b in barcodes if str(b).strip()]
    barcodes, excluded = filter_disassembly_barcodes(barcodes)
    retry_limit = _normalize_retry_limit(data.get('retry_limit', DEFAULT_BATCH_RETRY_LIMIT))
    if not barcodes:
        return jsonify({'success': False, 'error': '输入的条码都是拆机条码，无需查询' if excluded else '条码不能为空', 'excluded': excluded})
    with transfer_job_lock:
        if transfer_job['running']:
            return jsonify({'success': False, 'error': '移库任务正在执行，请等待完成后再查询条码'})
    with library_query_lock:
        if library_query_job['running']:
            return jsonify({'success': False, 'error': '条码匹配查询正在执行，请等待完成后再批量查询'})

    with batch_job_lock:
        if batch_job['running']:
            return jsonify({'success': False, 'error': '已有批量查询正在运行'})
        batch_job.update({
            'running': True,
            'stop_requested': False,
            'barcodes': barcodes,
            'total': len(barcodes),
            'current': 0,
            'success': 0,
            'failed': 0,
            'retry_limit': retry_limit,
            'log_seq': 0,
            'logs': [],
            'results': [],
        })

    t = threading.Thread(target=_run_batch_job, args=(barcodes, retry_limit, excluded), daemon=True)
    t.start()
    return jsonify({'success': True, 'total': len(barcodes), 'retry_limit': retry_limit, 'excluded': excluded})

@app.route("/api/crm/batch/status")
def api_crm_batch_status():
    try:
        since = int(request.args.get('since') or 0)
    except (TypeError, ValueError):
        since = 0
    include_results = request.args.get('include_results') in ('1', 'true', 'yes')
    with batch_job_lock:
        logs = list(batch_job['logs'])
        if since > 0:
            logs = [row for row in logs if int(row.get('id') or 0) > since]
        payload = {
            'success': True,
            'running': batch_job['running'],
            'stop_requested': batch_job['stop_requested'],
            'total': batch_job['total'],
            'current': batch_job['current'],
            'success_count': batch_job['success'],
            'failed_count': batch_job['failed'],
            'retry_limit': batch_job['retry_limit'],
            'log_seq': batch_job.get('log_seq') or 0,
            'logs': logs,
            'results_count': len(batch_job['results']),
        }
        if include_results:
            payload['results'] = list(batch_job['results'])
        return jsonify(payload)

@app.route("/api/crm/batch/stop", methods=["POST"])
def api_crm_batch_stop():
    with batch_job_lock:
        if batch_job['running']:
            batch_job['stop_requested'] = True
            return jsonify({'success': True})
    return jsonify({'success': False, 'error': '没有正在运行的批量查询'})

if __name__ == "__main__":
    print("=" * 60)
    print("怡口 CRM 条码查询结果页面")
    print("=" * 60)
    print("请访问: http://localhost:5001")
    print("=" * 60)

    os.makedirs(BARCODE_DIR, exist_ok=True)

    app.run(host="0.0.0.0", port=5001, debug=False)
