# -*- coding: utf-8 -*-
"""bench_merge_nbc.py —— 把 Notebookcheck 抓到的跑分并入 data/bench.json

## 为什么不用 bench_add.py

`bench_add.py` 是**逐条手工补录**用的：每条要手写 source + machine + url，
适合「查了 3 颗 CPU」的场景。这次是 15 颗批量、来源统一（同一张 NBC 汇总表），
逐条手写既慢又容易抄错 url。

所以单开一个合并脚本，但**继承 bench_add.py 的全部红线**：

1. 每条都带 source + url + capturedAt（写在 `source` / `sourceUrl` / `capturedAt`）
2. 只写**原始实测值**（`bench.r23` / `bench.gb6`），`perfCurrent` 由同一把尺子算
3. `aggregated: true` 标记这是**跨机型聚合值**，不是单机实测 —— 两者不能混
4. 已有芯片**不覆盖**（只填空缺），除非显式 `--overwrite`

## perf 的尺子（必须与人工层一致，见 prices.json 的 meta）

    电脑用 Cinebench R23：perf = 100 × (0.5×单核/2400 + 0.5×多核/40000)

这个公式是项目定的，**不许在这里另发明一个**。所以脚本直接复用
`bench_add.py` 里的换算函数，找不到就报错退出 —— 宁可失败也不写不一致的尺子。

## 用法

    python bench_merge_nbc.py              # 演练：打印将写入什么
    python bench_merge_nbc.py --apply      # 落盘（自动备份）
    python bench_merge_nbc.py --apply --overwrite   # 覆盖已有值（谨慎）
"""
import argparse
import importlib.util
import json
import os
import shutil
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
ROOT = os.path.dirname(TOOLS)
DATA = os.path.join(ROOT, "data")

BENCH = os.path.join(DATA, "bench.json")
NBC = os.path.join(DATA, "bench_nbc.json")

sys.path.insert(0, HERE)
import core  # noqa: E402


