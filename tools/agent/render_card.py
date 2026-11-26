# -*- coding: utf-8 -*-
"""render_card.py —— 把 answer_card 的结构化结果渲染成 markdown

## 为什么要单独一个模块

`answer_card()` 返回的是结构化字典（headline / rows / gaps / candidates…），
这是给前端渲染卡片用的。但 open-webui 走的是 OpenAI 协议，**只吃纯文本**。

所以需要一层转换：结构化 → markdown。

## 转换原则（按用户要求）

用户说：「你想表达很多严谨性，但是没必要展示给用户看」

所以这里：
  ✅ 展示：机型、价格、配置、短板、其他候选、超预算警告
  ❌ 不展示：dataAsOf、poolHint、审计明细、数据来源标注

## 为什么不用 JSON 塞进 markdown

试过 `{"headline":..., "rows":[...]}` 直接输出，问题：
  · open-webui 的 markdown 渲染器会把 JSON 当代码块，界面很难看
  · 用户看到的是原始 JSON，不是人话

markdown 表格 + 加粗才是「结构化但不难看」的折中。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 🔴 候选机型的「详情页」地址 —— 指向原界面（7860）。
#
#    为什么链接指向 7860 而不是 open-webui：
#      open-webui 没有「按 id 打开某台机型」的能力，它只是聊天窗口。
#      而 7860 的 app.js 支持 ?id=<机型id>（2026-11-26 加的）。
#
#    可用环境变量覆盖，方便换成局域网 IP 或公网域名：
#      公网：set DETAIL_BASE=https://xxx.trycloudflare.com
DETAIL_BASE = os.environ.get("DETAIL_BASE", "http://127.0.0.1:7860").rstrip("/")


def render(card, extra_note=""):
    """把 answer_card 的返回值渲染成 markdown。

    card: agent.answer_card() 的返回值（dict）
    extra_note: 额外的补充说明（如价格口径）
    """
    if not isinstance(card, dict):
        return str(card or "")

    out = []

    # ── 标题行（最重要的一句话）──
    headline = (card.get("headline") or "").strip()
    if headline:
        out.append("### " + headline)
        out.append("")

    # ── 参数表（机型/价格/配置/拆解）──
    rows = card.get("rows") or []
    if rows:
        out.append("| 项 | 内容 |")
        out.append("|---|---|")
        for r in rows:
            k = (r.get("k") or "").strip()
            v = (r.get("v") or "").strip()
            if not k or not v:
                continue
            # 表格里的竖线要转义，否则会破坏列结构
            v = v.replace("|", "\\|")
            out.append("| **%s** | %s |" % (k, v))
        out.append("")

    # ── 短板/差距（这是本系统的核心价值：主动说缺点）──
    gaps = card.get("gaps") or []
    if gaps:
        out.append("**需要注意**：")
        for g in gaps:
            txt = (g.get("text") or "").strip()
            if not txt:
                continue
            lvl = g.get("level") or "soft"
            mark = "⚠️" if lvl == "hard" else "·"
            out.append("- %s %s" % (mark, txt))
        out.append("")

    # ── 额外说明（价格口径等）──
    if extra_note:
        out.append(extra_note)
        out.append("")

    # ── 其他候选（可点击跳到那台的信息）──
    #
    # 🔴 2026-11-26（用户要求）：
    #    「推荐别的我可以直接点卡片跳到另一个推荐的机型的信息」
    #
    #    做法：把候选渲染成 markdown 链接，指向 7860 的 `?id=<机型id>`。
    #    7860 的 app.js 已支持 ?id=（见那里的注释）：
    #      ?id=add-xingbookproair14  →  S.focus=id; send('这台怎么样')
    #
    #    ⚠️ 为什么用 id 不用名字：
    #      实测「ThinkBook 14+ 怎么样」识别失败（名字里的 + 和括号
    #      干扰了机型名匹配），而 id 是唯一键，不会撞。
    #
    #    ⚠️ 链接要指向 7860（原界面），不是 open-webui —
    #      因为 open-webui 没有「按 id 打开某台机型」的能力。
    cands = card.get("candidates") or []
    if cands:
        items = []
        for c in cands[:4]:
            nm = (c.get("name") or "").strip()
            pr = c.get("price")
            cid = (c.get("id") or "").strip()
            if not nm:
                continue
            label = "%s（¥%s）" % (nm, _money(pr)) if pr else nm
            if cid:
                items.append("[%s](%s/?id=%s)" % (label, DETAIL_BASE, cid))
            else:
                items.append(label)
        if items:
            out.append("**其他可选**：" + "、".join(items))
            out.append("")

    # ── 警告（超预算这类必须留）──
    warns = card.get("warnings") or []
    if warns:
        for w in warns[:2]:
            wtxt = (w.get("text") if isinstance(w, dict) else str(w)) or ""
            if wtxt.strip():
                out.append("> ⚠️ " + wtxt.strip())
        out.append("")

    # ── blocks（有些分支会放额外段落）──
    for b in (card.get("blocks") or []):
        if isinstance(b, dict):
            t = (b.get("text") or "").strip()
            if t:
                out.append(t)
                out.append("")
        elif isinstance(b, str) and b.strip():
            out.append(b.strip())
            out.append("")

    # 去掉尾部多余空行
    while out and not out[-1].strip():
        out.pop()

    return "\n".join(out)


def _money(v):
    """价格格式化：整数不带小数，有小数保留。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f - round(f)) < 0.01:
        return str(int(round(f)))
    return ("%.2f" % f).rstrip("0").rstrip(".")


if __name__ == "__main__":
    # 自测
    import json
    demo = {
        "headline": "按你给的条件，排第 1 的是 联想小新 16，到手 ¥5192",
        "rows": [
            {"k": "机型", "v": "联想小新 16"},
            {"k": "到手价", "v": "¥5192"},
            {"k": "配置", "v": "R7-8845H · 16G + 1T · 16 寸 2560×1600"},
        ],
        "gaps": [{"level": "soft",
                  "text": "屏幕素质在同价位不算突出，做设计调色够用但不精细"}],
        "candidates": [{"name": "星Book Pro Air 14", "price": 5099.15},
                       {"name": "ThinkBook 14+", "price": 5399}],
        "warnings": [],
        "dataAsOf": "2026-11-02",
        "poolHint": "这一轮的排序池是 239 台",
    }
    print(render(demo, "（5192 是到手价，已含国补）"))
