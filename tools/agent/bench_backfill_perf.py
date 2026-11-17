# -*- coding: utf-8 -*-
"""bench_backfill_perf.py —— 给「有原始分但没算 perfCurrent」的芯片补算

## 这是什么问题

`bench.json` 里有些芯片的 `bench.r23` 有值，但 `perfCurrent` 是 `None`。
于是 `core._pool_to_offer` 查不到分，那台机器就显示「无跑分」——
**数据其实就在库里，只是没换算**。

2026-11-16 实测到 3 颗：

    i5-14450HX   R23 1838/13881   perfCurrent = None
    Apple M4     R23 2055/12100   perfCurrent = None
    Apple M5     R23 2511/16014   perfCurrent = None

这类是"填了原始分、漏了换算"，与"真的没数据"是两件事，必须分开处理：
前者补算就行，后者才要抓数据。

## 尺子（必须与项目一致，不许另发明）

    perf = 100 × (0.5×单核/2400 + 0.5×多核/40000)

来源：`prices.json` 的 meta。`bench_add.py` 与 `bench_merge_nbc.py` 都用这一把。

## 用法

    python bench_backfill_perf.py            # 演练
    python bench_backfill_perf.py --apply    # 落盘（自动备份）
"""
import argparse
import json
import os
import shutil
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BENCH = os.path.join(ROOT, "data", "bench.json")


def perf_from_r23(single, multi):
    if not single or not multi:
        return None
    return round(100.0 * (0.5 * single / 2400.0 + 0.5 * multi / 40000.0), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    bench = json.load(open(BENCH, encoding="utf-8"))
    cpus = bench.get("cpus") or []

    todo = []
    for c in cpus:
        r23 = (c.get("bench") or {}).get("r23") or {}
        if r23.get("single") and r23.get("multi") and c.get("perfCurrent") is None:
            p = perf_from_r23(r23["single"], r23["multi"])
            todo.append((c, p))

    print("有 R23 原始分但缺 perfCurrent：%d 颗" % len(todo))
    print()
    print("%-26s %-18s %-10s %s" % ("芯片", "R23 单/多核", "补算 perf", "说明"))
    print("-" * 74)
    for c, p in todo:
        r23 = c["bench"]["r23"]
        note = ""
        if str(c.get("name", "")).lower().startswith("apple"):
            note = "Apple 芯片 perf 口径偏保守（R23 非其强项），仅供参考"
        print("%-26s %-18s %-10s %s"
              % (c.get("name", "")[:24],
                 "%s / %s" % (r23["single"], r23["multi"]), p, note))

    if args.apply and todo:
        bak = BENCH + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(BENCH, bak)
        for c, p in todo:
            c["perfCurrent"] = p
            # 留痕：说明这个分是补算的，不是新抓的
            c["perfBackfilled"] = ("由已有 bench.r23 按项目尺子补算"
                                   "（perf = 100 × (0.5×单核/2400 + 0.5×多核/40000)）")
        json.dump(bench, open(BENCH, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print()
        print("已写入 %s（备份 %s）" % (BENCH, bak))
    elif not args.apply:
        print()
        print("（演练模式。加 --apply 落盘）")


if __name__ == "__main__":
    main()
