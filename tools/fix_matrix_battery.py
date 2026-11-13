#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fix_matrix_battery.py —— batteryWh 从 R 降到 H（改一次，幂等）

理由（详见 字段矩阵复查_batteryWh.md）：
1. Wh 是续航的差代理。同样 60Wh，配 15W 低压 U 能撑 10 小时，配 55W 标压 H 只有 3-4 小时。
   库里就有反例：小新 Air15 是 54.7Wh+低压，ThinkBook 16+ 是 99.9Wh+标压。
   设 Wh 门槛会把真·长续航机筛掉。
2. R 级语义是「缺了结论不成立」。但续航差是缺点，不是「不够用」——
   34Wh 的机器结论是「要带充电器」，不是「不能用」。这是权衡，用 trap 表达即可。
3. 真正决定续航的是 Wh / 平均功耗，而平均功耗没有厂商会给，只能靠实测（PCMark/MobileMark），
   那是 H 级信息。

改完影响：office 组卡点从 batteryWh×21 归零；code 组变成 ramType×19。
"""
import json
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "data" / "field_matrix.json"
TARGETS = [("office", "batteryWh", "R", "H"), ("code", "batteryWh", "R", "H")]


def main():
    f = json.loads(P.read_text(encoding="utf-8"))
    changed = []
    for gid, field, old, new in TARGETS:
        cell = f["matrix"][gid]
        if cell.get(field) == old:
            # 把原 R 的理由留存到 fields 的 why 里不需要；单独记一笔变更
            cell[field] = new
            changed.append((gid, field, old, new))

    if changed:
        f["meta"]["updated"] = "2026-11-09"
        f["meta"].setdefault("changes", []).append({
            "date": "2026-11-09",
            "what": "batteryWh 在 office / code 两组由 R 降为 H",
            "why": "Wh 不能代理续航（低压小电池可胜过标压大电池）；且续航短属缺点而非不够用，不应一票否决",
            "effect": "office 卡点 batteryWh×21 → 0；code 卡点 → ramType×19",
        })
        P.write_text(json.dumps(f, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"修改 {len(changed)} 处")
    for gid, field, old, new in changed:
        print(f"  {gid}.{field}: {old} -> {new}")
    if not changed:
        print("已是最新，无需改动")

    # 立即验证
    for gid, _f, _o, _n in TARGETS:
        r = [k for k, v in f["matrix"][gid].items() if v == "R"]
        print(f"  {gid} 新的 R 级字段: {r}")


if __name__ == "__main__":
    main()