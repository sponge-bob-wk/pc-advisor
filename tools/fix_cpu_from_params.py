# -*- coding: utf-8 -*-
"""fix_cpu_from_params.py —— 用参数页的「CPU型号」修正库内错误的 cpu.model

## 为什么需要单独一个脚本

`zol_to_pool` 从**页面标题 / skuLabel** 抠 CPU 型号，而 ZOL 的 skuLabel 常常不是 CPU：

    zol-392779  华硕G58JM4710   skuLabel="M4/8G"
                → 被当成 Apple M4 ！实际页面 CPU型号 = Intel 酷睿i7 4710HQ
    zol-1116386 联想扬天M51-80A-ISE  skuLabel="M5/4G"
                → 被当成 Apple M5 ！实际 = Intel 酷睿i7 6500U

「M4」「M5」这种短串既是联想/惠普的**机型后缀**，又是苹果的**芯片名**，
从标题正则抠必然撞车。参数页的 `CPU型号` 字段才是权威值。

## 判定规则（保守）

1. **只改「库内值是裸 M4/M5/M7/A18 Pro 这类非 CPU 名」且「参数页 CPU型号 是明确 CPU」** 的；
2. 参数页 CPU型号 必须能匹配出芯片名（i7 4710HQ / 酷睿M 5Y10c / 赛扬 N2840 …），
   否则不动；
3. 同时按新 CPU 名重算 vendor —— M4 被当 Apple 的错就是这么传染的；
4. 每笔改动记进 `backfilled` 与 `dataFrom.cpu_fix`。

## 用法

    python fix_cpu_from_params.py            # 演练
    python fix_cpu_from_params.py --apply    # 落盘（自动备份）
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import importlib.util

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POOL = os.path.join(ROOT, "data", "machine_pool.json")
SRC = os.path.join(ROOT, "data", "zol", "params-2026-11-16.jsonl")

spec = importlib.util.spec_from_file_location("mapmod", os.path.join(HERE, "zol_map_fields.py"))
mapmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mapmod)

# 库内值像「苹果芯片名」但只有 1-3 个字符 —— 这些一定是 skuLabel 残渣，不是真 CPU
SUSPECT = re.compile(r"^(M\d{1,2}|M\d{1,2}\s*(Pro|Max|Ultra)|A\d{1,2}(\s*Pro)?)$", re.I)
# 参数页值是「明确 CPU」的形状
CPU_LIKE = re.compile(
    r"(i[3579][-\s]?\d{4,5}[A-Z]{0,3}|赛扬|Celeron|奔腾|Pentium|酷睿\s?M\s?\w+|"
    r"Ultra\s?[3579]|Ryzen|锐龙|麒麟|Kirin|骁龙|Snapdragon|天玑|Atom|N\d{3,4})", re.I)


def load_params():
    out = {}
    if not os.path.exists(SRC):
        return out
    for line in open(SRC, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        out[rec["id"]] = rec.get("params") or {}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    params = load_params()
    pool = json.load(open(POOL, encoding="utf-8"))

    fixes = []
    for m in pool["machines"]:
        cpu = m.get("cpu")
        if not isinstance(cpu, dict):
            continue
        cur = (cpu.get("model") or "").strip()
        if not SUSPECT.match(cur):
            continue
        pr = params.get(m["id"]) or {}
        want = mapmod.clean_val(pr.get("CPU型号"))
        if not want or not CPU_LIKE.search(want):
            continue
        new_model = mapmod.norm_cpu(want)
        if not new_model:
            continue
        new_vendor = mapmod.vendor_of(want)
        fixes.append((m["id"], (m.get("name") or "")[:26], cur, cpu.get("vendor"),
                      new_model, new_vendor, m.get("skuLabel")))
        if args.apply:
            cpu["model"] = new_model
            if new_vendor:
                cpu["vendor"] = new_vendor
            bf = set(m.get("backfilled") or [])
            bf.add("cpu.model")
            m["backfilled"] = sorted(bf)
            df = dict(m.get("dataFrom") or {})
            df["cpu_fix"] = "参数页 CPU型号=%s（原 %s 来自 skuLabel）" % (want, cur)
            m["dataFrom"] = df

    print("待修 cpu.model：%d 台" % len(fixes))
    print()
    for mid, name, cur, oldv, newm, newv, sku in fixes:
        print("  %-16s %-28s" % (mid, name))
        print("      %s [%s]  →  %s [%s]   (skuLabel=%s)" % (cur, oldv, newm, newv, sku))

    if args.apply:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写入 %s（备份 %s）" % (POOL, bak))
    else:
        print("\n（演练模式，未写文件；加 --apply 落盘）")


if __name__ == "__main__":
    main()
