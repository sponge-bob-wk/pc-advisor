#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
make_pick_page.py —— 生成「按专业选电脑」网页

为什么要有网页版：

  pick.py 在命令行里是对的，但准大学生不会开终端。
  网页版把同一套判定逻辑搬到浏览器里，交互只有两步：选专业、填预算。

关键做法：判定结果在 Python 侧算好，前端只做筛选与展示。
  不在 JS 里重写一遍 judge() —— 那会变成第三份实现，早晚和 Python 版分叉
  （这个坑在 coverage.py 与 pick.py 之间已经踩过一次）。
  所以 Python 把「每个专业组 × 每台机器」的结论全算出来内嵌进 HTML，
  前端只是查表。代价是文件大一点，好处是永远只有一个真相。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_bench as LB  # noqa: E402
from lib_judge import (  # noqa: E402
    field_value, form_is_soldered, judge, r_fields_of,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

FIELD_WORD = {
    "cpu": "CPU 型号", "cores": "CPU 核心数", "ramGB": "内存容量",
    "ramType": "内存形态", "storageGB": "存储容量", "gpuModel": "显卡型号",
    "vramGB": "显存容量", "tgpW": "显卡功耗释放", "screenInch": "屏幕尺寸",
    "resolution": "分辨率", "refreshHz": "刷新率", "colorGamut": "色域",
    "brightnessNit": "屏幕亮度", "weightKg": "重量", "batteryWh": "电池容量",
    "ports": "接口", "efficiency": "能效等级", "throttle": "持续性能衰减",
    "modelHint（年份）": "型号年份", "modelHint": "型号命名",
}


def load(n):
    return json.loads((DATA / n).read_text(encoding="utf-8"))


def spec_line(m):
    bits = []
    cpu = m.get("cpu") if isinstance(m.get("cpu"), dict) else {}
    if cpu.get("model"):
        bits.append(str(cpu["model"]))
    ram = m.get("ram") if isinstance(m.get("ram"), dict) else {}
    if ram.get("gb"):
        bits.append(f"{ram['gb']}G")
    if m.get("storageGB"):
        g = m["storageGB"]
        bits.append(f"{g // 1024}T" if g >= 1024 else f"{g}G")
    gpu = m.get("gpu") if isinstance(m.get("gpu"), dict) else {}
    if gpu.get("model"):
        g = str(gpu["model"])
        if gpu.get("vramGB"):
            g += f" {gpu['vramGB']}G"
        bits.append(g)
    return " · ".join(bits) or "配置未采"


def _value_metric(gid, r_fields):
    """与 pick.py 的 value_metric 保持同一套口径（改一处要改两处）。"""
    if any(f in r_fields for f in ("gpuModel", "vramGB", "tgpW")):
        return "timeSpy"
    if "cores" in r_fields or gid == "code":
        return "cbMulti"
    return "cbSingle"


def _bench_obj(m):
    """机器的跑分对象，供前端展示。取不到就返回 None，绝不填估计值。"""
    cpu = (m.get("cpu") or {}).get("model")
    gpu = (m.get("gpu") or {}).get("model")
    cb = LB.cpu_bench(cpu)
    gb = LB.gpu_bench(gpu)
    out = {}
    if cb:
        r23 = (cb.get("bench") or {}).get("r23") or {}
        if r23.get("multi") or r23.get("single"):
            out["cbMulti"] = r23.get("multi")
            out["cbSingle"] = r23.get("single")
    if gb:
        ts = (gb.get("bench") or {}).get("timeSpyGraphics")
        if ts:
            out["timeSpy"] = ts
    return out or None


def machine_brief(m):
    """给前端用的精简机器对象。字段名短，减小体积。"""
    pr = m.get("price") or {}
    final = pr.get("final") or pr.get("net")
    risks = []
    if form_is_soldered(m) is True:
        risks.append("内存焊死")
    elif form_is_soldered(m) is None:
        risks.append("内存可否加装未核实")
    if m.get("confidence") == "unverified":
        risks.append("规格未经交叉核实")
    if m.get("efficiency") is None and m.get("efficiencyStatus") == "conflict":
        risks.append("能效备案冲突，国补存疑")
    elif m.get("efficiency") is None:
        risks.append("能效未查")
    if m.get("softConflicts"):
        names = "、".join(FIELD_WORD.get(f.split(".")[-1], f) for f in m["softConflicts"])
        risks.append(f"{names}官网标注有出入")
    return {
        "id": m["id"],
        "name": m["name"],
        "brand": m.get("brand"),
        "cat": m.get("cat"),
        "spec": spec_line(m),
        "price": final,
        "list": pr.get("list"),
        "net": pr.get("net"),
        "subsidy": pr.get("subsidy"),
        "shop": {"self": "京东自营", "flagship": "品牌旗舰店",
                 "authorized": "授权店", "thirdparty": "第三方店"}.get(m.get("shopType"), "店铺未记"),
        "captured": (m.get("priceCapturedAt") or "")[:10],
        "eff": m.get("efficiency"),
        # 跑分只放数值，前端自己决定怎么排版；没有出处的留 None
        "edu": ((m.get("eduPrice") or {}).get("final")),
        "bench": _bench_obj(m),
        "risks": risks,
        "url": m.get("url"),
    }


def main():
    pool = load("machine_pool.json")["machines"]
    fmat = load("field_matrix.json")
    majors = load("majors.json")
    rules = load("prices.json").get("subsidyRules") or {}

    # 机器清单去重（同一台机器在多个组里都会出现，只存一份）
    briefs = {}
    for m in pool:
        briefs[m["id"]] = machine_brief(m)

    groups_out = []
    for g in majors["groups"]:
        gid = g["id"]
        rf = r_fields_of(fmat["matrix"], gid)
        req = g.get("req") or {}
        gates = g.get("hardGates") or []
        verdicts = {}
        for m in pool:
            v, why, missing = judge(m, req, rf, gates)
            if v == "OK":
                verdicts[m["id"]] = {"v": "ok"}
            elif v == "DEGRADED":
                verdicts[m["id"]] = {"v": "deg", "w": why}
            elif v == "VIOLATE":
                verdicts[m["id"]] = {"v": "no", "w": why}
            else:
                # 键名必须叫 miss：前端 rows 用 {m: 机器对象, ...verdict} 展开，
                # 若这里也叫 m，缺的字段名数组会覆盖掉机器对象，渲染出 undefined
                verdicts[m["id"]] = {"v": "unk", "miss": [FIELD_WORD.get(x, x) for x in missing]}

        names = []
        for it in g.get("majors") or []:
            if isinstance(it, str):
                names.append(it)
            else:
                names.append(it.get("name"))

        groups_out.append({
            "id": gid,
            "name": g["name"],
            "tagline": g.get("tagline", ""),
            "req": {
                "ramMin": req.get("ramMin"), "ramRec": req.get("ramRec"),
                "vramMin": req.get("vramMin"), "vramRec": req.get("vramRec"),
                "gpu": req.get("gpu"), "storageMinGB": req.get("storageMinGB"),
                "cpu": req.get("cpu"), "rationale": req.get("rationale"),
                "basis": req.get("basis"),
            },
            "gates": gates,
            "mode": g.get("mode"),
            "preferCats": g.get("preferCats") or [],
            "trap": g.get("trap", ""),
            "followups": g.get("followups") or [],
            "majors": names,
            # 每元性能用哪个维度：前端在组内挑最划算那台时用
            "vm": _value_metric(gid, rf),
            "verdicts": verdicts,
        })

    # 别名表转成数组，前端做前缀/包含匹配
    aliases = [[k, v] for k, v in (majors.get("majorAliases") or {}).items()]

    payload = {
        "generatedAt": "2026-11-09",
        "machines": list(briefs.values()),
        "groups": groups_out,
        "aliases": aliases,
        "rules": rules.get("pc") or {},
        "stats": load("machine_pool.json")["stats"],
    }

    html = TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    out = ROOT / "按专业选电脑.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}  ({out.stat().st_size / 1024:.0f} KB)")
    print(f"  机型 {len(briefs)} 台 · 专业组 {len(groups_out)} 个 · 专业 {sum(len(g['majors']) for g in groups_out)} 个")
    return 0


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>按专业选电脑 · 准大学生</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
background:#f5f6f8;color:#1a1a1a;line-height:1.65;padding:24px 16px 80px}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:24px;font-weight:700;letter-spacing:-.4px}
.sub{color:#6b7280;font-size:13px;margin-top:5px}
.card{background:#fff;border-radius:12px;padding:20px 22px;margin-top:16px;
box-shadow:0 1px 3px rgba(0,0,0,.06),0 8px 24px -12px rgba(0,0,0,.1)}

/* 输入区 */
.picker{position:sticky;top:0;z-index:20;background:#fff;border-radius:12px;
padding:16px 18px;box-shadow:0 2px 10px rgba(0,0,0,.08);margin-top:16px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input[type=text],input[type=number]{font-size:15px;padding:11px 13px;border:1.5px solid #e5e7eb;
border-radius:9px;font-family:inherit;color:#1a1a1a;outline:none;transition:border .15s}
input[type=text]{flex:1;min-width:200px}
input[type=number]{width:130px}
input:focus{border-color:#c0392b}
.btn{font-size:14px;padding:11px 20px;border-radius:9px;border:none;background:#c0392b;
color:#fff;font-weight:600;cursor:pointer;font-family:inherit;transition:background .15s}
.btn:hover{background:#a93226}
.btn.ghost{background:#f1f3f5;color:#4b5563}
.btn.ghost:hover{background:#e5e7eb}

/* 候选下拉 */
.sug{position:absolute;left:18px;right:18px;top:100%;margin-top:4px;background:#fff;
border:1px solid #e5e7eb;border-radius:9px;box-shadow:0 8px 24px rgba(0,0,0,.12);
max-height:280px;overflow:auto;display:none;z-index:30}
.sug.on{display:block}
.sug div{padding:9px 14px;font-size:14px;cursor:pointer;border-bottom:1px solid #f6f7f8}
.sug div:last-child{border:none}
.sug div:hover,.sug div.sel{background:#fdf2f1}
.sug small{color:#9ca3af;font-size:11.5px;margin-left:8px}

/* 结论 */
.hit{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.hit b{font-size:19px;font-weight:700}
.badge{display:inline-block;padding:2px 10px;border-radius:20px;font-size:11.5px;
font-weight:700;background:#eef2ff;color:#4338ca}
.mline{font-size:13.5px;color:#4b5563;margin-top:3px}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:9px;margin-top:14px}
.kv div{background:#fafbfc;border:1px solid #eef0f3;border-radius:8px;padding:9px 11px}
.kv .l{font-size:11px;color:#9ca3af}
.kv .v{font-size:14px;font-weight:600;margin-top:1px}

h2{font-size:15px;font-weight:600;color:#374151;margin-bottom:13px;
padding-bottom:9px;border-bottom:1px solid #eef0f3;display:flex;align-items:center;
gap:8px;justify-content:space-between}
h2 span.t{display:flex;align-items:center;gap:8px}
h2 span.t::before{content:"";width:3px;height:14px;background:#c0392b;border-radius:2px}
h2 em{font-style:normal;font-size:12px;font-weight:500;color:#9ca3af}

/* 机器卡 */
.m{border:1px solid #eef0f3;border-radius:10px;padding:14px 16px;margin-bottom:10px;
transition:border-color .15s,box-shadow .15s}
.m:hover{border-color:#dfe3e8;box-shadow:0 3px 12px -6px rgba(0,0,0,.12)}
.m.top{border-color:#c0392b;border-width:1.5px;background:linear-gradient(180deg,#fffafa,#fff 60%)}
.m-h{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.m-n{font-size:15px;font-weight:650}
.m-p{font-size:19px;font-weight:750;color:#c0392b;font-variant-numeric:tabular-nums;white-space:nowrap}
.m-s{font-size:12.5px;color:#6b7280;margin-top:3px}
.m-p2{font-size:12px;color:#9ca3af;margin-top:6px;font-variant-numeric:tabular-nums}
.bv{display:inline-block;font-size:10px;padding:1px 5px;margin-left:6px;border-radius:3px;background:#fff7ed;color:#c2410c;border:1px solid #fed7aa;vertical-align:2px;font-weight:400}
.blist{display:flex;flex-direction:column;gap:1px;background:#f3f4f6;border:1px solid #e5e7eb;border-radius:6px;overflow:hidden;margin-top:8px}
.brow{display:flex;justify-content:space-between;align-items:center;gap:12px;background:#fff;padding:9px 11px;cursor:pointer}
.brow:hover{background:#fffbf5}
.bl{display:flex;align-items:baseline;gap:8px;min-width:0}
.bl b{font-size:13px;font-weight:600;color:#111827;white-space:nowrap}
.bn{font-size:11px;color:#9ca3af}
.br{text-align:right;min-width:0}
.bp{font-size:13px;font-weight:600;color:#c0392b;font-variant-numeric:tabular-nums;margin-right:8px}
.bmin{font-size:11px;color:#6b7280;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bh{font-size:12px;color:#6b7280;margin:14px 0 2px;font-weight:600}
.lad{font-size:12px;color:#374151;padding:5px 0;border-bottom:1px dashed #f3f4f6;font-variant-numeric:tabular-nums}
.lad span{font-weight:600;color:#111827;margin-right:6px}
.lad em{color:#059669;font-style:normal;margin-right:8px}
.lad i{color:#9ca3af;font-style:normal;margin-left:6px}
.m-edu{font-size:12px;color:#0f766e;margin-top:3px;font-variant-numeric:tabular-nums}
.m-bench{font-size:12px;color:#4b5563;margin-top:4px;font-variant-numeric:tabular-nums;letter-spacing:.2px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.chip{font-size:11px;padding:2px 8px;border-radius:5px;background:#f1f3f5;color:#6b7280}
.chip.ok{background:#e8f5e9;color:#1e8449;font-weight:600}
.chip.no{background:#fdecea;color:#c0392b;font-weight:600}
.chip.warn{background:#fffbeb;color:#b45309}

/* 列表 */
.mini{display:flex;justify-content:space-between;gap:12px;padding:9px 0;
border-bottom:1px solid #f6f7f8;font-size:13.5px;align-items:baseline}
.mini:last-child{border:none}
.mini .r{color:#6b7280;font-size:12px;text-align:right;flex:none;max-width:52%}
.mini .why{color:#c0392b}
.note{background:#fffbeb;border-left:3px solid #f59e0b;padding:11px 15px;
border-radius:0 7px 7px 0;font-size:12.5px;color:#78350f;margin-top:12px;line-height:1.75}
.note b{color:#92400e}
.empty{color:#9ca3af;font-size:13px;padding:8px 0}
.trap{background:#fafbfc;border-radius:9px;padding:13px 15px;font-size:13px;
color:#4b5563;line-height:1.85}
.foot{text-align:center;color:#9ca3af;font-size:11.5px;margin-top:26px;line-height:1.9}
.hide{display:none}
@media(max-width:600px){
  .m-p{font-size:17px} .wrap{padding:0} .mini .r{max-width:45%}
  input[type=number]{width:100px}
}
</style></head><body><div class="wrap">

<h1>按专业选电脑</h1>
<div class="sub">输入专业，看库里哪几台真的够用 · 判定依据是软件官方系统要求，不是销售话术</div>

<div class="picker" style="position:sticky">
  <div class="row" style="position:relative">
    <input type="text" id="q" placeholder="输入专业，如 计算机科学与技术 / 计科 / 土木工程" autocomplete="off">
    <input type="number" id="budget" placeholder="预算（元，只填这个也行）" min="0" step="500">
    <button class="btn" onclick="go()">查</button>
    <button class="btn ghost" onclick="reset()">清空</button>
    <div class="sug" id="sug"></div>
  </div>
</div>

<div id="out"></div>

<div class="foot" id="foot"></div>

</div>
<script>
const D = __DATA__;
const LADDER = {"4500": 4, "5000": 5, "5500": 5, "6000": 5, "6500": 5, "7000": 5, "7500": 5, "8000": 5, "8500": 5, "9000": 6, "9500": 9, "10000": 9, "10500": 9, "11000": 10, "11500": 10, "12000": 10, "12500": 10, "13000": 10, "13500": 10, "14000": 10, "14500": 10, "15000": 10, "15500": 10};
const M = {}; D.machines.forEach(m => M[m.id] = m);

/* ---------- 专业索引 ---------- */
const IDX = [];   // {name, gid}
D.groups.forEach(g => g.majors.forEach(n => IDX.push({name:n, gid:g.id, gname:g.name})));
const ALIAS = D.aliases;

function norm(s){ return (s||'').replace(/\s|　/g,'').toLowerCase(); }

function resolve(q){
  q = (q||'').trim();
  if(!q) return null;
  // 1) 专业名精确
  let hit = IDX.filter(x => norm(x.name) === norm(q));
  if(hit.length) return hit[0];
  // 2) 别名精确
  const a = ALIAS.find(x => norm(x[0]) === norm(q));
  if(a){
    hit = IDX.filter(x => norm(x.name) === norm(a[1]));
    if(hit.length) return hit[0];
  }
  // 3) 包含匹配：取最长（避免"建筑"命中"建筑环境与能源应用工程"这类更长的名字）
  hit = IDX.filter(x => norm(x.name).includes(norm(q)));
  if(hit.length){
    hit.sort((p,q2) => p.name.length - q2.name.length);
    return hit[0];
  }
  return null;
}

function suggest(q, limit){
  q = norm(q);
  if(!q) return [];
  const seen = new Set(), out = [];
  const push = x => {
    const k = x.name + '|' + x.gid;
    if(seen.has(k)) return;
    seen.add(k); out.push(x);
  };
  // 先别名（用户常打简称）
  ALIAS.forEach(a => {
    if(norm(a[0]).includes(q) || q.includes(norm(a[0]))){
      IDX.filter(x => norm(x.name) === norm(a[1])).slice(0,1).forEach(push);
    }
  });
  IDX.forEach(x => { if(norm(x.name).includes(q)) push(x); });
  return out.slice(0, limit || 8);
}

/* ---------- 输入交互 ---------- */
const qEl = document.getElementById('q');
const sugEl = document.getElementById('sug');
let selIdx = -1, curSug = [];

qEl.addEventListener('input', () => {
  const list = suggest(qEl.value, 9);
  curSug = list; selIdx = -1;
  if(!list.length){ sugEl.classList.remove('on'); return; }
  sugEl.innerHTML = list.map((x,i) =>
    `<div data-i="${i}" class="${i===selIdx?'sel':''}">${x.name}<small>${x.gname}</small></div>`).join('');
  sugEl.classList.add('on');
  sugEl.querySelectorAll('div').forEach(el => {
    el.onclick = () => { qEl.value = el.textContent.replace(/\s+.*$/,'').trim();
      // 用索引取值，别从 textContent 反解（会带上组名）
      qEl.value = list[+el.dataset.i].name;
      sugEl.classList.remove('on'); go(); };
  });
});
qEl.addEventListener('keydown', e => {
  if(!curSug.length) { if(e.key==='Enter') go(); return; }
  if(e.key === 'ArrowDown'){ selIdx = Math.min(selIdx+1, curSug.length-1); paint(); e.preventDefault(); }
  else if(e.key === 'ArrowUp'){ selIdx = Math.max(selIdx-1, -1); paint(); e.preventDefault(); }
  else if(e.key === 'Enter'){
    if(selIdx >= 0) qEl.value = curSug[selIdx].name;
    sugEl.classList.remove('on'); go();
    e.preventDefault();
  } else if(e.key === 'Escape'){ sugEl.classList.remove('on'); }
});
function paint(){
  sugEl.querySelectorAll('div').forEach((el,i) => el.classList.toggle('sel', i===selIdx));
}
document.getElementById('budget').addEventListener('keydown', e => { if(e.key==='Enter') go(); });
/* 快捷预算档：一键看该档全貌。按钮文字直接标注"该档能买 X/10 个方向"，
   数据来自 budget_ladder.json（夜间任务按 500 元一档全量算好的静态快照） */
(function(){
  const box = document.getElementById('budget');
  const bar = document.createElement('div');
  bar.style.cssText='display:flex;flex-wrap:wrap;gap:6px;margin-top:6px';
  const fmt=v=>'¥'+(v/1000>=1?(v/1000).toFixed(1).replace('.0','')+'k':v);
  Object.keys(LADDER).forEach(v=>{
    const b=document.createElement('button');
    b.type='button';
    b.style.cssText='font-size:11px;padding:3px 8px;border:1px solid #e5e7eb;background:#fff;'+
      'border-radius:4px;cursor:pointer;color:#374151;font-variant-numeric:tabular-nums';
    b.textContent=fmt(+v)+' · '+LADDER[v]+'/10';
    b.title='查看预算 '+v+' 元时各方向能买到的机器';
    b.onclick=()=>{ box.value=v; go(); };
    bar.appendChild(b);
  });
  box.parentNode.insertBefore(bar, box.nextSibling);
})();
document.addEventListener('click', e => {
  if(!sugEl.contains(e.target) && e.target !== qEl) sugEl.classList.remove('on');
});

/* ---------- 渲染 ---------- */
function yuan(v){
  if(v == null) return '价格未采';
  return '¥' + Math.round(v).toLocaleString('zh-CN');
}

function verdictOf(g, id){
  return g.verdicts[id] || {v:'unk'};
}

function r(res, budget){
  const g = res.group;
  const rows = Object.keys(g.verdicts).map(id => ({m: M[id], ...g.verdicts[id]}))
    .filter(x => x.m);
  const ok  = rows.filter(x => x.v==='ok').sort((a,b)=>(a.m.price??1e9)-(b.m.price??1e9));
  const deg = rows.filter(x => x.v==='deg').sort((a,b)=>(a.m.price??1e9)-(b.m.price??1e9));
  const no  = rows.filter(x => x.v==='no');
  const unk = rows.filter(x => x.v==='unk');

  const good = ok.concat(deg);
  const goodP = good.filter(x => x.m.price != null);
  let shown = goodP, over = [];
  if(budget){
    shown = goodP.filter(x => x.m.price <= budget);
    over  = goodP.filter(x => x.m.price >  budget);
  }

  const req = g.req;
  const gpuWord = {none:'不要独显（核显足够）', either:'核显或独显都行',
                   discrete:'必须是独立显卡', certified:'需认证显卡'}[req.gpu] || req.gpu;

  let h = '';

  /* 结论条 */
  h += `<div class="card">
    <div class="hit"><b>${res.name}</b>
      <span class="badge">${g.name}</span>
      <span style="font-size:12.5px;color:#9ca3af">${g.tagline}</span></div>`;

  if(!good.length){
    h += `<div class="note"><b>库里没有一台满足这一组的门槛。</b><br>
      这不是数据缺失 —— 是现有 ${D.machines.length} 台里确实没有够用的。
      下面「不够用的」逐台写了原因，照那个清单去筛比重新找更省时间。</div>`;
  } else {
    const cheap = goodP.length ? goodP[0] : null;
    h += `<div class="mline" style="margin-top:10px">`;
    if(cheap) h += `能买，最便宜 <b style="color:#c0392b">${yuan(cheap.m.price)}</b> —— ${cheap.m.name}`;
    else h += `够用 ${good.length} 台，但都没采到价格`;
    if(budget) h += `<br>你的预算 ${yuan(budget)} 内可选 <b>${shown.length}</b> 台`;
    h += `</div>`;
  }

  /* 门槛 */
  h += `<div class="kv">
    <div><div class="l">内存</div><div class="v">${req.ramMin}G 起 · 建议 ${req.ramRec}G</div></div>
    <div><div class="l">显卡</div><div class="v">${gpuWord}</div></div>
    ${req.vramMin ? `<div><div class="l">显存</div><div class="v">${req.vramMin}G 起</div></div>` : ''}
    <div><div class="l">存储</div><div class="v">${req.storageMinGB}G 起</div></div>
  </div>`;
  if(req.rationale){
    h += `<div class="mline" style="margin-top:11px;color:#6b7280;line-height:1.8">
      依据：${req.rationale}</div>`;
  }
  if(g.gates.length){
    h += `<div class="mline" style="margin-top:9px"><b>硬门槛：</b>` +
      g.gates.map(x => {
        const d = {ram:'内存',gpu:'显卡',storage:'存储',upgrade:'可加装'}[x.dim] || x.dim;
        return `${d} ≥ ${x.min}（${x.why}）`;
      }).join('；') + `</div>`;
  }
  h += `</div>`;

  /* 能买的 */
  if(good.length){
    h += `<div class="card"><h2><span class="t">能买的</span>
      <em>${good.length} 台 · ${goodP.length} 台有价格</em></h2>`;
    if(!shown.length && budget){
      h += `<div class="empty">预算 ${yuan(budget)} 内没有。下面是超预算的，供你判断要不要加钱。</div>`;
    }
    const list = budget ? (shown.length ? shown : over) : goodP;
    const capped = list.slice(0, 8);
    const bvId = bestValueId(g, goodP);
    capped.forEach((x,i) => { h += mcard(x, i===0 && !budget, x.m.id===bvId); });
    if(list.length > capped.length){
      h += `<div class="empty">还有 ${list.length - capped.length} 台，改预算可收紧范围</div>`;
    }
    if(budget && shown.length && over.length){
      h += `<div class="empty" style="margin-top:10px">另有 ${over.length} 台超预算，最便宜 ${yuan(over[0].m.price)}</div>`;
    }
    // 没价格的单独说
    const np = good.filter(x => x.m.price == null);
    if(np.length){
      h += `<div class="empty" style="margin-top:10px">另有 ${np.length} 台够用但未采到价格：` +
        np.map(x => x.m.name).join('、') + `</div>`;
    }
    h += `</div>`;
  }

  /* 不够用的 */
  const noNear = no.filter(x => (x.w||[]).length === 1);
  const noFar  = no.filter(x => (x.w||[]).length > 1);
  if(no.length){
    h += `<div class="card"><h2><span class="t">明确不够用的</span>
      <em>${no.length} 台 · 这是结论，不是「不知道」</em></h2>`;
    if(noNear.length){
      h += `<div style="font-size:12.5px;font-weight:600;color:#374151;margin:4px 0 6px">只差一项的（${noNear.length} 台）</div>`;
      noNear.slice(0,8).forEach(x => {
        const gap = x.w[0];
        const note = /不是同一台|无法推荐/.test(gap) ? '数据要修，不是机器问题'
                   : /核显/.test(gap) ? '显卡换不了，补不上'
                   : /焊死/.test(gap) ? '内存焊死，补不上' : '可能可以补';
        h += `<div class="mini"><span>${x.m.name}<br><small style="color:#9ca3af">${x.m.spec}</small></span>
          <span class="r"><span class="why">${gap}</span><br><small>${note}</small></span></div>`;
      });
      if(noNear.length > 8) h += `<div class="empty">还有 ${noNear.length-8} 台</div>`;
    }
    if(noFar.length){
      h += `<div style="font-size:12.5px;font-weight:600;color:#374151;margin:14px 0 6px">差两项以上，不用考虑（${noFar.length} 台）</div>`;
      noFar.slice(0,5).forEach(x => {
        h += `<div class="mini"><span>${x.m.name}</span>
          <span class="r"><small>${(x.w||[]).join('；')}</small></span></div>`;
      });
      if(noFar.length > 5) h += `<div class="empty">还有 ${noFar.length-5} 台</div>`;
    }
    h += `</div>`;
  }

  /* 数据不足 */
  if(unk.length){
    const cnt = {};
    unk.forEach(x => (x.miss||[]).forEach(k => cnt[k] = (cnt[k]||0)+1));
    const top = Object.entries(cnt).sort((a,b)=>b[1]-a[1]);
    h += `<div class="card"><h2><span class="t">数据不足，判不了</span>
      <em>${unk.length} 台 · 补上就能判</em></h2>
      <div class="mline" style="margin-bottom:8px">卡在：` +
      top.map(([k,v]) => `${k} × ${v}`).join('、') + `</div>`;
    unk.slice(0,6).forEach(x => {
      h += `<div class="mini"><span>${x.m.name}</span>
        <span class="r"><small>缺 ${(x.miss||[]).join('、')}</small></span></div>`;
    });
    if(unk.length > 6) h += `<div class="empty">还有 ${unk.length-6} 台</div>`;
    h += `</div>`;
  }

  /* 陷阱与追问 */
  h += `<div class="card"><h2><span class="t">最容易踩的坑</span></h2>
    <div class="trap">${g.trap}</div>`;
  if(g.followups.length){
    h += `<div class="mline" style="margin-top:12px"><b>还要问自己一句：</b></div>`;
    g.followups.forEach(q => h += `<div class="mline" style="padding-left:10px">· ${q}</div>`);
  }
  h += `</div>`;

  return h;
}


/* 每元性能：同组内比较"都能用的情况下谁最划算"。
   口径与 pick.py 一致（vm 由后端按组的 R 字段决定）。
   没有跑分的机器返回 null，不参与比较，也不会被标成不划算。 */
function valueScore(m, vmk){
  const b = m.bench || {};
  const v = vmk==='timeSpy' ? b.timeSpy : (vmk==='cbMulti' ? b.cbMulti : b.cbSingle);
  if(v == null || m.price == null) return null;
  return v / m.price;
}
function bestValueId(g, list){
  const vm = g.vm || 'cbSingle';
  let id = null, hi = 0;
  list.forEach(x => { const s = valueScore(x.m, vm); if(s != null && s > hi){ hi = s; id = x.m.id; } });
  return id;
}
const VM_WORD = {timeSpy:'每元显卡分', cbMulti:'每元多核分', cbSingle:'每元单核分'};

function mcard(x, isTop, isBest){
  const m = x.m;
  let h = `<div class="m${isTop?' top':''}">
    <div class="m-h"><span class="m-n">${m.name}${isBest?' <span class="bv">★ 每元性能最高</span>':''}</span>
      <span class="m-p">${yuan(m.price)}</span></div>
    <div class="m-s">${m.spec}</div>`;
  let p2 = [];
  if(m.list && m.list !== m.price) p2.push('划线 ' + yuan(m.list));
  if(m.net && m.net !== m.price && m.net !== m.list) p2.push('券后 ' + yuan(m.net));
  if(m.subsidy) p2.push('国补 ' + yuan(m.subsidy));
  p2.push(m.shop);
  if(m.captured) p2.push('采价 ' + m.captured);
  h += `<div class="m-p2">${p2.join(' · ')}</div>`;
  if(m.edu && m.edu < m.price){
    h += `<div class="m-edu">学生价 ${yuan(m.edu)}（教育优惠，需认证，再省 ${yuan(m.price-m.edu)}）</div>`;
  }

  // 性能跑分：只写有出处的数。同价位比"快多少"比"参数多一条"更有用。
  if(m.bench){
    const b = m.bench, bs = [];
    if(b.cbMulti || b.cbSingle){
      const t = [];
      if(b.cbMulti) t.push('多核 ' + b.cbMulti.toLocaleString('zh-CN'));
      if(b.cbSingle) t.push('单核 ' + b.cbSingle.toLocaleString('zh-CN'));
      bs.push('Cinebench R23 ' + t.join(' / '));
    }
    if(b.timeSpy) bs.push('3DMark 显卡 ' + b.timeSpy.toLocaleString('zh-CN'));
    h += `<div class="m-bench">${bs.join('  ·  ')}</div>`;
  }

  let chips = [];
  if(m.eff === 1) chips.push('<span class="chip ok">1 级能效 · 可领国补</span>');
  else if(m.eff) chips.push(`<span class="chip no">${m.eff} 级能效 · 拿不到国补</span>`);
  else chips.push('<span class="chip warn">能效未查 · 补贴不保证</span>');
  if(x.w && x.w.length) chips.push(`<span class="chip warn">${x.w[0]}</span>`);
  (m.risks||[]).forEach(t => chips.push(`<span class="chip">${t}</span>`));
  if(chips.length) h += `<div class="chips">${chips.join('')}</div>`;
  h += `</div>`;
  return h;
}


/* ---------- 预算总览 ----------
   与 tools/budget.py 同一口径：够用(ok/deg) 且 到手价 ≤ 预算 才算"买得到"。
   买不到的方向不算含糊，直接报差多少钱；再加价格阶梯看"多花多少能多解锁几个"。 */
function groupRows(g){
  return Object.keys(g.verdicts).map(id => ({m: M[id], ...g.verdicts[id]}))
    .filter(x => x.m && (x.v==='ok' || x.v==='deg') && x.m.price != null);
}
function budgetView(budget){
  const rows = D.groups.map(g => {
    const good = groupRows(g).filter(x => x.m.price <= budget).sort((a,b)=>a.m.price-b.m.price);
    const over = groupRows(g).filter(x => x.m.price >  budget).sort((a,b)=>a.m.price-b.m.price);
    return {g, good, over, gap: (!good.length && over.length) ? over[0].m.price - budget : null};
  });
  const ok = rows.filter(x => x.good.length).sort((a,b)=>b.good.length-a.good.length);
  const no = rows.filter(x => !x.good.length).sort((a,b)=>(a.gap??1e9)-(b.gap??1e9));

  let h = `<div class="card"><h2><span class="t">预算 ${yuan(budget)} 能学什么</span>
    <em>${ok.length}/${rows.length} 个方向现在就能买</em></h2>`;

  if(ok.length){
    h += `<div class="blist">`;
    ok.forEach(x => {
      const c = x.good[0].m, b = c.bench || {};
      // 只显示该组的评判维度（与 tools/budget.py 一致）：
      // 办公组看单核，视频组看显卡，理工看多核 —— 显示别的维度会让人误判
      const vmk = x.g.vm || 'cbSingle';
      const val = vmk==='timeSpy' ? b.timeSpy : (vmk==='cbMulti' ? b.cbMulti : b.cbSingle);
      const vLabel = {timeSpy:'显卡', cbMulti:'多核', cbSingle:'单核'}[vmk];
      const v = val ? `${vLabel} ${val.toLocaleString('zh-CN')}` : '';
      h += `<div class="brow" onclick="pick('${(x.g.majors||[])[0]||''}')">
        <div class="bl"><b>${x.g.name}</b><span class="bn">${x.good.length} 台可选</span></div>
        <div class="br"><span class="bp">${yuan(c.price)}</span>
          <span class="bmin">${c.name}${v?' · '+v:''}</span></div>
      </div>`;
    });
    h += `</div>`;
  }

  if(no.length){
    h += `<div class="bh">这个预算买不到的方向（不是没机器，是够用的都超了）</div><div class="blist">`;
    no.forEach(x => {
      h += `<div class="brow"><div class="bl"><b>${x.g.name}</b></div>
        <div class="br">${x.gap!=null
          ? `<span class="bp">差 ${yuan(x.gap)}</span><span class="bmin">最少 ${yuan(budget+x.gap)} · ${x.over[0].m.name}</span>`
          : `<span class="bmin">库里没有够用且有价格的机器</span>`}</div></div>`;
    });
    h += `</div>`;

    const steps = [...new Set(no.filter(x=>x.gap).map(x=>x.gap))].sort((a,b)=>a-b);
    if(steps.length){
      h += `<div class="bh">往上加钱的阶梯</div>`;
      steps.slice(0,4).forEach(s => {
        const names = no.filter(x=>x.gap && x.gap<=s).map(x=>x.g.name);
        const fresh = no.filter(x=>x.gap===s).map(x=>x.g.name);
        h += `<div class="lad"><span>${yuan(budget+s)}</span>
          <em>+${yuan(s)}</em> 可买 ${ok.length+names.length}/${rows.length}
          <i>新解锁：${fresh.join('、')}</i></div>`;
      });
    }
  }
  h += `<div class="empty" style="margin-top:10px">点任意方向看该专业组的门槛和逐台推荐</div></div>`;
  return h;
}

function go(){
  const q = document.getElementById('q').value.trim();
  const bEl = document.getElementById('budget');
  const budget = bEl.value ? +bEl.value : null;
  const out = document.getElementById('out');
  if(!q){
    // 只填预算不填专业：学生常有的提问顺序 —— 家里先定了预算
    out.innerHTML = budget ? budgetView(budget) : '';
    if(budget) window.scrollTo({top:0, behavior:'smooth'});
    return;
  }

  const hit = resolve(q);
  if(!hit){
    const cand = suggest(q, 6);
    out.innerHTML = `<div class="card"><div class="empty">认不出「${q}」。</div>` +
      (cand.length ? `<div class="mline" style="margin-top:8px">你是说：` +
        cand.map(x => `<a href="javascript:void(0)" onclick="pick('${x.name}')" style="color:#c0392b">${x.name}</a>`).join(' / ') + `</div>`
      : `<div class="mline" style="margin-top:8px">目前覆盖 ${IDX.length} 个专业，试试上面的输入建议。</div>`) +
      `</div>`;
    return;
  }
  const g = D.groups.find(x => x.id === hit.gid);
  out.innerHTML = r({name: hit.name, group: g}, budget);
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function pick(name){
  document.getElementById('q').value = name;
  go();
}

function reset(){
  document.getElementById('q').value = '';
  document.getElementById('budget').value = '';
  document.getElementById('out').innerHTML = '';
  document.querySelector('.picker').scrollIntoView({behavior:'smooth'});
}

/* 页脚 */
document.getElementById('foot').innerHTML =
  `库里 ${D.machines.length} 台机型 · ${IDX.length} 个专业 · 更新于 ${D.generatedAt}<br>` +
  `判定按「软件官方系统要求」推导，不按销售推荐。库里没有的机型不代表不行，只是还没采进来。<br>` +
  `国补按家电线 15%、单台封顶 1500 计算，且需要 1 级能效备案才拿得到。`;
</script>
</body></html>
"""

if __name__ == "__main__":
    sys.exit(main())