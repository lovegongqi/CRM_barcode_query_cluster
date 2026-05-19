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
from collections import OrderedDict
from flask import Flask, render_template, request, jsonify, send_from_directory, Response
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

CRM_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_crm_config():
    config_paths = [
        CRM_CONFIG_PATH,
        os.path.join(os.path.dirname(__file__), "config.example.json"),
        os.path.join(os.path.dirname(__file__), "config.docker.example.json"),
    ]
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
                headless=cfg["browser"].get("headless", True),
                viewport=cfg["browser"]["viewport"]
            )
        except Exception as e:
            error_msg = str(e)
            print(f"  [DEBUG] Browser launch error: {error_msg[:200]}")
            if "ProcessSingleton" in error_msg or "Failed to create a ProcessSingleton" in error_msg:
                # 锁文件导致的错误，清理后重试
                self._close_browser()
                self._cleanup_singleton_lock(session_dir)
                self.playwright = sync_playwright().start()
                self.context = self.playwright.chromium.launch_persistent_context(
                    user_data_dir=session_dir,
                    headless=cfg["browser"].get("headless", True),
                    viewport=cfg["browser"]["viewport"]
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
        """删除 SingletonLock 文件"""
        lock_file = os.path.join(session_dir, "SingletonLock")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception:
                pass

    def login_step1(self, username, password):
        """Step1: 填账号密码 → 点登录 → 点发送验证码（你收到短信）"""
        if not HAS_PLAYWRIGHT:
            return False, "Playwright 未安装"
        with self.lock:
            try:
                self.logged_in = False
                if not self._ensure_browser():
                    return False, "浏览器启动失败"

                # 检查是否已登录（会话有效）
                url = self.page.url.lower()
                if "login" not in url:
                    time.sleep(1)
                    body_text = self.page.inner_text("body")
                    if "退出" in body_text or "注销" in body_text:
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
                    return False, "验证码输入框未出现，请重新触发发送验证码"

                captcha_input.click(); time.sleep(0.3)
                captcha_input.press("Control+a"); time.sleep(0.1)
                captcha_input.press("Backspace"); time.sleep(0.1)
                captcha_input.type(captcha, delay=150)
                time.sleep(0.5)

                # 点确定
                if not self._click_confirm_near_captcha(captcha_scope, captcha_input):
                    return False, "验证码已填入，但未找到确定按钮"
                time.sleep(3)

                # 检查是否登录成功
                url = self.page.url.lower()
                body_text = self.page.inner_text("body")
                if (
                    "login" not in url or
                    "退出" in body_text or
                    "注销" in body_text or
                    "首页" in body_text or
                    "报表" in body_text or
                    "home" in url
                ):
                    self.logged_in = True
                    self.needs_navigation = True
                    return True, "登录成功"
                return False, "验证码可能错误，请重试"

            except Exception as e:
                return False, str(e)

    def login(self, username, password, captcha=None):
        """统一登录：可选传入验证码（有就自动填入并点确定）"""
        if not HAS_PLAYWRIGHT:
            return False, "Playwright 未安装"
        with self.lock:
            try:
                if not self._ensure_browser():
                    return False, "浏览器启动失败"

                # 检查是否已登录
                url = self.page.url.lower()
                if "login" not in url:
                    time.sleep(1)
                    body_text = self.page.inner_text("body")
                    if "退出" in body_text or "注销" in body_text:
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

                    # 多次检查登录状态，等待页面加载完成
                    for _ in range(10):
                        url = self.page.url.lower()
                        body_text = self.page.inner_text("body")
                        if "login" not in url and ("退出" in body_text or "注销" in body_text or "首页" in body_text):
                            self.logged_in = True
                            self.needs_navigation = True
                            return True, "登录成功"
                        time.sleep(1)

                    # 最后再检查一次
                    url = self.page.url.lower()
                    body_text = self.page.inner_text("body")
                    if "login" not in url:
                        self.logged_in = True
                        self.needs_navigation = True
                        return True, "登录成功（页面已跳转）"
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

    def query_barcode(self, barcode):
        with self.lock:
            if not self.is_alive():
                return False, "浏览器未启动"
            try:
                # 导航到报表页面（只在第一次查询时）
                if self.needs_navigation:
                    print("  [导航] 准备打开报表页面...")
                    if not self.prepare_next_report():
                        if self.last_report_error:
                            return False, f"报表页面加载错误: {self.last_report_error}"
                        return False, "打开查询条码所有信息报表失败"
                    self.needs_navigation = False

                # 切换到报表标签页
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
                    input_box = self._reload_report_for_input()
                    if not input_box or not input_box.is_visible():
                        input_box = self._find_input_in_open_report_tabs()
                        if not input_box or not input_box.is_visible():
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
                input_box.type(barcode, delay=100)
                print(f"  已输入条码: {barcode}")

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
                            print(f"  报表已加载完成（{js_check['length']} 字符）")
                            break
                        elif (wait_i + 1) % 10 == 0:
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
                    html_dir = os.path.join(os.path.dirname(__file__), "barcode")
                    os.makedirs(html_dir, exist_ok=True)
                    output_file = os.path.join(html_dir, f"{barcode}.html")
                    with open(output_file, "w", encoding="utf-8") as f:
                        f.write(html_content)
                    self.needs_navigation = False
                    return True, barcode
                else:
                    self.needs_navigation = False
                    return False, "查询结果为空"

            except Exception as e:
                self.needs_navigation = True
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

    def query_barcode(self, barcode):
        return self._call("query_barcode", barcode)

crm_session = CRMWorker()

batch_job_lock = threading.Lock()
batch_job = {
    'running': False,
    'stop_requested': False,
    'barcodes': [],
    'total': 0,
    'current': 0,
    'success': 0,
    'failed': 0,
    'logs': [],
    'results': [],
}

def _batch_log(message, level='dim'):
    with batch_job_lock:
        batch_job['logs'].append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'message': message,
            'level': level,
        })
        batch_job['logs'] = batch_job['logs'][-300:]

