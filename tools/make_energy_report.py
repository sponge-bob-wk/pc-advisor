#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""make_energy_report.py —— 生成能效数据看板 HTML"""
import json
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "能效数据看板.html"


def load(n):
    return json.loads((ROOT / "data" / n).read_text(encoding="utf-8"))


def main():
    led = load("efficiency_ledger.json")
    rep = load("efficiency_match_report.json")
    specs = load("specs_verified.json")
    prices = load("prices.json")
    nd = load("efficiency_nodata.json")

    recs = led["records"]
    s = led["summary"]
    total = s["uniqueRecords"]
    lv = s["levelDistribution"]

    # 按生产者分组
    by_prod = defaultdict(Counter)
    for r in recs:
        by_prod[r.get("producer") or "未知"][r["level"]] += 1

    # 已核实的机型
    verified = [m for m in specs["records"] if isinstance(m.get("efficiency"), int)]

    # 各文件查询概览
    files = led["files"]

    def pct(a, b):
        return f"{a/b*100:.1f}%" if b else "-"

    # ---- 图表数据 ----
    lv_rows = ""
    for k in (1, 2, 3):
        n = lv.get(str(k), 0)
        w = n / total * 100 if total else 0
        color = ["#c0392b", "#e67e22", "#7f8c8d"][k - 1]
        lv_rows += f"""<div class="bar-row">
          <span class="bar-label">{k} 级</span>
          <div class="bar-track"><div class="bar-fill" style="width:{w:.1f}%;background:{color}"></div></div>
          <span class="bar-num">{n} 条 · {w:.1f}%</span>
        </div>"""

    prod_rows = ""
    for p, c in sorted(by_prod.items(), key=lambda x: -sum(x[1].values())):
        tot = sum(c.values())
        one = c.get(1, 0)
        prod_rows += f"""<tr><td>{p}</td><td class="num">{tot}</td>
          <td class="num good">{one}</td><td class="num">{c.get(2,0)}</td>
          <td class="num">{c.get(3,0)}</td><td class="num">{one/tot*100:.0f}%</td></tr>"""

    ver_rows = ""
    for m in verified:
        ev = m.get("efficiencyEvidence") or {}
        fil = ev.get("filings") or []
        regs = "<br>".join(
            f'<span class="mono">{f.get("regNo","")}</span> <span class="dim">{f.get("date","")}</span>'
            for f in fil[:2])
        ver_rows += f"""<tr>
          <td>{m.get("modelHint","")}</td>
          <td class="mono">{ev.get("energyModel") or "-"}</td>
          <td class="center"><span class="tag tag-ok">{m["efficiency"]} 级</span></td>
          <td class="small">{regs}</td></tr>"""

    file_rows = ""
    for f in files:
        st = "查无数据" if f.get("noData") else ("完整" if f.get("complete") else "部分")
        cls = "warn" if f.get("noData") else ("ok" if f.get("complete") else "")
        file_rows += f"""<tr><td class="mono small">{f['file']}</td>
          <td>{f.get('queryKeyword') or f.get('producerKeyword') or '-'}</td>
          <td class="num">{f.get('parsed',0)}</td>
          <td class="num">{f.get('totalOnSite') if f.get('totalOnSite') is not None else '-'}</td>
          <td class="center"><span class="{cls}">{st}</span></td></tr>"""

    nd_rows = "".join(
        f"""<tr><td class="mono">{x['query']}</td><td>{x['result']}</td>
        <td class="small">{x['why']}</td></tr>"""
        for x in nd.get("noData", []))

    # prices 覆盖
    pc = [o for o in prices["offers"] if o.get("cat") in ("thin", "gaming", "desktop")]
    pc_have = [o for o in pc if isinstance(o.get("efficiency"), int)]

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>能效数据看板 · 2026-11-09</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
background:#f5f6f8;color:#1a1a1a;line-height:1.65;padding:28px 20px 60px}}
.wrap{{max-width:1100px;margin:0 auto}}
h1{{font-size:25px;font-weight:700;letter-spacing:-.4px}}
.sub{{color:#6b7280;font-size:13px;margin-top:6px}}
.card{{background:#fff;border-radius:12px;padding:22px 24px;margin-top:18px;
box-shadow:0 1px 3px rgba(0,0,0,.06),0 8px 24px -12px rgba(0,0,0,.1)}}
h2{{font-size:15px;font-weight:600;color:#374151;margin-bottom:16px;
padding-bottom:10px;border-bottom:1px solid #eef0f3;display:flex;align-items:center;gap:8px}}
h2::before{{content:"";width:3px;height:14px;background:#c0392b;border-radius:2px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.kpi{{background:#fafbfc;border:1px solid #eef0f3;border-radius:10px;padding:14px 16px}}
.kpi .v{{font-size:26px;font-weight:700;letter-spacing:-.5px;font-variant-numeric:tabular-nums}}
.kpi .l{{font-size:11.5px;color:#6b7280;margin-top:3px}}
.kpi.hi .v{{color:#c0392b}}
.kpi.ok .v{{color:#1e8449}}
.bar-row{{display:flex;align-items:center;gap:12px;margin:9px 0}}
.bar-label{{width:42px;font-size:12.5px;color:#4b5563;flex:none}}
.bar-track{{flex:1;height:20px;background:#f1f3f5;border-radius:5px;overflow:hidden}}
.bar-fill{{height:100%;border-radius:5px;transition:width .5s}}
.bar-num{{width:120px;font-size:12px;color:#6b7280;text-align:right;flex:none;
font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;font-size:11.5px;font-weight:600;color:#6b7280;
padding:7px 9px;border-bottom:1.5px solid #e5e7eb;white-space:nowrap}}
td{{padding:8px 9px;border-bottom:1px solid #f3f4f6;vertical-align:top}}
tr:last-child td{{border-bottom:none}}
tbody tr:hover{{background:#fafbfc}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.center{{text-align:center}}
.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}}
.small{{font-size:12px}}
.dim{{color:#9ca3af}}
.good{{color:#1e8449;font-weight:600}}
.warn{{color:#b45309;font-weight:600}}
.ok{{color:#1e8449}}
.tag{{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11.5px;font-weight:600}}
.tag-ok{{background:#e8f5e9;color:#1e8449}}
.note{{background:#fffbeb;border-left:3px solid #f59e0b;padding:11px 15px;
border-radius:0 7px 7px 0;font-size:12.5px;color:#78350f;margin-top:14px;line-height:1.7}}
.note b{{color:#92400e}}
.method{{font-size:12.5px;color:#4b5563;line-height:1.9}}
.method code{{background:#f1f3f5;padding:1px 6px;border-radius:4px;
font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:#c0392b}}
</style></head><body><div class="wrap">

<h1>能效数据看板</h1>
<div class="sub">来源：中国能效标识网 产品备案查询 · 采集日 2026-11-09 · 共 {total} 条备案记录</div>

<div class="card">
  <h2>总览</h2>
  <div class="kpis">
    <div class="kpi"><div class="v">{total}</div><div class="l">备案记录（去重）</div></div>
    <div class="kpi ok"><div class="v">{lv.get('1',0)}</div><div class="l">1 级能效 · {pct(lv.get('1',0), total)}</div></div>
    <div class="kpi"><div class="v">{len(verified)}</div><div class="l">已回填机型</div></div>
    <div class="kpi"><div class="v">{len(nd.get('noData',[]))}</div><div class="l">查无数据（否定结论）</div></div>
    <div class="kpi"><div class="v">{len(pc_have)}/{len(pc)}</div><div class="l">价格主档能效覆盖</div></div>
  </div>
  <div class="note">
    <b>关键判断</b>：2025 版能效标准下，笔记本拿 1 级是常态而不是例外（{lv.get('1',0)}/{total} = {pct(lv.get('1',0), total)}）。
    这说明国补的「一级能效前置」门槛不会大面积卡掉候选机型。<br>
    但要注意：<b>台式机/一体机会掉级</b>——`小新台式电脑–14IRR` 就是 2 级。国补对这类机型要单独核对。
  </div>
</div>

<div class="card">
  <h2>等级分布</h2>
  {lv_rows}
</div>

<div class="card">
  <h2>按生产者</h2>
  <table><thead><tr><th>生产者</th><th class="num">备案数</th>
  <th class="num">1 级</th><th class="num">2 级</th><th class="num">3 级</th>
  <th class="num">1 级占比</th></tr></thead>
  <tbody>{prod_rows}</tbody></table>
</div>

<div class="card">
  <h2>已核实并回填的机型（{len(verified)} 台）</h2>
  <table><thead><tr><th>机型</th><th>能效网规格型号</th><th class="center">等级</th>
  <th>备案号 / 日期</th></tr></thead>
  <tbody>{ver_rows}</tbody></table>
  <div class="note">
    <b>这 7 台的共同点</b>：都是先拿到「能效网规格型号」才查到的。反过来，用营销名（天选、小新 Air）去查一律「暂无数据」。
  </div>
</div>

<div class="card">
  <h2>查询记录</h2>
  <table><thead><tr><th>原始文件</th><th>查询词</th><th class="num">解析条数</th>
  <th class="num">页面总数</th><th class="center">状态</th></tr></thead>
  <tbody>{file_rows}</tbody></table>
</div>

<div class="card">
  <h2>查无数据的型号（否定结论，不必重查）</h2>
  <table><thead><tr><th>查询词</th><th>结果</th><th>原因</th></tr></thead>
  <tbody>{nd_rows}</tbody></table>
</div>

<div class="card">
  <h2>采集方法</h2>
  <div class="method">
    ① 网址 <code>energylabel.com.cn</code> → 产品备案查询<br>
    ② 产品类型必须先限定 <code>办公电子 → 微型计算机 2025版</code>，否则混入显示器与服务器<br>
    ③ 输入「能效网规格型号」（不是中文营销名）→ 搜索<br>
    ④ 页面表格读不到 DOM，改用 <code>Ctrl+A / Ctrl+C</code> 复制表格 → 读系统剪贴板<br>
    ⑤ 复制前必须先写哨兵值，否则会把上一次的内容当成这次的<br>
    ⑥ 解析时注意两套备案号格式：2025 版带 <code>CEL</code>，2012 版不带
  </div>
</div>

</div></body></html>"""
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT}  ({len(html)} bytes)")


if __name__ == "__main__":
    main()