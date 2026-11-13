# -*- coding: utf-8 -*-
"""energy_batch.py —— 按关键词批量查能效备案，解析成结构化行，并回填机型池。

背景：Edge 扩展截图通道坏了，但在桌面级无障碍树里能稳定读到查询结果表。
每行 8 个 data-item：序号 / 产品类型 / 型号 / 备案号 / 生产者 / 能效等级 / 备案类型 / 日期。

用法：
  python tools/energy_batch.py 小新 ThinkBook ThinkPad
输出：
  data/efficiency_ledger_auto.json   本次抓到的行（含 queryKeyword）
  并打印与机型池的匹配建议（不自动写库，避免错挂）
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cu import Client  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "efficiency_ledger_auto.json")

REG = re.compile(r"^\d{8}-CEL\d{7}-\d{6}$|^\d{8}-\d{6}-\d{9,}$")
ITEM = re.compile(r'data-item "([^"]*)" \(focused=false, input_target=false\) id=(\d+)')


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


def parse_rows(txt):
    """把 data-item 序列按 8 列一组切成行。

    踩过的坑：一开始用"遇到 1-10 的数字就当作新行序号"来切，
    但能效等级也是 1/2/3，会把每行从中间劈开、全部丢掉。
    表格固定 8 列（序号/产品类型/型号/备案号/生产者/等级/类型/日期），
    直接按 8 个一组切，再用备案号格式校验，最稳。
    """
    vals = [m.group(1) for m in ITEM.finditer(txt)]
    out = []
    for i in range(0, len(vals) - 7, 8):
        chunk = vals[i:i + 8]
        if not REG.match(chunk[3]):
            continue
        out.append({"no": chunk[0], "productType": chunk[1], "model": chunk[2],
                    "regNo": chunk[3], "producer": chunk[4], "level": chunk[5],
                    "filingType": chunk[6], "date": chunk[7]})
    return out


def find_id(txt, pattern):
    m = re.search(pattern + r"[^\n]*?id=(\d+)", txt)
    return m.group(1) if m else None


_LAST_SIG = {"sig": None}


def query(c, pid, win, keyword, pages=1):
    txt = tree(c, pid, win)
    inp = find_id(txt, r'edit "请输入产品型号"')
    if not inp:
        print("INPUT_NOT_FOUND"); return []
    c.call("click", {"pid": pid, "element_id": inp})
    time.sleep(0.2)
    c.call("press_key", {"pid": pid, "key": "CTRL+A"})
    c.call("press_key", {"pid": pid, "key": "DELETE"})
    c.call("type_text", {"pid": pid, "text": keyword, "element_id": inp})
    time.sleep(0.3)

    # 发送前必须确认输入框里真的是这个关键词。
    # 踩过的坑：页面未聚焦时点击/输入会静默失败，脚本照样点"搜索"，
    # 结果页还是上一次的 —— 于是把 A 公司的备案记到 B 机器头上。
    txt = tree(c, pid, win)
    got = re.search(r'edit "请输入产品型号"[^\n]*val="([^"]*)"', txt)
    if not got or got.group(1).strip() != keyword.strip():
        print(f"  !! 输入框未写入（当前={got.group(1) if got else '读取失败'}），跳过 {keyword}")
        return []

    # 记录"点搜索前"页面上的备案号集合。
    # 只跟上一次查询比较是不够的 —— 本次运行的第一条查询没有对照物，
    # 页面没刷新就会把上一轮的结果当成本轮结果（真实踩过：微星查到联想）。
    pre_sig = tuple(sorted(re.findall(r'\d{8}-CEL\d{7}-\d{6}', txt)))

    btn = find_id(txt, r'button "搜索"')
    c.call("click", {"pid": pid, "element_id": btn})
    time.sleep(2.5)

    allrows = []
    for pg in range(pages):
        txt = tree(c, pid, win)
        rows = parse_rows(txt)
        sig = tuple(sorted(r["regNo"] for r in rows))
        if rows and sig == pre_sig:
            print(f"  !! {keyword} 搜索后页面未变化（与搜索前一致），本次不采信")
            return []
        if rows and sig == _LAST_SIG["sig"]:
            print(f"  !! {keyword} 的结果与上一次查询完全相同，疑似未刷新，本次不采信")
            return []
        _LAST_SIG["sig"] = sig
        for r in rows:
            r["queryKeyword"] = keyword
            r["page"] = pg + 1
        allrows.extend(rows)
        if pg == pages - 1:
            break
        # 下一页：找分页器里的「下一页」按钮
        nxt = find_id(txt, r'button "[^"]*下一页[^"]*"')
        if not nxt:
            break
        c.call("click", {"pid": pid, "element_id": nxt})
        time.sleep(2.5)
    return allrows


def main():
    kws = sys.argv[1:]
    if not kws:
        print(__doc__); return 1
    pid, win = edge()
    if not pid:
        print("EDGE_NOT_FOUND"); return 1
    c = Client()
    try:
        txt = tree(c, pid, win)
        if "请输入产品型号" not in txt:
            print("请在 Edge 里打开 https://www.energylabel.com.cn/productFiling 并置于前台")
            return 2
        got = []
        for kw in kws:
            rows = query(c, pid, win, kw)
            print(f"[{kw}] {len(rows)} 行")
            for r in rows[:12]:
                print(f"    {r['model']:<22} {r['level']}级  {r['date']}  {r['producer'][:12]}")
            got.extend(rows)
            time.sleep(1.0)

        # 合并历史
        old = []
        if os.path.exists(OUT):
            old = json.load(open(OUT, encoding="utf-8")).get("records") or []
        seen = {(r["regNo"], r["model"]) for r in old}
        merged = old + [r for r in got if (r["regNo"], r["model"]) not in seen]
        json.dump({"capturedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "source": "中国能效标识网 产品备案查询（无障碍树读取）",
                   "records": merged},
                  open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n累计 {len(merged)} 行 -> {OUT}")
        return 0
    finally:
        c.p.terminate()


if __name__ == "__main__":
    sys.exit(main())
