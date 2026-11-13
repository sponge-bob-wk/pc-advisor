#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专业门槛查询（演示版，验证 majors.json 可用）。

用法：
    python tools/major_lookup.py 建筑学
    python tools/major_lookup.py 计科 --budget 8000
    python tools/major_lookup.py            # 列出所有组

它只做一件事：把「专业」翻译成「硬件门槛 + 原因 + 坑 + 依据」。
不出机型、不排榜——那是 data/prices.json 的决策引擎的活，两边还没接。

刻意不做的事：
- 不猜价格。给了预算也只提示「预算与门槛可能冲突」，不编一台机器出来。
- 不认的专业直接说认不出，并给出最接近的候选。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "majors.json"

GPU_WORD = {
    "none": "不要独显（核显足够）",
    "either": "核显或独显都行",
    "discrete": "必须是独立显卡",
    "certified": "需认证显卡 + 认证驱动（不看显存大小）",
}


def load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def mlist(g):
    """把组的 majors 归一成 [(name, level), ...]。字符串默认本科。"""
    out = []
    for item in g.get("majors") or []:
        if isinstance(item, str):
            out.append((item, "本"))
        else:
            out.append((item.get("name"), item.get("level", "本")))
    return out


def find_in(d, name):
    """在全部组里找专业名，返回 [(组, 层次), ...]。本科排前面。"""
    hits = []
    for g in d["groups"]:
        for n, lv in mlist(g):
            if n == name:
                hits.append((g, lv))
    hits.sort(key=lambda x: 0 if x[1] == "本" else 1)
    return hits


def resolve(d, name):
    """专业名 → (专业, 层次, 组, 命中方式)。支持别名与包含匹配。"""
    name = name.strip()
    if not name:
        return None, None, None, "empty"

    alias = d.get("majorAliases", {})
    low = name.lower()

    # 1) 正式名精确命中（本科优先）
    hits = find_in(d, name)
    if hits:
        g, lv = hits[0]
        return name, lv, g, "exact"

    # 2) 别名键命中（大小写不敏感）
    for k, v in alias.items():
        if k.lower() == low:
            hits = find_in(d, v)
            if hits:
                g, lv = hits[0]
                return v, lv, g, "alias"

    # 3) 包含匹配：说「我是学建筑」也能认；优先匹配更长的专业名
    best = None
    for g in d["groups"]:
        for n, lv in mlist(g):
            if n and (n in name or name in n):
                if best is None or len(n) > len(best[0]):
                    best = (n, lv, g)
    if best:
        return best[0], best[1], best[2], "fuzzy"

    # 4) 认不出，给候选
    cands = [m for m in alias if low in m.lower() or m.lower() in low]
    return None, None, None, cands or "unknown"


def dwidth(s):
    """显示宽度：中文与全角符号算两格。"""
    return sum(2 if ord(c) > 127 else 1 for c in s)


def bar(label, text, width=11):
    pad = " " * max(2, width - dwidth(label))
    print("  %s%s%s" % (label, pad, text))