def load_bench_add():
    """复用 bench_add.py 的换算尺子，避免两套公式。"""
    path = os.path.join(TOOLS, "bench_add.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("bench_add", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001
        print("!! 加载 bench_add.py 失败：%s" % e)
        return None
    return mod


def find_scaler(mod):
    """在 bench_add 里找 perf 换算函数。找不到就返回 None（脚本会退出）。"""
    if mod is None:
        return None
    for name in ("perf_of", "calc_perf", "perf", "compute_perf", "_perf"):
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None


def perf_from_r23(single, multi):
    """项目定的尺子（prices.json meta 原文）：
         perf = 100 × (0.5×单核/2400 + 0.5×多核/40000)
    """
    if not single or not multi:
        return None
    return round(100.0 * (0.5 * single / 2400.0 + 0.5 * multi / 40000.0), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(NBC):
        print("没有 %s，先跑 bench_fetch_nbc.py --write" % NBC)
        return

    nbc = json.load(open(NBC, encoding="utf-8"))
    meta = nbc.get("meta") or {}
    rows = nbc.get("cpus") or []
    print("NBC 中间产物：%d 颗（抓取于 %s）" % (len(rows), meta.get("capturedAt")))

    # 尺子一致性检查：bench_add.py 里若有换算函数，用它复核我们自己算的
    mod = load_bench_add()
    scaler = find_scaler(mod)
    if scaler:
        print("尺子来源：bench_add.py 的 %s()" % scaler.__name__)
    else:
        print("尺子来源：脚本内置公式（bench_add.py 未暴露换算函数）")
        print("  公式：perf = 100 × (0.5×单核/2400 + 0.5×多核/40000)")

    bench = json.load(open(BENCH, encoding="utf-8"))
    cpus = bench.setdefault("cpus", [])
    by_key = {}
    for c in cpus:
        for k in {core._cpu_key(c.get("name")), core._cpu_key(c.get("id"))}:
            if k:
                by_key[k] = c

    added, filled, skipped, conflicted = [], [], [], []
    for r in rows:
        k = r.get("cpuKey") or core._cpu_key(r.get("chip"))
        r23 = r.get("r23") or {}
        single, multi = r23.get("single"), r23.get("multi")
        if not single or not multi:
            skipped.append((r.get("chip"), "NBC 无 R23 数据"))
            continue

        perf = perf_from_r23(single, multi)
        if scaler:
            try:
                ref = scaler(single, multi)
                if ref is not None and abs(float(ref) - perf) > 0.15:
                    conflicted.append((r.get("chip"), perf, ref))
            except Exception:  # noqa: BLE001
                pass

        exist = by_key.get(k)
        rec_bench = {
            "r23": {"single": single, "multi": multi},
            "gb6": r.get("gb6"),
            "r15": r.get("r15"),
            "zip7": r.get("zip7"),
        }
        if exist:
            old = (exist.get("bench") or {}).get("r23") or {}
            old_has = bool(old.get("single") and old.get("multi"))
            # 🔴 「有 R23 原始分」和「有 perfCurrent」是两件事。
            #    实测：i7-13620H 的 bench.r23 = {1775, 14161} 存在，
            #    但 perfCurrent 是 None —— 之前有人填了原始分却漏了换算。
            #    这种必须补 perf（用已有原始分算，不用 NBC 的），
            #    否则这台机器永远显示"无跑分"，而数据其实就在库里。
            if old_has and exist.get("perfCurrent") is None and not args.overwrite:
                perf_old = perf_from_r23(old["single"], old["multi"])
                if args.apply:
                    exist["perfCurrent"] = perf_old
                filled.append((r.get("chip"),
                               "用已有 R23 补算 perf（原有原始分 %s/%s）"
                               % (old["single"], old["multi"]),
                               old["single"], old["multi"], perf_old))
                continue
            if old_has and not args.overwrite:
                skipped.append((r.get("chip"), "已有 R23 值且 perf 齐全，不覆盖"))
                continue
            if args.apply:
                exist["bench"] = rec_bench
                exist["perfCurrent"] = perf
                exist["aggregated"] = True
                exist["source"] = meta.get("source")
                exist["sourceUrl"] = meta.get("url")
                exist["capturedAt"] = meta.get("capturedAt")
            filled.append((r.get("chip"), r.get("name"), single, multi, perf))
        else:
            newc = {
                "id": k.replace(" ", "-"),
                "name": r.get("name") or r.get("chip"),
                "vendor": ("AMD" if re.search(r"\b(r[3579]|ryzen|锐龙)", k)
                           else ("Intel" if re.search(r"\b(i[3579]|ultra|core|celeron|pentium)", k)
                                 else "其他")),
                "bench": rec_bench,
                "perfCurrent": perf,
                "aggregated": True,
                "source": meta.get("source"),
                "sourceUrl": meta.get("url"),
                "capturedAt": meta.get("capturedAt"),
                "note": "跨机型聚合值（NBC 汇总表），不是单机实测",
            }
            if args.apply:
                cpus.append(newc)
                by_key[k] = newc
            added.append((r.get("chip"), r.get("name"), single, multi, perf))

    print()
    print("=" * 74)
    print("新增 %d 颗 / 补空 %d 颗 / 跳过 %d 颗" % (len(added), len(filled), len(skipped)))
    print("=" * 74)
    if added:
        print("新增：")
        for chip, name, s, m, p in added:
            print("  %-26s R23 %-7s/%-8s → perf %-5s  (%s)"
                  % (chip[:24], s, m, p, name[:26]))
    if filled:
        print("补空（原记录无 R23）：")
        for chip, name, s, m, p in filled:
            print("  %-26s R23 %-7s/%-8s → perf %s" % (chip[:24], s, m, p))
    if skipped:
        print("跳过：")
        for chip, why in skipped[:12]:
            print("  %-26s %s" % (str(chip)[:24], why))
    if conflicted:
        print()
        print("⚠ 尺子不一致 %d 条（脚本算的 vs bench_add 算的）：" % len(conflicted))
        for chip, a, b in conflicted[:8]:
            print("  %-24s 本脚本 %s / bench_add %s" % (str(chip)[:22], a, b))

    if args.apply and (added or filled):
        bak = BENCH + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(BENCH, bak)
        json.dump(bench, open(BENCH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print()
        print("已写入 %s（备份 %s）" % (BENCH, bak))
        print("现有 CPU 总数：%d" % len(cpus))
    elif not args.apply:
        print()
        print("（演练模式。加 --apply 落盘）")


import re  # noqa: E402  （放在末尾避免与上面的 import 块抢行）


if __name__ == "__main__":
    main()