def _run_batch_job(barcodes):
    _batch_log(f"开始批量查询 {len(barcodes)} 个条码...", 'info')
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

        _batch_log(f"正在查询第 {idx}/{len(barcodes)} 个：{barcode}", 'info')
        success, result = crm_session.query_barcode(barcode)
        with batch_job_lock:
            if success:
                batch_job['success'] += 1
                batch_job['results'].append({
                    'barcode': barcode,
                    'success': True,
                    'view_url': f'/barcode/{barcode}.html',
                })
                batch_job['logs'].append({
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'message': f"✓ {barcode} 查询成功",
                    'level': 'success',
                })
            else:
                batch_job['failed'] += 1
                batch_job['results'].append({
                    'barcode': barcode,
                    'success': False,
                    'error': result,
                })
                batch_job['logs'].append({
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'message': f"✗ {barcode} 查询失败: {result}",
                    'level': 'error',
                })
            batch_job['logs'] = batch_job['logs'][-300:]

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
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

app = Flask(__name__)

BARCODE_DIR = "barcode"
ARCHIVE_DIR = os.path.join(BARCODE_DIR, "archived")
DATA_FILE = os.path.join(BARCODE_DIR, "barcode_data.json")

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

EXPORT_FIELDS_ORDER = [
    ('newname1_sr1', '条码号'),
    ('remark', '备注'),
    ('newisclosed1_sr2', '结单状态'),
    ('SHIPSTATUS1', '装箱单状态'),
    ('newproductidName1_sr2', '机型'),
    ('ProductNumber1', '物料编码'),
    ('myproductdealer1_sr5', '所属经销商'),
    ('newdealername1_sr2', '服务经销商'),
    ('newstationidName1', '服务站'),
    ('typestr1_sr2', '服务类型'),
    ('statustr1_sr2', '服务单状态'),
    ('servno1_sr2', '服务单号'),
    ('name1_sr2', '客户'),
    ('newtelephone1_sr2', '电话'),
    ('newaddress1_sr2', '地址'),
    ('zxd1', '装箱单号'),
    ('shipdate1', '发货日期'),
    ('newerpshipno1', '发货单号'),
    ('newordsalesorderidName1', '订单号'),
    ('buno1_sr8', '移库单号'),
    ('transstockdate1_sr8', '移库日期'),
]

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
    archived = get_archived_set()
    for filename in os.listdir(BARCODE_DIR):
        if filename.endswith('.html') and filename.replace('.html', '') not in archived:
            barcode = filename.replace('.html', '')
            filepath = os.path.join(BARCODE_DIR, filename)
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
                'remark': info.get('remark', ''),
            })
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
                'remark': info.get('remark', ''),
                'archiveTime': info.get('archiveTime', ''),
            })
    barcodes.sort(key=lambda x: x['mtime'], reverse=True)
    return barcodes

@app.route("/")
def index():
    return render_template("index.html")

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
    filepath = os.path.join(BARCODE_DIR, barcode + '.html')
    if not os.path.exists(filepath):
        return jsonify({'success': False, 'error': '文件不存在'})
    
    fields = extract_fields_from_html(filepath)
    
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

    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    headers = [label for _, label in EXPORT_FIELDS_ORDER]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = thin_border

    for row_idx, barcode_data in enumerate(selected_barcodes, 2):
        fields = barcode_data.get('fields', {})
        for col_idx, (field_id, _) in enumerate(EXPORT_FIELDS_ORDER, 1):
            if field_id == 'remark':
                value = barcode_data.get('remark', '')
            else:
                value = _get_field(fields, field_id)
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin_border
            cell.alignment = Alignment(vertical='center', wrap_text=True)

    for col_idx in range(1, len(EXPORT_FIELDS_ORDER) + 1):
        ws.column_dimensions[chr(64 + col_idx) if col_idx <= 26 else chr(64 + (col_idx-1)//26) + chr(65 + (col_idx-1)%26)].width = 18

    output_path = os.path.join(BARCODE_DIR, 'export_result.xlsx')
    wb.save(output_path)

    return jsonify({
        'success': True,
        'message': f'已导出 {len(selected_barcodes)} 条记录',
        'filename': 'export_result.xlsx'
    })

@app.route("/barcode/<filename>")
def serve_barcode(filename):
    return send_from_directory(BARCODE_DIR, filename)

@app.route("/barcode/archived/<filename>")
def serve_archived(filename):
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
    return render_template("crm.html")

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
    if not barcodes:
        return jsonify({'success': False, 'error': '条码不能为空'})

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
            'logs': [],
            'results': [],
        })

    t = threading.Thread(target=_run_batch_job, args=(barcodes,), daemon=True)
    t.start()
    return jsonify({'success': True, 'total': len(barcodes)})

@app.route("/api/crm/batch/status")
def api_crm_batch_status():
    with batch_job_lock:
        return jsonify({
            'success': True,
            'running': batch_job['running'],
            'stop_requested': batch_job['stop_requested'],
            'total': batch_job['total'],
            'current': batch_job['current'],
            'success_count': batch_job['success'],
            'failed_count': batch_job['failed'],
            'logs': list(batch_job['logs']),
            'results': list(batch_job['results']),
        })

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
