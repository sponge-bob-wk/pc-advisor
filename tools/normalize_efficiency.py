#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
normalize_efficiency.py —— 把 efficiency 字段统一成结构化数值

问题：
  同一个字段出现过两种写法：「1 级」（来自能效网回填）、「一级」（人工填的）。
  字符串比大小会出错，筛选「一级能效」时漏掉一半，是典型的静默 bug。

目标形态：
  "efficiency": 1              ← 数值，1/2/3，可直接比较
  "efficiencyText": "一级"      ← 展示用
  "efficiencyStatus": "verified" / "pending" / "absent"
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CN = {"一": 1, "二": 2, "三": 3, "1": 1, "2": 2, "3": 3}


def parse_level(v):
    """把各种写法解析成 1/2/3；无法解析返回 None"""
    if v is None:
        return None
    if isinstance(v, int):
        return v if v in (1, 2, 3) else None
    s = str(v).strip()
    m = re.search(r"([一二三123])\s*级", s)
    if m:
        return CN.get(m.group(1))
    return None


def fix_file(path, key_name):
    data = json.loads(path.read_text(encoding="utf-8"))
    recs = data.get("records") or data.get("offers") or []
    changed = []
    for m in recs:
        old = m.get("efficiency")
        lv = parse_level(old)
        if lv is not None:
            if m.get("efficiency") != lv:
                m["efficiency"] = lv
                m["efficiencyText"] = {1: "一级", 2: "二级", 3: "三级"}[lv]
                m["efficiencyStatus"] = "verified"
                changed.append((m.get("modelHint") or m.get("name"), old, lv))
        elif old is None:
            # 没填：区分「未查」与「查过没有」
            if "efficiency" in m:
                m["efficiencyStatus"] = m.get("efficiencyStatus") or "pending"
    # 补 efficiencyStatus
    for m in recs:
        if m.get("efficiency") in (1, 2, 3):
            m.setdefault("efficiencyText", {1: "一级", 2: "二级", 3: "三级"}[m["efficiency"]])
            m["efficiencyStatus"] = "verified"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{path.name}: 规范化 {len(changed)} 条")
    for name, old, new in changed:
        print(f"  {name}  {old!r} -> {new}")
    return len(changed)


def main():
    n = 0
    n += fix_file(ROOT / "data" / "specs_verified.json", "records")
    n += fix_file(ROOT / "data" / "prices.json", "offers")
    print(f"\n合计规范化 {n} 条")


if __name__ == "__main__":
    main()