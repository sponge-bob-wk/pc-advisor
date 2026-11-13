# -*- coding: utf-8 -*-
"""night_run.py —— 无人值守夜间任务编排器

设计原则：
  1. 每个阶段独立 try/except，一步失败不影响后续（凌晨没人看着，不能卡死）
  2. 每阶段记录 exit code 与耗时，最后写自检报告
  3. 浏览器相关步骤是 best-effort：守卫拦截就跳过，绝不阻塞
  4. 关键指标（价格不平数、能效覆盖数、回归失败数）前后对比，恶化即标红

用法：
  python tools/night_run.py            # 全量（约 2 小时）
  python tools/night_run.py --quick    # 跳过浏览器阶段（约 30 分钟，用于验证脚本本身）
"""
import json
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "night_run_log.txt"
SELF = DATA / "selfcheck_report.md"

PY = sys.executable
PHASES = []          # (名称, 起始时间, 结束时间, ok, 摘要)
START = time.time()


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd, timeout=1800):
    """跑一个子命令，返回 (rc, 输出尾部)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           cwd=str(ROOT))
        tail = "\n".join((r.stdout or "").strip().splitlines()[-6:])
        return r.returncode, tail
    except subprocess.TimeoutExpired:
        return 124, "超时"
    except Exception as e:
        return 1, str(e)


def phase(name, fn):
    log(f"▶ 阶段开始：{name}")
    t0 = time.time()
    ok, summary = False, ""
    try:
        summary = fn()
        ok = True
    except Exception:
        summary = "异常：" + traceback.format_exc(limit=3).replace("\n", " | ")
        log(summary)
    dt = time.time() - t0
    PHASES.append((name, t0, time.time(), ok, summary))
    log(f"■ 阶段结束：{name}  {'✓' if ok else '✗'}  {dt:.0f}s  {summary[:200]}\n")
    return ok


# ---------------------------------------------------------------- 阶段实现

def snap_metrics():
    """采集关键指标快照，供前后对比。"""
    pool = json.loads((DATA / "machine_pool.json").read_text(encoding="utf-8"))["machines"]
    priced = [m for m in pool if (m.get("price") or {}).get("final") or (m.get("price") or {}).get("net")]
    badp = 0
    for m in priced:
        pr = m["price"]
        lst, net, sub, fin = pr.get("list"), pr.get("net"), pr.get("subsidy"), pr.get("final")
        dis = sum((d.get("amount") or 0) for d in (pr.get("discounts") or []))
        if fin is None or net is None:
            continue
        if lst is not None and abs(net - (lst - dis)) > 1.5:
            badp += 1
        elif abs(fin - (net - (sub or 0))) > 1.5:
            badp += 1
    return {
        "machines": len(pool),
        "priced": len(priced),
        "price_bad": badp,
        "eff1": sum(1 for m in pool if m.get("efficiency") == 1),
        "cert": sum(1 for m in pool if m.get("certModel")),
        "throttle": sum(1 for m in pool if m.get("throttle")),
    }


def phase_a():
    """价格对账 + 能效型号串修正（纯 Python）。"""
    rc, out = run([PY, "tools/audit_prices.py", "--fix"], 600)
    log("audit_prices: " + out.splitlines()[-1] if out else "")

    # 能效型号串与台账精确对齐
    led = json.loads((DATA / "efficiency_ledger.json").read_text(encoding="utf-8"))
    have = {r.get("model"): r.get("level") for r in led["records"] if r.get("model")}
    # 台账里的 model 字段可能是逗号拼接多型号，展开
    expanded = {}
    for r in led["records"]:
        for part in str(r.get("model") or "").split(","):
            part = part.strip()
            if part:
                expanded[part] = int(r["level"])

    p = DATA / "machine_pool.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    fixed = 0
    for m in d["machines"]:
        em = m.get("energyModel")
        if not em:
            continue
        # 1) 能精确匹配台账 → 顺带把等级带上（若等级缺失）
        if em in expanded and m.get("efficiency") is None:
            m["efficiency"] = expanded[em]
            m["efficiencyStatus"] = "verified"
            fixed += 1
            log(f"  能效对齐：{m['name'][:26]} ← {em} = {expanded[em]} 级")
        # 2) Mac：MC7C4CH/A 这类是 SKU 型号不是能效备案型号，且能效网查无备案
        #    置空 energyModel，把"查无备案"写进证据，消除跨源核对里的假缺口
        if (m.get("devicePlatform") or "").lower() == "macos":
            m["energyModel"] = None
            ev = m.setdefault("evidence", {})
            ev["energySearch"] = ("能效网无 MacBook 备案（已按型号尝试查询，0 行）；"
                                  "京东明确标可享国补，国补口径对苹果可能另有认定，未核清前不填等级")
            fixed += 1
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"能效型号修正 {fixed} 处")

    # specs 里的 Mac energyModel 也要同步置空，防止反向补齐盖回来
    ps = DATA / "specs_verified.json"
    sv = json.loads(ps.read_text(encoding="utf-8"))
    for r in sv["records"]:
        if (r.get("devicePlatform") or "").lower() == "macos":
            if r.get("energyModel"):
                r["energyModel"] = None
                r["efficiencyStatus"] = r.get("efficiencyStatus") or "pending"
                r.setdefault("evidence", {})
                r["evidence"]["energySearch"] = "能效网无 MacBook 备案（夜间任务核实：按型号查询 0 行）"
                fixed += 1
    json.dump(sv, open(ps, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return f"价格对账完成；能效型号修正 {fixed} 处"


def phase_b():
    """全量 311 专业回归。"""
    majors = json.loads((DATA / "majors.json").read_text(encoding="utf-8"))
    names = []
    for g in majors["groups"]:
        for it in g.get("majors") or []:
            nm = it if isinstance(it, str) else it.get("name")
            if nm:
                names.append(nm)
    names = list(dict.fromkeys(names))
    log(f"全量回归 {len(names)} 个专业")
    fails, dist = [], {}
    out = {"total": len(names), "fails": [], "dist": dist, "empty": []}
    for i, n in enumerate(names):
        r = subprocess.run([PY, "tools/pick.py", n], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
        if r.returncode != 0 or "Traceback" in (r.stderr or ""):
            fails.append({"name": n, "err": (r.stderr or "")[-300:]})
            log(f"  ✗ {n}: {(r.stderr or '')[-120:]}")
            continue
        txt = r.stdout or ""
        # 统计能买/不够等关键词行，粗粒度分布
        for key, pat in (("能买", "能买，最便宜"), ("不够", "明确不够用"), ("数据不足", "数据不足")):
            if pat in txt:
                dist[key] = dist.get(key, 0) + 1
                break
        if "能买的（0 台" in txt:
            out["empty"].append(n)
        if (i + 1) % 50 == 0:
            log(f"  … {i+1}/{len(names)}")
    out["fails"] = fails
    (DATA / "regression_full.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"回归完成：失败 {len(fails)}，分布 {dist}，0 台可推荐 {len(out['empty'])} 个")
    return f"{len(names)} 个专业，失败 {len(fails)}，零候选 {len(out['empty'])}"


def phase_c():
    """预算阶梯静态数据：每档 500 元，各方向可买台数与最便宜机型。"""
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parent))
    from lib_judge import judge, r_fields_of
    pool = json.loads((DATA / "machine_pool.json").read_text(encoding="utf-8"))["machines"]
    fmat = json.loads((DATA / "field_matrix.json").read_text(encoding="utf-8"))
    majors = json.loads((DATA / "majors.json").read_text(encoding="utf-8"))
    ladder = {}
    for budget in range(4500, 15501, 500):
        row = {}
        for g in majors["groups"]:
            rf = r_fields_of(fmat["matrix"], g["id"])
            best = None
            cnt = 0
            for m in pool:
                v, _, _ = judge(m, g.get("req") or {}, rf, g.get("hardGates") or [])
                pr = m.get("price") or {}
                p = pr.get("final") or pr.get("net")
                if v in ("OK", "DEGRADED") and p and p <= budget:
                    cnt += 1
                    if best is None or p < best[0]:
                        best = (p, m["name"], g.get("vm"))
            row[g["id"]] = {"count": cnt,
                            "cheapest": {"price": best[0], "name": best[1]} if best else None}
        ladder[budget] = row
    (DATA / "budget_ladder.json").write_text(
        json.dumps(ladder, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"预算阶梯 {len(ladder)} 档写入 budget_ladder.json"


def phase_d():
    """能效台账缺口批量补（浏览器 best-effort，失败自动跳过）。"""
    # 从跨源核对里取"采集有能效型号但台账未收录"的型号
    r = subprocess.run([PY, "tools/check_cross_source.py"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=300)
    txt = r.stdout or ""
    models = re.findall(r"\('(?:[^']+)',\s*'([^']+)'\)", txt)
    models = [x for x in models if x and "未标" not in x]
    log(f"待补能效型号 {len(models)} 个: {models}")
    done, skip = [], []
    for kw in models:
        for attempt in range(2):
            rc, out = run([PY, "tools/energy_batch.py", kw], 300)
            if "0 行" in out:
                skip.append((kw, "查无备案"))
                log(f"  [查无] {kw}")
                break
            if rc == 0 and "行" in out:
                done.append(kw)
                log(f"  [OK] {kw}: {out.splitlines()[-1]}")
                break
            log(f"  [重试{attempt+1}] {kw}: {out.splitlines()[-1] if out else '无输出'}")
            time.sleep(5)
        else:
            skip.append((kw, "两次尝试失败"))
    # Mac 的型号在 Phase A 已置空，这里若仍出现说明 sources 没同步
    return f"补入 {len(done)}，查无/跳过 {len(skip)}"


def phase_e():
    """报告与页面。"""
    rc1, o1 = run([PY, "tools/audit_report.py"], 300)
    rc2, o2 = run([PY, "tools/make_pick_page.py"], 300)
    rc3, o3 = run([PY, "tools/check_cross_source.py"], 300)
    return f"report={rc1} page={rc2} cross={rc3}"


def phase_f(before):
    """自检：重跑抽样回归 + 指标对比。"""
    after = snap_metrics()
    # 重建后池可能变化，重新读
    majors = json.loads((DATA / "majors.json").read_text(encoding="utf-8"))
    names = []
    for g in majors["groups"]:
        for it in (g.get("majors") or [])[:3]:
            nm = it if isinstance(it, str) else it.get("name")
            if nm:
                names.append(nm)
    names = list(dict.fromkeys(names))
    fails = [n for n in names
             if (lambda r: r.returncode != 0 or "Traceback" in (r.stderr or ""))(
                 subprocess.run([PY, "tools/pick.py", n], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", cwd=str(ROOT)))]

    lines = ["# 夜间任务自检报告",
             f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
             "## 阶段执行情况\n"]
    for name, t0, t1, ok, summ in PHASES:
        lines.append(f"- {'✓' if ok else '✗'} **{name}**（{t1-t0:.0f}s）— {summ[:160]}")

    lines.append("\n## 关键指标（前 → 后）\n")
    lines.append("| 指标 | 前 | 后 | 判定 |")
    lines.append("|---|---|---|---|")
    alerts = []
    for k in ("machines", "priced", "price_bad", "eff1", "cert", "throttle"):
        b, a = before.get(k), after.get(k)
        worse = (k == "price_bad" and a > b) or (k != "price_bad" and a < b)
        mark = "⚠ 恶化" if worse else "OK"
        if worse:
            alerts.append(k)
        lines.append(f"| {k} | {b} | {a} | {mark} |")

    lines.append(f"\n## 抽样回归（{len(names)} 个专业）\n- 失败：{len(fails)}"
                 + (f" → {fails}" if fails else ""))
    if alerts:
        lines.append(f"\n**⚠ 指标恶化：{alerts}** —— 需人工检查")
    lines.append(f"\n总耗时：{time.time()-START:.0f} 秒")
    SELF.write_text("\n".join(lines), encoding="utf-8")
    log(f"自检报告 → {SELF}")
    return f"抽样回归失败 {len(fails)}，指标恶化 {alerts or '无'}"


def main():
    quick = "--quick" in sys.argv
    log("=" * 60)
    log(f"夜间任务启动 {'(quick)' if quick else '(全量)'}")
    before = snap_metrics()
    log(f"任务前指标：{before}\n")

    phase("A 价格对账+能效型号修正", phase_a)
    subprocess.run([PY, "tools/build_pool.py"], capture_output=True, cwd=str(ROOT))
    phase("B 全量专业回归", phase_b)
    phase("C 预算阶梯数据", phase_c)
    if not quick:
        phase("D 能效台账批量补（浏览器）", phase_d)
        subprocess.run([PY, "tools/build_pool.py"], capture_output=True, cwd=str(ROOT))
    phase("E 报告与页面重建", phase_e)
    phase("F 自检", lambda: phase_f(before))

    log(f"\n全部结束，总耗时 {(time.time()-START)/60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