def show(d, major, lv, g, budget):
    print("")
    print("专业  %s（%s）" % (major, "本科" if lv == "本" else "专科"))
    print("分组  %s — %s" % (g["name"], g["tagline"]))
    print("")

    print("软件栈")
    print("  " + (g.get("stackText") or "—"))
    print("")

    req = g["req"]
    print("硬件门槛")
    ram = "最低 %sG / 建议 %sG" % (req["ramMin"], req["ramRec"])
    if req["ramMin"] == req["ramRec"]:
        ram = "%sG" % req["ramRec"]
    bar("内存", ram)
    bar("显卡", GPU_WORD.get(req["gpu"], req["gpu"]))
    if req.get("vramMin"):
        bar("显存", "最低 %sG / 建议 %sG" % (req["vramMin"], req["vramRec"]))
    bar("存储", "%sG 起" % req["storageMinGB"])
    bar("CPU", req.get("cpu") or "—")
    print("")

    print("该往哪个侧重选")
    mode_cn = {"value": "省钱优先", "perf": "性能优先",
               "future": "能加装优先", "carry": "要带出门"}
    cats = "、".join({"thin": "轻薄本", "gaming": "游戏本",
                     "desktop": "台式整机", "upgrade": "升级配件",
                     "phone": "手机"}.get(c, c) for c in g.get("preferCats", []))
    print("  %s%s优先看 %s" % (mode_cn.get(g["mode"], g["mode"]),
                              "，" if cats else "", cats))
    print("")

    gates = g.get("hardGates") or []
    if gates:
        print("硬门槛（不满足就不该买）")
        for h in gates:
            print("  - %s ≥ %s：%s" % (h["dim"], h["min"], h["why"]))
        print("")

    print("最容易踩的坑")
    for line in _wrap(g.get("trap", ""), 46):
        print("  " + line)
    print("")

    fus = g.get("followups") or []
    if fus:
        print("还要问一句")
        for q in fus:
            print("  - " + q)
        print("")

    # 依据
    ids = []
    if req.get("derivedFrom"):
        ids += [d["software"][i]["source"] for i in range(len(d["software"]))
                if d["software"][i]["id"] in req["derivedFrom"]
                and d["software"][i].get("source")]
    for sid in g.get("evidenceSources") or []:
        ids.append(sid)
    ids = list(dict.fromkeys(ids))
    src = {s["id"]: s for s in d["sources"]}
    if ids:
        print("依据（%s）" % ("官方系统要求" if g["evidence"] == "official" else "经验判断"))
        for sid in ids:
            s = src.get(sid)
            if s:
                print("  [%s] %s（%s）" % (s["publisher"], s["title"][:38], s["date"]))
        print("")

    if budget:
        print("预算提示")
        print("  你说的预算：%s 元" % budget)
        heavy = req["ramRec"] >= 32 and req["gpu"] in ("discrete", "certified")
        if heavy:
            print("  「32G 内存 + 独立显卡」在笔记本里是两道要同时满足的门槛，")
            print("  同一预算下通常只能二选一：给了独显往往是 16G 内存，给了 32G 往往只有核显。")
            print("  先确认课程真正卡住你的是哪一条，再让预算去补另一条。")
            print("  还有一件物理事实：笔记本的显卡换不了，「先买够内存、以后加显卡」不成立。")
        else:
            print("  这一组门槛不高，预算该花在屏幕、续航和轻便上，不在算力上。")
            print("  多出来的钱买 32G 内存和更好的屏，比买独显有用。")
        print("  （具体机型要等价格库接进来，这里不编。）")


def _wrap(text, n):
    """按显示宽度折行（中文算两格），并且不把英文/数字词拆开。"""
    # 先切成「不可断的块」：连续的 ASCII 字符算一块，中文逐字
    tokens, buf = [], ""
    for ch in text:
        if ord(ch) > 127:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        else:
            buf += ch
    if buf:
        tokens.append(buf)

    out, line, w = [], "", 0
    for t in tokens:
        tw = dwidth(t)
        if w + tw > n and line:
            out.append(line)
            line, w = "", 0
        line += t
        w += tw
    if line:
        out.append(line)
    return out


def main():
    d = load()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    budget = None
    for i, a in enumerate(sys.argv[1:]):
        if a == "--budget" and i + 2 <= len(sys.argv) - 1:
            try:
                budget = int(sys.argv[i + 2])
            except ValueError:
                pass

    if not args:
        total = sum(len(mlist(g)) for g in d["groups"])
        nb = sum(1 for g in d["groups"] for _, lv in mlist(g) if lv == "本")
        print("共 %d 个组 / %d 个专业（本科 %d / 专科 %d）。用法：python tools/major_lookup.py 专业名 [--budget 8000]"
              % (len(d["groups"]), total, nb, total - nb))
        print("")
        for g in d["groups"]:
            names = [n for n, _ in mlist(g)]
            print("  %-10s %-4d %s" % (g["name"], len(names), "、".join(names[:4]) + " 等"))
        print("")
        print("  未覆盖：Mac 全系、平板、主机、二手。")
        return 0

    major, lv, g, how = resolve(d, args[0])
    if not g:
        print("认不出「%s」。" % args[0])
        if isinstance(how, list) and how:
            print("你是说：%s ？" % " / ".join(how))
        else:
            total = sum(len(mlist(x)) for x in d["groups"])
            print("目前覆盖 %d 个专业，先跑 python tools/major_lookup.py 看清单。" % total)
        return 1

    if how == "alias":
        print("（按别名匹配到「%s」）" % major)
    elif how == "fuzzy":
        print("（按包含关系匹配到「%s」，如果不是请用全名）" % major)

    show(d, major, lv, g, budget)

    # 同名另一层次也收录时，提示一句，避免看错学制
    others = [x for x in find_in(d, major) if x[1] != lv]
    if others:
        og, olv = others[0]
        print("")
        print("注：同名专业另有%s版，归属「%s」，门槛%s。"
              % ("专科" if olv == "专" else "本科", og["name"],
                 "相同" if og["id"] == g["id"] else "不同"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
