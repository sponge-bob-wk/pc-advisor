#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gaps.py —— 把「还差什么数据」算成一张待办清单

为什么要单独算这个：

  「缺 19 个 ramType」这种说法没有行动价值。真正要回答的是三个问题：
    1. 补哪一项，能让最多专业组从「判不了」变成「判得了」？（杠杆率）
    2. 这一项到底是真的没采，还是查过确实没有？（数据缺口 vs 否定结论）
    3. 补完之后能救几台机器？值不值得花时间去补？

  优先级不能按「缺得多」排，要按「补一项救几个组×几台机」排。
  补 19 台 ramType 能救编程组；补 1 个字段 throttle 能救视频+引擎两个组 ——
  后者更值，哪怕它只需要一次采集模板的改动。

输出 data/gaps.json，并打印按杠杆率排序的待办。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_judge import field_present, is_integrated, judge, r_fields_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

FIELD_WORD = {
    "cpu": "CPU 型号", "cores": "CPU 核心数", "ramGB": "内存容量",
    "ramType": "内存形态/类型", "storageGB": "存储容量", "gpuModel": "显卡型号",
    "vramGB": "显存容量", "tgpW": "显卡功耗释放", "screenInch": "屏幕尺寸",
    "resolution": "分辨率", "refreshHz": "刷新率", "colorGamut": "色域",
    "brightnessNit": "屏幕亮度", "weightKg": "重量", "batteryWh": "电池容量",
    "ports": "接口", "efficiency": "能效等级", "throttle": "持续性能衰减",
    "modelHint（年份）": "型号年份", "modelHint": "型号命名",
}


def load(n):
    return json.loads((DATA / n).read_text(encoding="utf-8"))


def main():
    pool = load("machine_pool.json")["machines"]
    fmat = load("field_matrix.json")
    majors = load("majors.json")
    nodata = {}
    p = DATA / "efficiency_nodata.json"
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        # 实际键名是 noData（不是 records）。写错键名不会报错，
        # 只会静默拿到空列表 —— 那类 bug 最难发现，所以这里两种都兼容。
        for item in raw.get("noData") or raw.get("records") or []:
            nodata[item.get("query")] = item

    # 每个 R 级字段：缺在哪些组、缺在哪些机
    # 关键：核显机的 vramGB 不是「缺数据」，是「事实不满足」。
    # 统计缺口时若把它算进缺失，会得出「补 27 台显存就能救 4 个组」的错误结论 ——
    # 实际上核显补不出显存来，那些机器永远是"不够用"，不是"判不了"。
    # 所以这里只看「判定结果真的是 UNKNOWN」的机器。
    req_by_gid = {g["id"]: (g.get("req") or {}) for g in majors["groups"]}
    gates_by_gid = {g["id"]: (g.get("hardGates") or []) for g in majors["groups"]}

    by_field = {}
    for gid, cells in fmat["matrix"].items():
        rf = r_fields_of(fmat["matrix"], gid)
        for f in rf:
            hit = []
            for m in pool:
                if field_present(m, f)[0]:
                    continue
                # 只在「这一项缺失正是判定卡住的原因」时才算缺口
                v, _why, _miss = judge(m, req_by_gid.get(gid, {}), rf, gates_by_gid.get(gid))
                if v != "UNKNOWN":
                    continue  # 已经能出结论（含"不够用"），不算缺口
                hit.append(m["name"])
            if not hit:
                continue
            rec = by_field.setdefault(f, {"field": f, "name": FIELD_WORD.get(f, f),
                                          "groups": [], "machines": []})
            if gid not in rec["groups"]:
                rec["groups"].append(gid)
            rec["machines"].extend(hit)

    # 去重并算杠杆：一个字段能救多少个「组 × 机」组合
    rows = []
    for f, rec in by_field.items():
        rec["machines"] = sorted(dict.fromkeys(rec["machines"]))
        rec["groupCount"] = len(rec["groups"])
        rec["machineCount"] = len(rec["machines"])
        # 杠杆率 = 组数 × 台数。补一次，能解锁的判定组合数。
        rec["leverage"] = rec["groupCount"] * rec["machineCount"]
        rows.append(rec)
    rows.sort(key=lambda r: -r["leverage"])

    # 非 R 级但影响国补的字段（P 级）单独列，避免和"判不了"混在一起
    eff_missing = [m["name"] for m in pool if not field_present(m, "efficiency")[0]]
    no_price = [m["name"] for m in pool if not ((m.get("price") or {}).get("net")
                                                or (m.get("price") or {}).get("final"))]

    print("=" * 78)
    print("数据缺口待办（按杠杆率排序：补一项能解锁多少「专业组 × 机型」判定）")
    print("=" * 78)
    print()
    print(f"{'字段':16}{'卡住的组':>8}{'缺的机型':>8}{'杠杆':>7}   影响的组")
    for r in rows:
        gnames = ",".join(r["groups"])
        print(f"{r['name']:16}{r['groupCount']:>8}{r['machineCount']:>8}{r['leverage']:>7}   {gnames}")

    print()
    print("─" * 78)
    print(f"另外两类缺口，不影响「够不够用」，但影响「买不买得起 / 能不能补」：")
    print(f"  · 能效等级未查  {len(eff_missing):>2} 台  →  国补资格判不了（P 级）")
    print(f"  · 没有价格     {len(no_price):>2} 台  →  进不了预算比较")
    if eff_missing:
        print(f"      能效缺的是：{'、'.join(x[:16] for x in eff_missing[:8])}")
        if len(eff_missing) > 8:
            print(f"                  …… 还有 {len(eff_missing) - 8} 台")
    if no_price:
        print(f"      无价格的是：{'、'.join(x[:16] for x in no_price)}")

    if nodata:
        print()
        print("─" * 78)
        print(f"已经查过、确实没有的（{len(nodata)} 条）—— 不要再重复查：")
        for k, v in nodata.items():
            print(f"  · {k}：{v.get('result')}（{v.get('why')}）")

    out = DATA / "gaps.json"
    out.write_text(json.dumps({
        "meta": {
            "updated": "2026-11-09",
            "purpose": "按杠杆率排序的数据缺口清单。杠杆 = 卡的组数 × 缺的台数",
            "why": "不能按「缺得多」排优先级：补 1 个 throttle 字段能解锁视频+引擎两个组，比补 19 台 ramType 更值",
        },
        "fieldGaps": rows,
        "nonBlocking": {
            "efficiencyMissing": eff_missing,
            "priceMissing": no_price,
            "note": "这两类不影响「够不够用」的判定，只影响国补金额与预算筛选",
        },
        "confirmedNoData": list(nodata.values()),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())