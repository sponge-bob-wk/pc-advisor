#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
match_energy.py —— 用「能效网规格型号」到能效台账里做精确匹配

数据源要分清：
  energyModel / certModel 存在于 data/crawl/*.jsonl 的采集记录里（京东商品详情区块抄来的）
  efficiency 存在于 data/specs_verified.json 的机型记录里
  两者靠 modelHint 关联

匹配规则（严格）：
  只有 norm(energyModel) == norm(备案型号) 才算 EXACT。
  不做任何片段匹配——「Pro」「Air」这类词元会命中整个系列，属于假匹配。

输出：
  data/efficiency_match_report.json  匹配明细
  并把 EXACT 命中的 efficiency / efficiencyEvidence 回写到 specs_verified.json
"""
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRAWL = ROOT / "data" / "crawl"
SPECS = ROOT / "data" / "specs_verified.json"
LEDGER = ROOT / "data" / "efficiency_ledger.json"
REPORT = ROOT / "data" / "efficiency_match_report.json"


def norm(s):
    if not s:
        return ""
    s = s.replace("　", " ").translate(str.maketrans("（）－—", "()--"))
    return re.sub(r"\s+", "", s).lower()


def norm_hint(s):
    """机型名归一化：去掉括号里的配置串、空格、年份后缀

    采集记录的 modelHint 常写成「拯救者 Y7000P 2026（Ultra7-251HX/RTX5060）」，
    规格库写成「拯救者 Y7000P 2026」——两者指的是同一台，必须能认出是同一台。
    """
    if not s:
        return ""
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = s.replace("　", " ").translate(str.maketrans("（）－—", "()--"))
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"(20\d{2})(款)?$", "", s)
    return s.lower()


def load_crawl_energy():
    """从所有 jsonl 收 energyModel/certModel，按 modelHint 聚合"""
    by_hint = {}
    for f in sorted(CRAWL.glob("*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            em, cm = d.get("energyModel"), d.get("certModel")
            if not em and not cm:
                continue
            hint = d.get("modelHint") or ""
            key = norm_hint(hint)
            slot = by_hint.setdefault(key, {"modelHint": hint, "energyModel": None,
                                            "certModel": None, "srcFiles": []})
            slot["energyModel"] = slot["energyModel"] or em
            slot["certModel"] = slot["certModel"] or cm
            if f.name not in slot["srcFiles"]:
                slot["srcFiles"].append(f.name)
    return by_hint


def main():
    led = json.loads(LEDGER.read_text(encoding="utf-8"))
    spec = json.loads(SPECS.read_text(encoding="utf-8"))
    crawl = load_crawl_energy()

    # 备案型号索引
    idx = {}
    for r in led["records"]:
        idx.setdefault(norm(r["model"]), []).append(r)

    items, exact_n = [], 0
    for m in spec["records"]:
        hint = m.get("modelHint", "")
        c = crawl.get(norm_hint(hint), {})
        em = m.get("energyModel") or c.get("energyModel")
        cm = m.get("certModel") or c.get("certModel")

        hits = []
        for key in (em, cm):
            if key:
                hits.extend(idx.get(norm(key), []))
        # 去重
        seen, uniq = set(), []
        for h in hits:
            if h["regNo"] not in seen:
                seen.add(h["regNo"])
                uniq.append(h)

        status = "EXACT" if uniq else ("HAS_ID_NO_HIT" if (em or cm) else "NO_CERT_ID")
        if uniq:
            exact_n += 1
            levels = sorted({h["level"] for h in uniq})
            m["efficiency"] = f"{levels[0]} 级" if len(levels) == 1 else f"多档 {levels}"
            m["efficiencyEvidence"] = {
                "energyModel": em,
                "certModel": cm,
                "filings": [{"model": h["model"], "level": h["level"],
                             "regNo": h["regNo"], "date": h["date"],
                             "scheme": h["regScheme"]} for h in uniq],
                "source": "中国能效标识网 产品备案查询",
                "checkedAt": "2026-11-09",
            }
        items.append({
            "modelHint": hint,
            "energyModel": em,
            "certModel": cm,
            "status": status,
            "filings": [{"model": h["model"], "level": h["level"],
                         "regNo": h["regNo"], "date": h["date"],
                         "scheme": h["regScheme"]} for h in uniq],
        })

    stats = {}
    for it in items:
        stats[it["status"]] = stats.get(it["status"], 0) + 1

    print("匹配结果：", stats, f"  共 {len(items)} 台")
    for it in items:
        if it["status"] == "EXACT":
            lv = [f["level"] for f in it["filings"]]
            print(f"  ✅ {it['modelHint'][:30]:32} {it['energyModel'] or it['certModel']:26} 等级={sorted(set(lv))} {len(lv)}条备案")
    print("\n--- 有型号但查不到（需复查型号是否抄错）---")
    for it in items:
        if it["status"] == "HAS_ID_NO_HIT":
            print(f"  ⚠️  {it['modelHint'][:30]:32} {it['energyModel'] or it['certModel']}")

    REPORT.write_text(json.dumps({
        "meta": {
            "updated": "2026-11-09",
            "rule": "EXACT 要求 norm(能源网规格型号) 与备案型号严格相等；不做片段匹配",
            "why": "营销名里的 Pro/Air/Plus 会命中整个系列，属假匹配，比不匹配更危险",
            "statusMeaning": {
                "EXACT": "已核实到备案，efficiency 已回写 specs_verified.json",
                "HAS_ID_NO_HIT": "采集数据有能效型号，但在已查询范围内查不到，需复查型号/换 2012 版再查",
                "NO_CERT_ID": "采集数据没有能效网规格型号，无法查——这才是主要瓶颈",
            },
        },
        "stats": stats,
        "items": items,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 备份后回写 specs
    bak = SPECS.with_suffix(".json.bak_eff")
    if not bak.exists():
        shutil.copy2(SPECS, bak)
    SPECS.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {REPORT.name}  and updated {SPECS.name} (backup: {bak.name})")


if __name__ == "__main__":
    main()