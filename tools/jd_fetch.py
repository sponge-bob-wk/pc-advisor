# -*- coding: utf-8 -*-
"""京东商品页取数（有头 Edge，单页、人工节奏、不碰 XHR）。
用法: python jd_fetch.py <sku> <输出目录>
复用 jd_login.py 导出的 jd_state.json（如存在）。
命中「频控/验证」页时直接退出码 2，不重试、不绕验证。
"""
import sys, os, json, time, random

sys.path.insert(0, r"E:\电脑涨价分析\tools\pw")
from playwright.sync_api import sync_playwright

STATE = r"E:\电脑涨价分析\tools\jd_state.json"


def human_pause(a=3.0, b=8.0):
    time.sleep(random.uniform(a, b))


def blocked(title, body_head):
    marks = ["频控", "访问过于频繁", "验证", "安全验证", "京东-欢迎登录", "登录页面"]
    return any(m in (title or "") for m in marks) or any(m in (body_head or "") for m in marks)


def main():
    sku = sys.argv[1]
    outdir = sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    url = f"https://item.jd.com/{sku}.html"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",
            headless=False,
            args=["--start-maximized"],
        )
        kw = {"viewport": None, "locale": "zh-CN"}
        if os.path.exists(STATE):
            kw["storage_state"] = STATE
        ctx = browser.new_context(**kw)
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        human_pause(4, 7)

        title = page.title()
        body_head = page.evaluate("() => document.body.innerText.slice(0, 600)")
        if blocked(title, body_head):
            shot = os.path.join(outdir, f"jd-{sku}-blocked.png")
            page.screenshot(path=shot, full_page=False)
            print(json.dumps({"sku": sku, "status": "BLOCKED", "title": title,
                              "body": body_head[:200], "shot": shot}, ensure_ascii=False))
            ctx.close()
            browser.close()
            sys.exit(2)

        for _ in range(random.randint(3, 5)):
            page.mouse.wheel(0, random.randint(600, 1100))
            human_pause(1.2, 2.6)

        data = {"sku": sku, "url": url, "title": title, "status": "OK"}

        data["summary"] = page.evaluate(
            """() => {
                const out = [];
                document.querySelectorAll('.summary-top .p-parameter-list li, .summary .p-parameter-list li')
                  .forEach(li => { const t = li.innerText.replace(/\\s+/g,' ').trim(); if (t) out.push(t); });
                return out;
            }"""
        )

        data["detailTable"] = page.evaluate(
            """() => {
                const rows = [];
                document.querySelectorAll('.Ptable .Ptable-item dl, .p-parameter .Ptable-item dl').forEach(dl => {
                    const dts = [...dl.querySelectorAll('dt')].map(e => e.innerText.trim());
                    const dds = [...dl.querySelectorAll('dd')].map(e => e.innerText.replace(/\\s+/g,' ').trim());
                    for (let i = 0; i < dts.length; i++) rows.push([dts[i], dds[i] || '']);
                });
                return rows;
            }"""
        )

        data["priceVisible"] = page.evaluate(
            """() => {
                const pick = sel => { const e = document.querySelector(sel); return e ? e.innerText.replace(/\\s+/g,' ').trim() : null; };
                return {
                    jdPrice: pick('.summary-price .p-price .price') || pick('#jd-price'),
                    promo: pick('.summary-promotion') || pick('.prom-item'),
                    stock: pick('#store-prompt') || pick('.store-prompt')
                };
            }"""
        )
        data["bodyHead"] = page.evaluate("() => document.body.innerText.slice(0, 2000)")

        shot = os.path.join(outdir, f"jd-{sku}-detail.png")
        page.screenshot(path=shot, full_page=True)
        data["screenshot"] = shot

        with open(os.path.join(outdir, f"jd-{sku}-raw.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)

        print(json.dumps({"sku": sku, "status": "OK", "title": title,
                          "rows": len(data["detailTable"]), "shot": shot}, ensure_ascii=False))
        human_pause(2, 4)
        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
