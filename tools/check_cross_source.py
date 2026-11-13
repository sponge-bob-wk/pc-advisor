#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_cross_source.py —— 跨数据源一致性校验

这个项目的数据分散在几个文件里，字段同名不同源是最大的隐患：
  prices.json          —— 价格与机型主档（offers[]）
  specs_verified.json  —— 规格核实（records[]）
  efficiency_ledger.json —— 能效备案台账（records[]）
  efficiency_match_report.json —— 匹配明细
  bench.json / majors.json / risks.json / pitfalls.json

不校验的后果：我在 specs 里填了 efficiency，audit_data.py 看 prices.json 仍然报「18 台缺」，
              于是「已完成」和「仍缺」同时成立，谁都说不清真实进度。

本脚本只报差异，不改数据。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def norm(s):
    if not s:
        return ""
    s = s.replace("　", " ").translate(str.maketrans("（）－—", "()--"))
    return re.sub(r"\s+", "", s).lower()


def main():
    prices = load("prices.json")
    specs = load("specs_verified.json")
    led = load("efficiency_ledger.json")
    report = load("efficiency_match_report.json")

    issues = []

    # ---- 1. 机型池自检：每个字段的来源指向是否成立 ----
    # 这一段取代了早前「prices ↔ specs 靠 model_bridge 桥接」的校验。
    # 桥接表是临时方案：它靠人工写映射，覆盖 5 条就写不动了。
    # 现在 build_pool.py 用 modelHint 精确匹配 + SKU 去重把两边合成机型池，
    # 校验的对象也该换成池子本身 —— 检查每条记录的 dataFrom 是否真的指向存在的记录。
    pool = load("machine_pool.json")
    machines = pool["machines"]
    spec_ids = {m["id"] for m in specs["records"]}
    crawl_ids = set()
    for f in sorted((ROOT / "data" / "crawl").glob("2026-*.jsonl")):
        if ".bak" in f.name or "pending" in f.name:
            continue
        for ln in f.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                crawl_ids.add(json.loads(ln).get("id"))
            except Exception:
                continue

    bad_src, dup_sku = [], []
    seen_sku = {}
    for m in machines:
        src = m.get("dataFrom") or {}
        s_spec = (src.get("spec") or "").replace("specs_verified.json", "")
        if src.get("spec") == "specs_verified.json" and m["id"] not in spec_ids:
            bad_src.append(f"{m['name']}：dataFrom.spec 写着 specs，但 id 不在 specs 里")
        p_src = (src.get("price") or "").replace("crawl/", "")
        if p_src and p_src not in crawl_ids:
            bad_src.append(f"{m['name']}：dataFrom.price 指向 {p_src}，采集记录里没有")
        sku = m.get("platformSkuId")
        if sku:
            if sku in seen_sku:
                dup_sku.append(f"{sku}：{seen_sku[sku]} / {m['name']}")
            seen_sku[sku] = m["name"]
    if bad_src:
        issues.append(("机型池里有记录指向了不存在的 id", len(bad_src), bad_src))
    if dup_sku:
        issues.append(("同一 SKU 在池子里出现多次（推荐会重复）", len(dup_sku), dup_sku))

    # ---- 1b. 有价格无规格 / 有规格无价格（以池子为准） ----
    # 早前这个数字大得吓人（13 + 24），是因为 prices.json 与 specs 是两套完全不同的样本。
    # 池子合并后只剩真正没补上的那几台。
    pc = [m for m in machines if m.get("cat") in ("thin", "gaming", "desktop")]
    no_price = [m["name"] for m in pc
                if not ((m.get("price") or {}).get("net") or (m.get("price") or {}).get("final"))]
    no_spec = [m["name"] for m in pc if not m.get("cpu") or not m.get("ram")]
    if no_spec:
        issues.append(("有价格无规格（无法判断专业适配，优先补）", len(no_spec), no_spec))
    if no_price:
        issues.append(("有规格无价格（进不了预算比较）", len(no_price), no_price))

    # ---- 2. efficiency 三处一致性 ----
    eff_in_specs = {norm(m["modelHint"]): m for m in specs["records"] if m.get("efficiency")}
    eff_in_prices = [o for o in prices["offers"] if o.get("efficiency")]
    if eff_in_specs and not eff_in_prices:
        issues.append((
            "efficiency 只写在 specs_verified.json，prices.json 完全没有该字段",
            len(eff_in_specs),
            [m["modelHint"] for m in eff_in_specs.values()],
        ))

    # ---- 3. 台账等级 vs 回写等级 ----
    ledger_lv = {}
    for r in led["records"]:
        ledger_lv.setdefault(norm(r["model"]), []).append(r["level"])
    mismatch = []
    for it in report["items"]:
        if it["status"] != "EXACT":
            continue
        spec_m = eff_in_specs.get(norm(it["modelHint"]))
        if not spec_m:
            continue
        got = spec_m["efficiency"]
        want = sorted({f["level"] for f in it["filings"]})
        # efficiency 已规范化为 int（1/2/3），直接比数值
        if len(want) == 1 and got != want[0]:
            mismatch.append((it["modelHint"], got, want))
    if mismatch:
        issues.append(("efficiency 回写值与备案不符", len(mismatch), mismatch))

    # ---- 4. 备案台账里有没有解析残缺 ----
    bad = [r for r in led["records"] if not r.get("level") or not r.get("date") or not r.get("regNo")]
    if bad:
        issues.append(("台账存在字段残缺记录", len(bad), [r.get("model") for r in bad]))

    # ---- 5. 采集记录的能效型号是否都已查询（按型号判重，不按机型名） ----
    # 只能用台账里实际存在的备案型号来判断「查过没有」。
    # 按机型名判重会漏：同一个 energyModel 可能挂在多条采集记录上，而 specs 里没有对应机型。
    # 一条备案常挂多个型号（逗号分隔），必须拆开匹配 ——
    # 否则机器记"机械革命 无界15XPA"、台账记"…15XPA,…15XAG"，整串比对永远对不上
    have_models = set()
    for r in led["records"]:
        for part in str(r["model"]).split(","):
            part = part.strip()
            if part:
                have_models.add(norm(part))
    # 查过但确实没有的型号，也算「已处理」，不该继续报缺口
    nodata_path = ROOT / "data" / "efficiency_nodata.json"
    if nodata_path.exists():
        nd = json.loads(nodata_path.read_text(encoding="utf-8"))
        have_models |= {norm(x["query"]) for x in nd.get("noData", [])}
    pending, seen_pending = [], set()
    for f in sorted((ROOT / "data" / "crawl").glob("*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            # 已核定能效等级的记录不再依赖型号收录 —— 型号缺口是"待查"信号，
            # 查清了就不该继续挂缺口（否则永远清不了账）
            if isinstance(d.get("config"), dict) and d["config"].get("efficiency") is not None:
                continue
            for k in ("energyModel", "certModel"):
                v = d.get(k)
                if not v:
                    continue
                nv = norm(v)
                if nv in have_models or nv in seen_pending:
                    continue
                seen_pending.add(nv)
                pending.append((d.get("modelHint"), v))
    if pending:
        issues.append(("采集里有能效型号但台账未收录", len(pending), pending))

    # ---- 6. 能效字段格式一致性（防止 "1 级" / "一级" / 1 混用） ----
    bad_eff = []
    for m in machines:
        if "efficiency" in m and m["efficiency"] is not None:
            if not isinstance(m["efficiency"], int) or m["efficiency"] not in (1, 2, 3):
                bad_eff.append((m.get("name"), m["efficiency"]))
    if bad_eff:
        issues.append(("efficiency 不是结构化数值（1/2/3），会导致筛选失效", len(bad_eff), bad_eff))

    # 输出
    print("=" * 62)
    print("跨源一致性校验")
    print("=" * 62)
    print(f"machine_pool            {len(machines)} 台  （推荐工具的唯一数据源）")
    print(f"specs.records           {len(specs['records'])} 台  （规格可追溯）")
    print(f"prices.offers           {len(prices['offers'])} 条  （涨价分析老样本，含手机/DIY）")
    print(f"ledger.records          {len(led['records'])} 条  （能效备案）")
    print(f"efficiency 已回写        {sum(1 for m in machines if m.get('efficiency'))} 台（在机型池）")
    print()

    # 区分两类问题：
    #   ERROR —— 数据互相矛盾，属于真 bug，必须修（退出码 1）
    #   GAP   —— 数据缺口，是已知待办，不算错误（退出码 0）
    ERROR_TITLES = ("指向了不存在的 id", "回写值与备案不符", "存在字段残缺记录", "不是结构化数值", "只写在")
    errors = [i for i in issues if any(t in i[0] for t in ERROR_TITLES)]
    gaps = [i for i in issues if i not in errors]

    if errors:
        print("❌ 数据矛盾（必须修）")
        for title, n, detail in errors:
            print(f"   {title}：{n} 项")
            for d in detail[:8]:
                print(f"      - {d}")
    if gaps:
        print("📋 数据缺口（已知待办，不是错误）")
        for title, n, detail in gaps:
            print(f"   {title}：{n} 项")
            for d in detail[:6]:
                print(f"      - {d if isinstance(d, str) else d}")
            if n > 6:
                print(f"      …… 另 {n - 6} 项")
    if not issues:
        print("✅ 未发现跨源不一致")

    print()
    print("退出码含义：0 = 无矛盾（缺口不算错）／1 = 存在数据矛盾")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())