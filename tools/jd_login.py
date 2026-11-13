# -*- coding: utf-8 -*-
"""有头 Edge：等用户扫码登录京东，成功后把 cookie 写成 storage_state。

本机两个坑：
1) E 盘新建 profile 目录偶发 WinError 5 → 用 launch() 的临时 profile；
2) Playwright 自带的 storage_state(path=) 写文件偶发 WinError 13 →
   自己用 ctx.cookies() 组装 state，普通 open() 写，失败则退到 %TEMP%。
登录期间不跳转页面，避免触发京东 PC 频控页。
"""
import sys, os, json, time

sys.path.insert(0, r"E:\电脑涨价分析\tools\pw")
from playwright.sync_api import sync_playwright

CANDIDATES = [
    r"E:\电脑涨价分析\tools\jd_state.json",
    os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "jd_state.json"),
]


def has_login(ctx):
    for c in ctx.cookies():
        if c.get("name") == "pt_key" and c.get("value"):
            return True
    return False


def save(ctx):
    state = {"cookies": ctx.cookies(), "origins": []}
    blob = json.dumps(state, ensure_ascii=False)
    for path in CANDIDATES:
        for _ in range(3):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(blob)
                return path
            except Exception as e:
                print("SAVE_RETRY", path, e, flush=True)
                time.sleep(2)
    return None


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",
            headless=False,
            args=["--start-maximized"],
        )
        ctx = browser.new_context(viewport=None, locale="zh-CN")
        page = ctx.new_page()
        page.goto("https://www.jd.com", wait_until="domcontentloaded", timeout=60000)
        print("OPENED", page.title(), flush=True)
        print("PLEASE_LOGIN", flush=True)

        deadline = time.time() + 600
        logged = False
        while time.time() < deadline:
            time.sleep(4)
            try:
                if has_login(ctx):
                    logged = True
                    break
            except Exception as e:
                print("ERR", e, flush=True)

        print("RESULT", "LOGGED_IN" if logged else "TIMEOUT", flush=True)
        if logged:
            time.sleep(2)
            path = save(ctx)
            print("STATE_SAVED", path, flush=True)
            try:
                page.screenshot(path=os.path.join(os.environ.get("TEMP", "."), "login_ok.png"))
            except Exception:
                pass
        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
