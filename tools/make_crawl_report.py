#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把采集到的 jsonl 渲染成可看的看板。

用法：
    python tools/make_crawl_report.py
输出：
    E:/电脑涨价分析/采集数据看板.html  （双击打开或侧边预览）

为什么要有这个：jsonl 是给机器读的，人看不了。每次采集完跑一次，
就能一眼看出——价格对不对、哪条缺截图、哪个型号没有第二家店。
"""

import json
import html
from pathlib import Path
from urllib.parse import quote
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
CRAWL = ROOT / "data" / "crawl"
OUT = ROOT / "采集数据看板.html"

SHOP_CN = {"self": "自营", "flagship": "官方旗舰", "authorized": "授权专卖", "thirdparty": "第三方"}
SHOP_CLS = {"self": "b-self", "flagship": "b-flagship", "authorized": "b-auth", "thirdparty": "b-third"}

CSS = """
*{box-sizing:border-box}
body{margin:0;padding:24px;background:#f7f7f5;color:#1f1e1c;
 font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:13px;line-height:1.6}
h1{font-size:17px;font-weight:500;margin:0 0 4px}
.sub{color:#6b6963;font-size:12px;margin-bottom:18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:10px;margin-bottom:16px}
.card{background:#fff;border:1px solid #e5e3dd;border-radius:10px;padding:12px 14px}
.cl{color:#6b6963;font-size:12px}
.cv{font-size:22px;font-weight:500;margin-top:2px}
.cs{color:#8a8780;font-size:11px;min-height:15px}
.bar{margin:0 0 16px}
.b{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;margin-right:6px;border:1px solid}
.b-self{background:#e8f1fb;border-color:#b7d3f0;color:#185fa5}
.b-flagship{background:#eeeafd;border-color:#cfc7f2;color:#4a3fa8}
.b-auth{background:#e4f4ee;border-color:#b4e0d0;color:#0f6e56}
.b-third{background:#fbeeda;border-color:#f0d2a5;color:#8a5a0b}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e3dd;border-radius:10px;overflow:hidden}
th{text-align:left;font-weight:500;font-size:12px;color:#6b6963;background:#faf9f7;
 padding:9px 10px;border-bottom:1px solid #e5e3dd;position:sticky;top:0}
td{padding:9px 10px;border-bottom:1px solid #f0eee9;vertical-align:top}
tr:last-child td{border-bottom:0}
.mname{font-weight:500}
.msub{color:#8a8780;font-size:11px;margin-top:1px}
.cfg{font-size:12px;color:#3d3b37}
.num{text-align:right;white-space:nowrap}
.list{color:#a09d96;text-decoration:line-through;margin-right:4px}
.arrow{color:#c0bdb5;margin-right:4px}
.tag{font-size:11px;padding:1px 7px;border-radius:5px;border:1px solid}
.s-ok{background:#e9f6ee;border-color:#bce0c8;color:#2c6b3f}
.s-delta{background:#fdf4e3;border-color:#f0dcb4;color:#8a5a0b}
.s-bad{background:#fdeaea;border-color:#f2c4c4;color:#9d2b2b}
.shot img{width:64px;border-radius:5px;border:1px solid #e5e3dd;display:block}
.miss{color:#9d2b2b;font-size:11px}
h2{font-size:14px;font-weight:500;margin:26px 0 8px}
.note{color:#6b6963;font-size:12px;margin-top:8px}
"""


def load(p):
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def check(r):
    """返回 (状态, 说明)。ok=通过 / delta=待证 / bad=不平"""
    p = r.get("price") or {}
    L, N, S, F = p.get("list"), p.get("net"), p.get("subsidy"), p.get("final")
    if L is None or N is None or F is None:
        return "bad", "缺价格环节"
    if abs(N - F) <= 1:
        return "ok", "无补贴"
    if S is not None:
        if abs(N - S - F) <= 1:
            return "ok", "算式成立"
        return "bad", "补贴后差 %.2f" % (N - S - F)
    d = N - F
    if abs(d - N * 0.15) <= 2:
        return "delta", "差额 %.2f 约 15%%，国补未标金额" % d
    return "bad", "差额 %.2f 无法解释" % d


def money(v):
    return "—" if v is None else format(round(v, 2), "g")


def img_tag(src):
    if not src:
        return ""
    f = Path(src)
    if not f.exists():
        return '<span class="miss">缺图</span>'
    rel = "crawl/shots/" + quote(f.name)
    return '<a href="%s" target="_blank"><img src="%s" alt=""></a>' % (rel, rel)


def row(r):
    p = r.get("price") or {}
    c = r.get("config") or {}
    st, note = check(r)
    cfg = " / ".join(str(x) for x in [c.get("cpu"), c.get("ramGB"), c.get("storageGB"), c.get("gpu")] if x)
    ev = r.get("evidence") or {}
    badge = '<span class="b %s">%s</span>' % (
        SHOP_CLS.get(r["shopType"], ""), SHOP_CN.get(r["shopType"], r["shopType"]))
    stc = {"ok": "s-ok", "delta": "s-delta", "bad": "s-bad"}[st]
    stn = {"ok": "通过", "delta": "待证", "bad": "不平"}[st]
    return ("<tr>"
            '<td><div class="mname">%s</div><div class="msub">%s · %s</div></td>'
            "<td>%s<div class=\"msub\">%s</div></td>"
            '<td class="cfg">%s</td>'
            '<td class="num"><span class="list">%s</span><span class="arrow">→</span><b>%s</b></td>'
            '<td class="num">%s</td>'
            '<td><span class="tag %s">%s</span><div class="msub">%s</div></td>'
            '<td class="shot">%s</td>'
            "</tr>") % (
        html.escape(r.get("modelHint") or ""), html.escape(r.get("brand") or ""), html.escape(r.get("series") or ""),
        badge, html.escape((r.get("shopName") or "")[:18]),
        html.escape(cfg),
        money(p.get("list")), money(p.get("final")), money(p.get("subsidy")),
        stc, stn, html.escape(note),
        img_tag(ev.get("screenshot")),
    )


def main():
    rows = load(CRAWL / "2026-11-07_jd.jsonl")
    pend = load(CRAWL / "2026-11-07_jd_pending.jsonl")
    specs = load(CRAWL / "2026-11-07_specs.jsonl")

    okn = sum(1 for r in rows if check(r)[0] == "ok")
    dln = sum(1 for r in rows if check(r)[0] == "delta")
    badn = sum(1 for r in rows if check(r)[0] == "bad")

    brands = defaultdict(int)
    shops = defaultdict(int)
    for r in rows:
        brands[r["brand"]] += 1
        shops[r["shopType"]] += 1

    covered = set()
    for s in specs:
        covered |= set(s.get("covers") or [])

    by = defaultdict(set)
    for r in rows:
        by[(r["brand"], r["modelHint"])].add(r["shopType"])
    multi = sum(1 for v in by.values() if len(v) > 1)

    have_shot = sum(1 for r in rows if Path((r.get("evidence") or {}).get("screenshot") or "x").exists())

    stat = [
        ("价格记录", len(rows), "主表"),
        ("型号", len(by), "P0 目标 46"),
        ("品牌", len(brands), "目标 20"),
        ("校验", okn, "待证 %d · 不平 %d" % (dln, badn)),
        ("有官网规格", len(covered), "覆盖 %d%%" % (len(covered) * 100 // max(1, len(rows)))),
        ("多店对照", multi, "型号级"),
        ("截图", have_shot, "张"),
    ]
    cards = "".join(
        '<div class="card"><div class="cl">%s</div><div class="cv">%s</div><div class="cs">%s</div></div>' % (a, b, c)
        for a, b, c in stat)

    shopbar = "".join(
        '<span class="b %s">%s %d</span>' % (SHOP_CLS.get(k, ""), SHOP_CN.get(k, k), v)
        for k, v in sorted(shops.items(), key=lambda x: -x[1]))

    body = "".join(row(r) for r in rows)

    pend_rows = ""
    for r in pend:
        p = r.get("price") or {}
        pend_rows += ("<tr><td>%s</td><td>%s</td><td class=\"num\">%s</td><td class=\"msub\">%s</td></tr>" % (
            html.escape(r.get("modelHint") or ""),
            SHOP_CN.get(r.get("shopType"), r.get("shopType")),
            money(p.get("final")),
            html.escape((r.get("note") or r.get("price", {}).get("basisNote") or "")[:56])))

    doc = []
    doc.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    doc.append("<title>采集数据看板</title><style>%s</style></head><body>" % CSS)
    doc.append("<h1>采集数据看板</h1>")
    doc.append('<div class="sub">京东 · 采集时间 2026-11-07 23:50 · 共 %d 条记录 · 源文件 data/crawl/2026-11-07_jd.jsonl</div>' % len(rows))
    doc.append('<div class="cards">%s</div>' % cards)
    doc.append('<div class="bar">%s</div>' % shopbar)
    thead = ('<table><thead><tr>'
             '<th style="width:21%">型号</th><th style="width:19%">店铺</th><th style="width:20%">配置</th>'
             '<th style="width:12%;text-align:right">标价 → 到手</th>'
             '<th style="width:8%;text-align:right">补贴</th>'
             '<th style="width:13%">校验</th><th>截图</th></tr></thead><tbody>')
    doc.append(thead + body + "</tbody></table>")
    doc.append('<div class="note">「待证」= 差额恰为 15%，页面未标国补金额，按规定不推算、留第二轮补证；'
               "「不平」= 差额无法用已知优惠解释，需回查截图。</div>")
    doc.append("<h2>单价记录（%d 条，已隔离，不进主表）</h2>" % len(pend))
    doc.append("<table><thead><tr><th>型号</th><th>店铺</th><th style=\"text-align:right\">单价</th>"
               "<th>原因</th></tr></thead><tbody>%s</tbody></table>" % pend_rows)
    doc.append("</body></html>")

    OUT.write_text("\n".join(doc), encoding="utf-8")
    print("已生成 %s（%.1f KB，%d 条记录，截图 %d 张）"
          % (OUT.name, OUT.stat().st_size / 1024, len(rows), have_shot))


if __name__ == "__main__":
    main()
