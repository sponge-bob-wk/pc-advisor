#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
陷阱库自检 + 数据缺口体检。

用法：
    python tools/audit_data.py

它回答两个问题：
1. 陷阱库本身是否可信（有没有重复 id、有没有声称自动却没落地）
2. 这 29 条坑里，现在真正拦得住几条？拦不住的，数据缺在哪、缺多少？

输出三段：已自动拦截 / 数据缺口（可量化）/ 只能靠人。
"""

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "data"

REQ = ("id", "category", "severity", "automated", "title", "symptom",
       "cause", "detection", "fix", "where", "affects")
SEV = {"blocker", "high", "medium"}
CATS = {"bench", "price", "spec", "source", "logic"}

errs, warns = [], []


def load(name):
    f = P / name
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def main():
    pf = load("pitfalls.json")
    if not pf:
        print("找不到 data/pitfalls.json")
        return 1

    items = pf["pitfalls"]

    # ---------- 陷阱库自检 ----------
    ids = set()
    sev_count = {}
    for it in items:
        for f in REQ:
            # automated 是布尔字段，false 是合法值 —— 不能用真值判断，否则 false 会被当成没填
            if f not in it:
                errs.append("%s 缺字段 %s" % (it.get("id", "?"), f))
            elif f != "automated" and not it.get(f):
                errs.append("%s 的 %s 是空的" % (it.get("id", "?"), f))
        if it["id"] in ids:
            errs.append("id 重复：" + it["id"])
        ids.add(it["id"])
        if it["severity"] not in SEV:
            errs.append("%s 的 severity 不认识：%s" % (it["id"], it["severity"]))
        if it["category"] not in CATS:
            errs.append("%s 的 category 不认识：%s" % (it["id"], it["category"]))
        sev_count[it["severity"]] = sev_count.get(it["severity"], 0) + 1
        # 声称自动化的，落地的文件必须存在
        if it["automated"]:
            w = it["where"].split(" / ")[0].split(" ")[0].strip()
            if not (ROOT / w).exists() and not (P / w).exists():
                errs.append("%s 声称已自动化，但 %s 不存在" % (it["id"], w))

    auto = [x for x in items if x["automated"]]
    manual = [x for x in items if not x["automated"]]

    # 声明的统计数必须和实际一致，否则文档会骗人
    declared = pf.get("meta", {}).get("stats", {})
    if declared.get("total") is not None and declared["total"] != len(items):
        errs.append("meta.stats.total 写的是 %s，实际 %d 条" % (declared["total"], len(items)))
    if declared.get("automated") is not None and declared["automated"] != len(auto):
        errs.append("meta.stats.automated 写的是 %s，实际 %d 条" % (declared["automated"], len(auto)))

    print("=" * 62)
    print("陷阱库  %d 条  ·  blocker %d / high %d / medium %d"
          % (len(items), sev_count.get("blocker", 0),
             sev_count.get("high", 0), sev_count.get("medium", 0)))
    print("已自动拦截 %d 条  ·  依赖人工 %d 条  ·  自动化率 %.0f%%"
          % (len(auto), len(manual), 100.0 * len(auto) / len(items)))
    print("=" * 62)

    # ---------- 数据缺口体检 ----------
    gaps = []
    price = load("prices.json")
    bench = load("bench.json")
    cat = load("catalog.json")
    maj = load("majors.json")

    # ---------- 风险库自检 ----------
    rk = load("risks.json")
    if rk:
        rk_errs = []
        tier_ids = {t["id"] for t in rk.get("brandTiers", [])}

        for d in rk.get("designDefects", []):
            for f in ("id", "name", "tier", "scope", "symptom", "cause",
                      "repair", "verify", "decision"):
                if not d.get(f):
                    rk_errs.append("缺陷 %s 缺 %s" % (d.get("id", "?"), f))
            if d.get("tier") not in ("A", "B", "C"):
                rk_errs.append("缺陷 %s 的 tier 不认识：%s" % (d.get("id"), d.get("tier")))
            if d.get("scope") not in ("series", "model"):
                rk_errs.append("缺陷 %s 的 scope 不认识：%s" % (d.get("id"), d.get("scope")))

        for q in rk.get("qcIssues", []):
            # 品控条目没有「到手怎么验」和「怎么兜」就是空话
            for f in ("id", "name", "nature", "buyerCheck", "mitigate"):
                if not q.get(f):
                    rk_errs.append("品控 %s 缺 %s" % (q.get("id", "?"), f))

        for a in rk.get("afterSales", []):
            if a.get("tier") not in tier_ids:
                rk_errs.append("售后 %s 的 tier 不存在：%s" % (a.get("brand"), a.get("tier")))
            try:
                if not 1 <= int(a.get("network", 0)) <= 5:
                    rk_errs.append("售后 %s 的 network 要在 1-5" % a.get("brand"))
            except (TypeError, ValueError):
                rk_errs.append("售后 %s 的 network 不是数字" % a.get("brand"))

        # 售后里的品牌若不在价格库里，就是空对空，推不到任何机型
        if price:
            pcb = {b["name"] for b in price["brands"] if (b.get("scope") or "pc") == "pc"}
            for a in rk.get("afterSales", []):
                if a.get("notInOffers"):
                    continue
                if a["brand"] not in pcb:
                    rk_errs.append("售后品牌「%s」不在 prices.brands 里" % a["brand"])

        for r in rk.get("decisionRules", []):
            for f in ("id", "when", "then", "why"):
                if not r.get(f):
                    rk_errs.append("规则 %s 缺 %s" % (r.get("id", "?"), f))

        errs.extend(rk_errs)
        n_c = sum(1 for d in rk.get("designDefects", []) if d.get("tier") == "C")
        print("\n风险库")
        print("  设计缺陷 %d 条（其中 C 级待核实 %d） · 品控 %d 条 · 售后 %d 家 · 决策规则 %d 条 · 概念纠正 %d 条"
              % (len(rk.get("designDefects", [])), n_c,
                 len(rk.get("qcIssues", [])), len(rk.get("afterSales", [])),
                 len(rk.get("decisionRules", [])), len(rk.get("conceptWarnings", []))))
        cov = {}
        for a in rk.get("afterSales", []):
            cov[a["tier"]] = cov.get(a["tier"], 0) + 1
        print("  档次覆盖：" + " · ".join("%s %d 家" % (t["name"], cov.get(t["id"], 0))
                                        for t in rk.get("brandTiers", [])))

    if price:
        offers = price["offers"]
        pc = [o for o in offers if o.get("cb23")]
        phones = [o for o in offers if o.get("gb6")]

        if phones:
            gaps.append(("bench-gb-version-mix", len(phones),
                         "手机 %d 台只有 gb6 字段，无版本标签；GB7 迁入后不可比" % len(phones)))

        no_ramtype = [o for o in pc if "ramType" not in o]
        if no_ramtype:
            gaps.append(("spec-ram-soldered", len(no_ramtype),
                         "%d 台电脑缺 ramType（板载 / 插槽），内存焊死只能靠文案判断" % len(no_ramtype)))

        no_eff = [o for o in pc if "efficiency" not in o]
        if no_eff:
            gaps.append(("price-efficiency-gate", len(no_eff),
                         "%d 台电脑缺 efficiency（能效等级），国补前置条件无法校验" % len(no_eff)))

        if "throttle" not in offers[0]:
            gaps.append(("bench-throttle", len(pc),
                         "%d 台电脑无 throttle（10 轮衰减），峰值当成了持续性能" % len(pc)))

        sm = price.get("sameModel") or []
        no_date = [s for s in sm if not any(k in s for k in ("date", "from", "to", "asOf"))]
        if sm and no_date:
            gaps.append(("price-final-vs-net", len(no_date),
                         "sameModel %d 条无日期，无法判断涨幅是否跨政策期" % len(no_date)))

        basis = str(price.get("meta", {}).get("priceBasis", ""))
        if "国补" in basis and "净价" not in basis:
            gaps.append(("price-coupon-vs-list", len(offers),
                         "现价格口径为「含国补到手价」，涨幅与比价都混入了政策变量"))

        up = price.get("meta", {}).get("updated")
        if up:
            try:
                y, m, d = [int(x) for x in up.split("-")]
                age = (date.today() - date(y, m, d)).days
                if age > 7:
                    gaps.append(("source-official-no-price", age,
                                 "价格数据已 %d 天未更新（阈值 7 天）" % age))
            except ValueError:
                pass

    if bench:
        gpus = bench.get("gpus") or []
        unver = [g for g in all_cpus(bench) if not g.get("verified")]
        gaps.append(("bench-tgp", len(gpus),
                     "GPU 表仅 %d 条参考样本，且都无机型级 tgpW；型号名不足以建库" % len(gpus)))
        gaps.append(("source-no-score-db", len(unver),
                     "CPU 基准 %d 条全部 verified=false，均未自测复现" % len(unver)))

    if cat:
        es = cat["entries"]
        no_gamut = [e for e in es if not e.get("colorGamut")]
        if no_gamut:
            gaps.append(("spec-screen-missing", len(no_gamut),
                         "种子库 %d/%d 条缺色域与 ΔE，平面组无法校验屏幕" % (len(no_gamut), len(es))))
        series = [e for e in es if e.get("granularity") == "series"]
        if series:
            gaps.append(("source-official-no-price", len(series),
                         "%d 条只到系列级，无法落到具体 SKU" % len(series)))

    if maj:
        jud = [g for g in maj["groups"] if g["evidence"] == "judgment"]
        gaps.append(("logic-single-score", len(jud),
                     "%d 个专业组的门槛是人写判断，非官方依据：%s"
                     % (len(jud), "、".join(g["name"] for g in jud))))

    if gaps:
        print("\n数据缺口（可量化，%d 项）" % len(gaps))
        for pid, n, msg in sorted(gaps, key=lambda x: -x[1]):
            it = next((x for x in items if x["id"] == pid), None)
            tag = {True: "已拦", False: "待建"}.get(it["automated"] if it else False, "待建")
            print("  [%s] %-26s %s" % (tag, pid, msg))

    # ---------- 只能靠人的 ----------
    print("\n只能靠人（无法从数据检测，%d 条）" % len(manual))
    by_cat = {}
    for it in manual:
        by_cat.setdefault(it["category"], []).append(it)
    for c in ("bench", "price", "spec", "source", "logic"):
        if c not in by_cat:
            continue
        print("  %s（%d）" % (c, len(by_cat[c])))
        for it in by_cat[c]:
            print("    - %s  %s" % (it["severity"][:4].ljust(4), it["title"]))

    # ---------- 结论 ----------
    print("\n" + "=" * 62)
    if errs:
        for e in errs:
            print("  [FAIL] " + e)
        return 1
    print("陷阱库自检通过。")
    print("提醒：自动化率 %.0f%% 意味着绝大多数坑仍靠人守，"
          "把 blocker 级的 %d 条做成硬闸门是接下来性价比最高的事。"
          % (100.0 * len(auto) / len(items), sev_count.get("blocker", 0)))
    return 0


def all_cpus(bench):
    return bench.get("cpus") or []


if __name__ == "__main__":
    sys.exit(main())
