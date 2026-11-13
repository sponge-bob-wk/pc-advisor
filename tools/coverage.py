#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
coverage.py —— 重算「专业组 × 机型」可出结论率

为什么要单独一个工具：
  85/280 这个数字以前是手算的，写在 specs_verified.json 的 meta 里。
  手算的数字复现不了，也回答不了「补了 X 之后变成多少」。

判定分两层（这是 field_matrix.json 的核心设计，不能混）：
  R 级字段  —— 回答「这台机器够不够这个专业用」。缺任一项 → 该对无法出结论。
  P 级字段  —— 只回答「能补多少钱」。缺了不影响"够不够用"，只是国补金额算不准。

三条写死在代码里的规则（都是踩过的坑）：
  1. 核显遇到显存要求 = 事实不满足（FAIL），不是"缺数据"
     核显共享系统内存，物理上没有独立显存，这是确定判断。
  2. throttle 缺失 = 降级（DEGRADED），不阻断
     峰值跑分仍可参考，只是不能保证持续性能。
  3. efficiency 绝不进门槛（它是 P 级）
     否则 10 个组会全部被能效卡死，得出"0 台可用"的假象。

输出 data/coverage.json，可随时复算。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_judge import judge as _judge, r_fields_of, present  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def judge(m, req, r_fields, hard_gates=None):
    """转发到 lib_judge.judge —— 判定逻辑只有一份实现。

    这里保留同名包装，是为了让本文件的可读性不变；
    真正的规则在 lib_judge.py，pick.py 用的是同一个函数。
    """
    v, why, _missing = _judge(m, req, r_fields, hard_gates)
    return v, why


def main():
    # 覆盖率按「机型池」算，不再只按 specs —— 池子里有价格的那批才是真正能推荐的
    pool = load("machine_pool.json")["machines"]
    majors = load("majors.json")
    fmat = load("field_matrix.json")
    markets = {g["id"]: g for g in fmat["groups"]}
    matrix = fmat["matrix"]
    grp_by_id = {g["id"]: g for g in majors["groups"]}
    req_by_gid = {k: (v.get("req") or {}) for k, v in grp_by_id.items()}
    gates_by_gid = {k: (v.get("hardGates") or []) for k, v in grp_by_id.items()}

    machines = pool
    total = ok = deg = viol = unk = 0
    rows = []

    for gid, cells in matrix.items():
        r_fields = r_fields_of(matrix, gid)
        gname = markets.get(gid, {}).get("name", gid)
        row = {"id": gid, "name": gname, "rFields": r_fields,
               "ok": 0, "degraded": 0, "violate": 0, "unknown": 0, "blockedBy": {}}
        for m in machines:
            total += 1
            verdict, why = judge(m, req_by_gid.get(gid, {}), r_fields, gates_by_gid.get(gid))
            if verdict == "OK":
                ok += 1
                row["ok"] += 1
            elif verdict == "DEGRADED":
                deg += 1
                row["degraded"] += 1
            elif verdict == "VIOLATE":
                viol += 1
                row["violate"] += 1
            else:
                unk += 1
                row["unknown"] += 1
                for w in why:
                    k = w.replace("缺 ", "")
                    row["blockedBy"][k] = row["blockedBy"].get(k, 0) + 1
        rows.append(row)

    usable = ok + deg + viol   # 能出结论 = 够用 / 降级够用 / 明确不够用
    recommendable = ok + deg   # 能推荐 = 够用 / 降级够用
    print("=" * 74)
    print(f"能出结论   {usable}/{total} = {usable/total*100:.1f}%   （含「明确不够用」，那也是一个可靠结论）")
    print(f"能推荐     {recommendable}/{total} = {recommendable/total*100:.1f}%   （只算够用与降级够用）")
    print(f"           严格可出 {ok} · 降级可出 {deg} · 明确不满足 {viol} · 数据不足 {unk}")
    print("=" * 74)
    print(f"{'专业组':20} {'够用':>4} {'降级':>4} {'不够':>4} {'未知':>4}   卡在哪个字段")
    for r in rows:
        blockers = sorted(r["blockedBy"].items(), key=lambda x: -x[1])[:3]
        btxt = ", ".join(f"{k}×{v}" for k, v in blockers) or "-"
        print(f"{r['name'][:18]:20} {r['ok']:>4} {r['degraded']:>4} {r['violate']:>4} {r['unknown']:>4}   {btxt}")
    print()
    print("口径说明（数字会随口径变，所以必须写死）：")
    print(f"  · 「明确不满足」{viol} 对 是结论，不是缺口——核显遇到显存要求就是不够用，不该算作未知")
    print(f"  · 「数据不足」{unk} 对 才是真缺口——补字段能救，卡点见上表")
    print(f"  · 能效等级缺失 {sum(1 for m in machines if not present(m,'efficiency'))} 台：P 级，不影响上表结论，只影响国补金额")
    print(f"  · 机型池 {len(machines)} 台（含 {sum(1 for m in machines if not m.get('price'))} 台无价格）；规模与早前 29 台不同，比率不可直接对比")

    out = ROOT / "data" / "coverage.json"
    out.write_text(json.dumps({
        "meta": {
            "updated": "2026-11-09",
            "scope": "machine_pool.json（规格可追溯 + 平台实采价格合并后的机型池）",
            "rule": "按 field_matrix R 级判定；核显遇显存要求=事实不满足；throttle 缺失=降级；efficiency 为 P 级不进门槛；hardGates 参与判定",
            "legend": {"OK": "R 级齐全且无违反", "DEGRADED": "R 级齐全但关键 H 缺，结论需标注", "VIOLATE": "已确定不满足", "UNKNOWN": "R 级字段缺失"},
        },
        "totals": {"pairs": total, "ok": ok, "degraded": deg, "violate": viol, "unknown": unk,
                   "usable": usable, "recommendable": recommendable,
                   "usableRate": round(usable / total, 4) if total else None,
                   "recommendRate": round(recommendable / total, 4) if total else None},
        "byGroup": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()