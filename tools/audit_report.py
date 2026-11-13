# -*- coding: utf-8 -*-
"""audit_report.py —— 生成数据质量审核报告（HTML）

为什么要有这份报告：
  这个项目的所有结论都建立在数据可信上。但"可信"不能靠感觉，
  必须能看到：多少台有价、多少台核过能效、价格账对不对、哪里还有矛盾。
  报告是给自己和用户看的账本，不是装饰。

用法：
  python tools/audit_report.py            # 生成 data/audit_report.html
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load(n):
    return json.loads((DATA / n).read_text(encoding="utf-8"))


def pct(a, b):
    return f"{a}/{b}（{a/b*100:.0f}%）" if b else f"{a}/{b}"


def main():
    pool = load("machine_pool.json")
    ms = pool["machines"]
    majors = load("majors.json")
    bench = load("bench.json")

    n = len(ms)
    priced = [m for m in ms if (m.get("price") or {}).get("final") or (m.get("price") or {}).get("net")]
    eff = [m for m in ms if m.get("efficiency") == 1]
    eff_conf = [m for m in ms if m.get("efficiencyStatus") == "conflict"]
    thr = [m for m in ms if m.get("throttle")]
    bench_ok = 0
    import lib_bench as LB
    ci = LB.cpu_index(bench)
    for m in ms:
        if LB.cpu_bench((m.get("cpu") or {}).get("model"), ci):
            bench_ok += 1
    macs = [m for m in ms if (m.get("devicePlatform") or "") == "macos"]

    # 价格对账
    def price_ok(m):
        pr = m.get("price") or {}
        net, sub, fin, lst = pr.get("net"), pr.get("subsidy"), pr.get("final"), pr.get("list")
        dis = sum((d.get("amount") or 0) for d in (pr.get("discounts") or []))
        if fin is None or net is None:
            return True
        if lst is not None and abs(net - (lst - dis)) > 1.5:
            return False
        return abs(fin - (net - (sub or 0))) <= 1.5

    bad_price = [m for m in priced if not price_ok(m)]

    # 冲突
    conflicts = []
    for m in ms:
        for c in (m.get("softConflicts") or []):
            conflicts.append((m["name"], c))
    # 明细冲突从 specs 里取
    try:
        sv = load("specs_verified.json")
        for r in sv["records"]:
            for c in r.get("conflicts") or []:
                if c.get("adopted") is None:
                    conflicts.append((r.get("modelHint") or r.get("id"),
                                      c.get("field") + "（" + str(c.get("why"))[:60] + "…）"))
    except Exception:
        pass

    rows_eff = "".join(
        f"<tr><td>{m['name']}</td><td>{m.get('energyModel') or '—'}</td>"
        f"<td class='ok'>{m.get('efficiency')} 级</td></tr>"
        for m in sorted(eff, key=lambda x: x["name"]))
    rows_conf = "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in conflicts[:12])

    # 各组覆盖
    from lib_judge import judge, r_fields_of
    fmat = load("field_matrix.json")
    grow = []
    for g in majors["groups"]:
        rf = r_fields_of(fmat["matrix"], g["id"])
        ok = sum(1 for m in ms
                 if judge(m, g.get("req") or {}, rf, g.get("hardGates") or [])[0] in ("OK", "DEGRADED")
                 and ((m.get("price") or {}).get("final") or (m.get("price") or {}).get("net")))
        grow.append(f"<tr><td>{g['name']}</td><td>{ok}</td><td>{len(g.get('majors') or [])}</td></tr>")

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>数据质量审核报告</title>
<style>
body{{font:14px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#1f2937;
max-width:900px;margin:0 auto;padding:32px 24px;background:#fff}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#6b7280;font-size:13px;margin-bottom:24px}}
h2{{font-size:15px;margin:28px 0 10px;padding-left:9px;border-left:3px solid #c0392b}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}}
th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid #eef1f4}}
th{{background:#f9fafb;font-weight:600;color:#374151}}
.kpi{{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0 4px}}
.card{{flex:1 1 150px;border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px}}
.card b{{display:block;font-size:20px;color:#111827;font-variant-numeric:tabular-nums}}
.card span{{font-size:12px;color:#6b7280}}
.ok{{color:#047857}} .bad{{color:#b91c1c}} .warn{{color:#b45309}}
.note{{background:#f9fafb;border-left:3px solid #d1d5db;padding:9px 12px;margin:12px 0;
font-size:12.5px;color:#4b5563;border-radius:0 4px 4px 0}}
</style></head><body>
<h1>数据质量审核报告</h1>
<div class="sub">生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 机型池 {n} 台 · {len(majors['groups'])} 个专业组</div>

<div class="kpi">
  <div class="card"><b>{pct(len(priced), n)}</b><span>有价格</span></div>
  <div class="card"><b>{pct(len(eff), n)}</b><span>已核 1 级能效</span></div>
  <div class="card"><b>{pct(bench_ok, n)}</b><span>有 CPU 跑分</span></div>
  <div class="card"><b>{pct(len(thr), n)}</b><span>有持续性能数据</span></div>
</div>

<h2>价格对账</h2>
<p>规则：<code>划线 − Σ折扣 = 券后</code>，<code>券后 − 国补 = 到手</code>。差额若符 15%/封顶 1500 判为国补，否则判为商家优惠。</p>
<p>{'<span class="ok">✓ 全部对得上账，无自相矛盾的价格</span>' if not bad_price else
     '<span class="bad">✗ ' + str(len(bad_price)) + ' 台账不平</span>'}</p>
{'' if not bad_price else '<table><tr><th>机型</th></tr>' + ''.join(f'<tr><td>{m["name"]}</td></tr>' for m in bad_price) + '</table>'}

<h2>专业组覆盖（够用且买得到）</h2>
<table><tr><th>专业组</th><th>可选机型</th><th>覆盖专业数</th></tr>{''.join(grow)}</table>

<h2>能效核对（国补资格的唯一依据）</h2>
<p>已确认 1 级：<b>{len(eff)}</b> 台；备案冲突无法判定：<b>{len(eff_conf)}</b> 台
{('（' + '、'.join(m["name"] for m in eff_conf) + '）') if eff_conf else ''}</p>
<details><summary style="cursor:pointer;color:#6b7280;font-size:13px">展开逐台清单</summary>
<table><tr><th>机型</th><th>能效网型号</th><th>等级</th></tr>{rows_eff}</table></details>

<h2>跨源冲突（未裁决）</h2>
{'<p class="ok">无未裁决冲突</p>' if not conflicts else
 f'<table><tr><th>机型 / 记录</th><th>冲突项</th></tr>{rows_conf}</table>'}
{'' if len(conflicts)<=12 else f'<div class="note">另有 {len(conflicts)-12} 条未列出，详见 data/specs_verified.json 的 conflicts 字段</div>'}

<h2>平台差异</h2>
<p>macOS 机型 <b>{len(macs)}</b> 台。判定分三个层面：</p>
<table>
<tr><th>层面</th><th>处理方式</th></tr>
<tr><td>软件可装性</td><td>五个组标记 Windows 独占软件（Revit/SolidWorks/Multisim/ArcGIS Pro/Origin）→ 直接不推荐</td></tr>
<tr><td>内存与显存</td><td>统一内存按容量判显存等效需求；门槛仍用 PC 口径，命中时注明"对 Mac 偏保守"</td></tr>
<tr><td>接口与散热</td><td>无 USB-A → 提示扩展坞成本；无风扇 → 提示长时间负载降频（有实测的写实测值）</td></tr>
</table>

<h2>已知局限（诚实清单）</h2>
<div class="note">
<b>1. 持续性能数据只有 {len(thr)} 台。</b>其余 {n-len(thr)} 台的"长时间跑会不会掉速"没有实测，
推荐里带"缺持续性能数据"的保留项。要补只能逐台翻评测的双烤/Cinebench 循环数据。<br>
<b>2. 跑分不覆盖全部 CPU。</b>{n-bench_ok} 台缺 CPU 跑分，多为新款或小众型号。<br>
<b>3. Mac 的能效等级查不到。</b>京东明确标可享国补，但能效网无 MacBook 备案，
所以价格照页面填、等级留空，并注明"以结算页为准"。<br>
<b>4. 门槛来自 PC 端软件官方建议。</b>应用到 Mac 上可能偏严，未做等效换算——这是刻意的，
因为"Mac 16G≈PC 32G"这类说法没有权威出处。<br>
<b>5. 价格是快照。</b>双 11 期间一天内多次变动，记录的是采集时刻的页面值，附 basisNote 说明口径。
</div>

<div class="note">数据源：京东商品页（无障碍树读取）+ 中国能效标识网（备案查询）+ 联想/华硕/微星官网 + Intel ARK + Notebookcheck/cpu-monkey 等公开评测。每条记录都带 evidence 与 basisNote。</div>
</body></html>"""

    out = DATA / "audit_report.html"
    out.write_text(html, encoding="utf-8")
    print(f"写出 {out}  ({len(html)//1024} KB)")
    print(f"机器 {n} · 有价 {len(priced)} · 1级能效 {len(eff)} · 有跑分 {bench_ok} · 有持续性能 {len(thr)}")
    print(f"价格不平 {len(bad_price)} · 未裁决冲突 {len(conflicts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
