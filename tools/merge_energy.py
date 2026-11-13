#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
merge_energy.py —— 把 energy_raw/*.txt 合并解析成 efficiency_ledger.json

产出两层：
  1. records[]  逐条备案（型号 / 等级 / 备案号 / 日期 / 备案体系）
  2. summary    按体系、按等级、按年份的分布，用于国补收益估算
"""
import json
import re
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "crawl" / "energy_raw"
OUT = ROOT / "data" / "efficiency_ledger.json"

import sys
sys.path.insert(0, str(ROOT / "tools"))
from parse_energy_raw import parse  # noqa: E402


def main():
    files = sorted(RAW.glob("*.txt"))
    records = []
    per_file = []
    for f in files:
        txt = f.read_text(encoding="utf-8-sig")
        recs = parse(txt)
        m = re.search(r"共\s*(\d+)\s*条", txt)
        total = int(m.group(1)) if m else None
        # 查询关键词：产品型号框里的值
        km = re.search(r"产品型号\r?\n([^\r\n]+)", txt) or re.search(r"产品型号\n([^\n]+)", txt)
        kw = km.group(1).strip() if km else None
        # 生产者框
        pm = re.search(r"生产者\r?\n([^\r\n]+)", txt)
        producer_kw = pm.group(1).strip() if pm else None
        per_file.append({
            "file": f.name, "queryKeyword": kw, "producerKeyword": producer_kw,
            "parsed": len(recs), "totalOnSite": total,
            "complete": total is not None and len(recs) >= total,
            # 「暂无数据」是有效的否定结论，必须记下来，否则以后会重复查
            "noData": ("暂无数据" in txt) or (total == 0),
        })
        for r in recs:
            r["srcFile"] = f.name
            r["queryKeyword"] = kw
            records.append(r)

    # 去重：同备案号保留一条
    by_reg = {}
    for r in records:
        by_reg.setdefault(r["regNo"], r)
    uniq = list(by_reg.values())

    lvl = Counter(r["level"] for r in uniq)
    scheme = Counter(r["regScheme"] for r in uniq)
    lvl_by_scheme = defaultdict(Counter)
    for r in uniq:
        lvl_by_scheme[r["regScheme"]][r["level"]] += 1
    year = Counter((r["date"] or "")[:4] for r in uniq)

    lvl1 = lvl.get(1, 0)
    n = sum(lvl.values())
    ledger = {
        "meta": {
            "version": "0.1",
            "updated": "2026-11-09",
            "source": "中国能效标识网 产品备案查询 https://www.energylabel.com.cn/productFiling",
            "method": "页面内 Ctrl+A / Ctrl+C 复制表格 → 系统剪贴板 → parse_energy_raw.py 解析；不调用任何接口",
            "note": "查询必须先把「产品类型」限定为 办公电子 / 微型计算机，否则混入显示器、服务器、打印机",
        },
        "files": per_file,
        "summary": {
            "uniqueRecords": len(uniq),
            "levelDistribution": dict(sorted(lvl.items())),
            "level1Ratio": round(lvl1 / n, 3) if n else None,
            "byRegScheme": {str(k): dict(v) for k, v in lvl_by_scheme.items()},
            "byYear": dict(sorted(year.items())),
        },
        "records": sorted(uniq, key=lambda r: (r["regScheme"] or 0, r["date"] or ""), reverse=True),
    }
    OUT.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}  records={len(uniq)}")
    print("files:", [(f['file'], f['parsed'], f['totalOnSite']) for f in per_file])
    nodata = [f["queryKeyword"] for f in per_file if f.get("noData")]
    if nodata:
        print("查无数据的型号（否定结论，不必重复查）:", nodata)
    print("level:", dict(lvl), " level1Ratio:", ledger["summary"]["level1Ratio"])
    print("byScheme:", ledger["summary"]["byRegScheme"])


if __name__ == "__main__":
    main()