# -*- coding: utf-8 -*-
"""energy_query.py —— 通过无障碍树驱动能效标识网查询并读回结果表。

为什么走无障碍树：
  Edge 扩展的截图通道在本机全程超时；网页正文（结果表格）又不在
  dom_cua 的可交互元素快照里。但桌面级 get_app_state 的无障碍树
  包含网页正文文本节点 —— 这是唯一能稳定读到表格的通道。

用法：
  python tools/energy_query.py --producer 联想 [--page N]
  python tools/energy_query.py --model "FA608"
输出：JSON 行（型号 / 等级 / 备案号 / 生产者 / 日期），同时打印原始文本片段便于核对。
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cu import Client  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def edge():
    c = Client()
    try:
        st = c.call("list_apps", {})
        txt = "".join(x.get("text", "") for x in (st.get("result") or {}).get("content") or []
                      if x.get("type") == "text")
    finally:
        c.p.terminate()
    pid = win = None
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("Microsoft Edge [pid="):
            pid = int(s.split("pid=")[1].split(",")[0])
            continue
        if pid and win is None and s.startswith("windowId="):
            win = int(s.split("windowId=")[1].split()[0])
    return pid, win


def tree(c, pid, win):
    st = c.call("get_app_state", {"pid": pid, "windowId": win,
                                  "disableScreenshot": True, "disableDiff": True})
    return "".join(x.get("text", "") for x in (st.get("result") or {}).get("content") or []
                   if x.get("type") == "text")


def find_id(txt, pattern):
    m = re.search(pattern + r"[^\n]*?id=(\d+)", txt)
    return m.group(1) if m else None


def main():
    args = sys.argv[1:]
    producer = model = kw = None
    for i, a in enumerate(args):
        if a == "--producer":
            producer = args[i + 1]
        elif a == "--model":
            model = args[i + 1]
        elif a == "--kw":
            kw = args[i + 1]

    pid, win = edge()
    if not pid:
        print("EDGE_NOT_FOUND")
        return 1

    c = Client()
    try:
        txt = tree(c, pid, win)
        if "请输入产品型号" not in txt:
            print("NOT_ON_QUERY_PAGE；请先让 Edge 打开 productFiling 页面并置于前台")
            return 2

        # 填表：先清空再输入
        target = None
        if model:
            target = find_id(txt, r'edit "请输入产品型号"')
            value = model
        elif producer:
            target = find_id(txt, r'edit "请输入生产者"')
            value = producer
        elif kw:
            target = find_id(txt, r'edit "请输入产品型号"')
            value = kw
        if not target:
            print("INPUT_NOT_FOUND")
            return 2

        c.call("click", {"pid": pid, "element_id": target})
        time.sleep(0.2)
        c.call("press_key", {"pid": pid, "key": "CTRL+A"})
        c.call("press_key", {"pid": pid, "key": "DELETE"})
        c.call("type_text", {"pid": pid, "text": value, "element_id": target})
        time.sleep(0.3)

        txt = tree(c, pid, win)
        sid = find_id(txt, r'button "搜索"')
        if not sid:
            print("SEARCH_BTN_NOT_FOUND")
            return 2
        c.call("click", {"pid": pid, "element_id": sid})
        time.sleep(3.0)

        txt = tree(c, pid, win)
        # 结果行判定：含备案号的行
        lines = [l.strip() for l in txt.splitlines()]
        rows = []
        reg_no = re.compile(r"\b\d{8}-CEL\d{7}-\d{6}\b|\b\d{8}-\d{6}-\d{9,}\b")
        for i, l in enumerate(lines):
            if reg_no.search(l):
                rows.append(l)
        print(f"匹配到含备案号的行 {len(rows)} 条")
        for l in rows[:20]:
            print("  ", l[:200])
        # 把整棵树存下来，便于离线解析（表格结构靠上下文）
        out = os.path.join(ROOT, "data", "crawl", "energy_raw")
        os.makedirs(out, exist_ok=True)
        tag = (model or producer or kw or "q").replace("/", "_").strip()
        fp = os.path.join(out, f"auto-{tag}-{time.strftime('%Y%m%d-%H%M%S')}.txt")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(txt)
        print("raw tree saved:", fp)
        return 0
    finally:
        c.p.terminate()


if __name__ == "__main__":
    sys.exit(main())
