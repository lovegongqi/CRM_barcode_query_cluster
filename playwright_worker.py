#!/usr/bin/env python3
"""独立的 Playwright 工作进程，通过 stdin/stdout 通信"""
import sys
import json
import time
import os

# 确保使用正确的 Python 环境
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_accounts():
    accounts_file = os.path.join(os.path.dirname(__file__), "accounts.json")
    if os.path.exists(accounts_file):
        with open(accounts_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def main():
    cfg = load_config()
    session_dir = cfg["session"]["state_path"]
    website_url = cfg["website"]["url"]

    playwright = None
    context = None
    page = None

    def cleanup():
        nonlocal playwright, context, page
        if context:
            try:
                context.close()
            except:
                pass
            context = None
        if playwright:
            try:
                playwright.stop()
            except:
                pass
            playwright = None
        page = None

    def is_alive():
        nonlocal page
        try:
            return page is not None
        except:
            return False

    def ensure_browser():
        nonlocal playwright, context, page
        os.makedirs(session_dir, exist_ok=True)

        # 检查锁文件
        lock_file = os.path.join(session_dir, "SingletonLock")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except:
                pass

        if is_alive():
            return True

        cleanup()
        playwright = sync_playwright().start()
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=session_dir,
            headless=False,
            viewport=cfg["browser"]["viewport"]
        )
        page = context.pages[0] if context.pages else None
        if not page:
            return False
        page.goto(website_url, timeout=30000)
        time.sleep(3)
        return True

    def do_login_step1(username, password):
        nonlocal page
        if not ensure_browser():
            return False, "浏览器启动失败"

        accounts = load_accounts()
        if username not in accounts:
            return False, f"账号 {username} 不在 accounts.json 中"
        saved_pw = accounts[username].get("password", "")
        if password != saved_pw:
            return False, "密码不匹配"

        # 检查是否已登录
        url = page.url.lower()
        if "login" not in url:
            time.sleep(1)
            body_text = page.inner_text("body")
            if "退出" in body_text or "注销" in body_text:
                return True, "已登录（会话有效）"

        time.sleep(1)

        # 填用户名
        for selector in ["input[name='username']", "input[name='user']", "input[name='logonUsername']", "#username", "#user", "input[type='text']"]:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    time.sleep(0.3)
                    el.press("Control+a")
                    time.sleep(0.1)
                    el.type(username, delay=100)
                    time.sleep(0.5)
                    break
            except:
                continue

        # 填密码
        for selector in ["input[name='password']", "input[name='pwd']", "#password", "#pwd", "input[type='password']"]:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    time.sleep(0.3)
                    el.press("Control+a")
                    time.sleep(0.1)
                    el.type(password, delay=100)
                    time.sleep(0.5)
                    break
            except:
                continue

        # 点登录
        for selector in ["button[type='submit']", "input[type='submit']", "#loginBtn", ".login-btn", "button:has-text('登录')", "a:has-text('登录')"]:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    time.sleep(2)
                    break
            except:
                continue

        # 点发送验证码
        for selector in ["button:has-text('发送验证码')", "button:has-text('获取验证码')", "a:has-text('发送验证码')", "a:has-text('获取验证码')"]:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    time.sleep(2)
                    return True, "captcha_sent"
            except:
                continue

        return True, "captcha_not_found"

    def do_login_step2(captcha):
        nonlocal page
        if not is_alive():
            return False, "浏览器未启动"
        if not captcha:
            return False, "验证码不能为空"

        # 等待验证码输入框
        captcha_input = None
        for _ in range(20):
            for selector in ["input[placeholder='验证码']", "input[name='verifyCode']", "input[name='captcha']"]:
                try:
                    el = page.query_selector(selector)
                    if el and el.is_visible():
                        captcha_input = el
                        break
                except:
                    continue
            if captcha_input:
                break
            time.sleep(0.5)

        if not captcha_input:
            return False, "验证码输入框未出现"

        captcha_input.click()
        time.sleep(0.3)
        captcha_input.press("Control+a")
        time.sleep(0.1)
        captcha_input.press("Backspace")
        time.sleep(0.1)
        captcha_input.type(captcha, delay=150)
        time.sleep(0.5)

        # 点确定
        buttons = page.query_selector_all("button")
        for btn in buttons:
            try:
                if btn.is_visible():
                    text = btn.inner_text().strip()
                    if "确" in text and "定" in text:
                        btn.click()
                        time.sleep(3)
                        break
            except:
                continue

        # 检查登录结果
        url = page.url.lower()
        body_text = page.inner_text("body")
        if "login" not in url and ("退出" in body_text or "注销" in body_text or "首页" in body_text):
            return True, "登录成功"
        return False, "验证码可能错误"

    def do_query(barcode):
        nonlocal page
        if not is_alive():
            return False, "浏览器未启动"

        time.sleep(3)
        input_box = page.query_selector("input[name='CrystalReportViewer1_p0DiscreteValue']")
        if not input_box:
            return False, "未找到条码输入框"

        # 等待加载完成
        for _ in range(30):
            try:
                imgs = page.query_selector_all("img")
                has_loading = False
                for img in imgs:
                    try:
                        if img.get_attribute("src") and "wait" in img.get_attribute("src"):
                            parent = page.evaluate("(el) => el.offsetParent !== null", img)
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

        clicked = False
        try:
            page.evaluate("if(typeof CrystalReportViewer1_submit === 'function'){ CrystalReportViewer1_submit(); }")
            clicked = True
            time.sleep(1)
        except:
            pass

        if not clicked:
            try:
                for link in page.query_selector_all("a"):
                    if "确定" in (link.inner_text() or "").strip():
                        link.click()
                        clicked = True
                        break
            except:
                pass

        if not clicked:
            return False, "提交查询失败"

        # 等待结果
        max_wait = 60
        for _ in range(max_wait):
            time.sleep(1)
            try:
                html_content = page.evaluate("""() => {
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
                if html_content and len(html_content.strip()) > 1000:
                    return True, barcode
            except:
                break

        return False, "查询结果为空"

    def do_status():
        return is_alive()

    def do_logout():
        cleanup()
        return True

    # 主循环：处理命令
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
            action = cmd.get("action")
            params = cmd.get("params", {})

            if action == "login_step1":
                success, msg = do_login_step1(params.get("username", ""), params.get("password", ""))
                print(json.dumps({"success": success, "message": msg}), flush=True)
            elif action == "login_step2":
                success, msg = do_login_step2(params.get("captcha", ""))
                print(json.dumps({"success": success, "message": msg}), flush=True)
            elif action == "query":
                success, result = do_query(params.get("barcode", ""))
                print(json.dumps({"success": success, "result": result}), flush=True)
            elif action == "status":
                print(json.dumps({"alive": do_status()}), flush=True)
            elif action == "logout":
                do_logout()
                print(json.dumps({"success": True}), flush=True)
            elif action == "quit":
                cleanup()
                break
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)

if __name__ == "__main__":
    main()