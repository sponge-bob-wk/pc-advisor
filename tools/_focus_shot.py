# -*- coding: utf-8 -*-
"""一次性：Edge 主窗口置前（点最大化）→ 截图 → 存盘。
MCP 观察状态不跨进程，必须同进程连着做。
"""
import sys, base64, re
sys.path.insert(0, r"E:\电脑涨价分析\tools")
from cu import Client

PID = 30032
WIN = 526504
OUT = r"E:\电脑涨价分析\crawl\shots\energy-results.jpg"

c = Client()
try:
    def state(shot=False):
        args = {"pid": PID, "windowId": WIN}
        if shot:
            args["disableScreenshot"] = False
        else:
            args["disableScreenshot"] = True
            args["disableDiff"] = True
        return c.call("get_app_state", args)

    def contents(st):
        return (st.get("result") or {}).get("content") or []

    st = state(False)
    tree = next((x.get("text", "") for x in contents(st) if x.get("type") == "text"), "")
    print("最大化 in tree:", "最大化" in tree)
    # 点击最大化（id=5，来自树）
    r = c.call("click", {"pid": PID, "element_id": "5"})
    rc = (r.get("result") or {}).get("content") or []
    txt = next((x.get("text", "") for x in rc if x.get("type") == "text"), "")
    print("click:", txt[:120])

    st2 = state(True)
    for x in contents(st2):
        if x.get("type") == "image":
            data = x.get("data") or ""
            with open(OUT, "wb") as f:
                f.write(base64.b64decode(data))
            print("saved", OUT, len(data) * 3 // 4 // 1024, "KB")
            break
    else:
        print("no image content item")
finally:
    try:
        c.p.terminate()
    except Exception:
        pass
