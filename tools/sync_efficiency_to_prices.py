#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sync_efficiency_to_prices.py —— 把已核实的能效等级同步进 prices.json

为什么需要这一步：
  能效是在 specs_verified.json 里核实的（它是个规格属性，来源是能效标识网备案），
  但国补金额是在 prices.json 里算的（offers[] 带价格）。
  两个文件不连通 → audit_data.py 看 prices 报「18 台缺能效」，而 specs 里明明已经填了，
  于是「已完成」和「仍缺」同时成立，谁都说不清真实进度。

同步规则：
  1. 只同步置信度不为 low 的桥接
  2. 只写 efficiency 与 efficiencyEvidence 两个字段，不动价格
  3. 写入带 source 标记，方便追溯是哪个备案号
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRICES = ROOT / "data" / "prices.json"
SPECS = ROOT / "data" / "specs_verified.json"
BRIDGE = ROOT / "data" / "model_bridge.json"


def main():
    prices = json.loads(PRICES.read_text(encoding="utf-8"))
    specs = json.loads(SPECS.read_text(encoding="utf-8"))
    bridge = json.loads(BRIDGE.read_text(encoding="utf-8"))

    spec_by_id = {m["id"]: m for m in specs["records"]}
    price_by_id = {o["id"]: o for o in prices["offers"]}

    synced, skipped = [], []
    for b in bridge["bridges"]:
        pid, sid = b["priceId"], b["specId"]
        if pid not in price_by_id or sid not in spec_by_id:
            skipped.append((pid, "桥接目标不存在"))
            continue
        if b.get("confidence") == "low":
            skipped.append((pid, "置信度 low，不自动同步"))
            continue
        m = spec_by_id[sid]
        eff = m.get("efficiency")
        if not eff:
            skipped.append((pid, "规格侧无 efficiency"))
            continue
        o = price_by_id[pid]
        o["efficiency"] = eff
        o["efficiencyEvidence"] = {
            "from": sid,
            "energyModel": (m.get("efficiencyEvidence") or {}).get("energyModel"),
            "filings": (m.get("efficiencyEvidence") or {}).get("filings", []),
            "source": "中国能效标识网 产品备案查询",
            "syncedAt": "2026-11-09",
        }
        synced.append((o["name"], eff))

    bak = PRICES.with_suffix(".json.bak_effsync")
    if not bak.exists():
        shutil.copy2(PRICES, bak)
    PRICES.write_text(json.dumps(prices, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"同步完成：{len(synced)} 台写入 prices.json")
    for name, eff in synced:
        print(f"  ✅ {name}  ->  {eff}")
    if skipped:
        print(f"\n跳过 {len(skipped)} 条：")
        for pid, why in skipped:
            print(f"  - {pid}: {why}")

    total_pc = sum(1 for o in prices["offers"] if o.get("cat") in ("thin", "gaming", "desktop"))
    have = sum(1 for o in prices["offers"]
               if o.get("cat") in ("thin", "gaming", "desktop") and o.get("efficiency"))
    print(f"\nprices 电脑类能效覆盖：{have}/{total_pc}")


if __name__ == "__main__":
    main()