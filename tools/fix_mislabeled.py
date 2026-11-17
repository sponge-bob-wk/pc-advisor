# -*- coding: utf-8 -*-
"""fix_mislabeled.py —— 修已落库的两类标签错误

## 1) 麒麟芯片被标成 Intel

`zol_map_fields.vendor_of()` 旧版是「三分支 else → Intel」：
    vendor = "Intel" if ... else "Intel"
任何非 AMD/Apple 的 CPU 全落到 Intel。于是：

    zol-2173891  麒麟XE90      → vendor=Intel   ❌ 应为 华为
    zol-2173892  麒麟X90 Plus  → vendor=Intel   ❌ 应为 华为

这类错的危险性在于**看数据完全看不出来**：Intel 是合法值，不报错、不告警，
只有拿 CPU 去 bench.json 匹配时才失配。

## 2) 端口数组被逗号劈坏

旧 `parse_ports` 按 `[，,；;]` 无脑切：
    `折叠态：2×USB-C接口（左侧1个，上侧1个）`
    → `['折叠态：2×USB-C接口（左侧1个', '上侧1个）']`   ❌ 括号被劈开

新解析器保护括号内分隔符。本脚本用新解析器重算这些机器的 ports，
但**只覆盖「旧值明显是残缺括号」的情形** —— 不拿新结果去冲掉人工核对过的值。

## 用法

    python fix_mislabeled.py            # 演练
    python fix_mislabeled.py --apply    # 落盘（自动备份）
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


def unbalanced(ports):
    """端口条目里括号不配对 → 是旧解析器劈坏的。"""
    for p in ports or []:
        s = str(p)
        if s.count("（") != s.count("）") or s.count("(") != s.count(")"):
            return True
    return False


# 厂商合并：手上已有的写法里，`Qualcomm`/`MediaTek` 都属于「其他」这一档，
# 换个说法不算修正，会把 25 条噪声里的真修正淹没。
_VENDOR_SYNONYM = {
    ("Qualcomm", "其他"), ("其他", "Qualcomm"),
    ("MediaTek", "其他"), ("其他", "MediaTek"),
    ("MediaTek", "Qualcomm"), ("Qualcomm", "MediaTek"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    params = load_params()
    pool = json.load(open(POOL, encoding="utf-8"))
    ms = pool["machines"]

    vfix, pfix = [], []

    for m in ms:
        cpu = m.get("cpu")
        if isinstance(cpu, dict) and cpu.get("model"):
            cur = cpu.get("vendor")
            want = mapmod.vendor_of(cpu["model"])
            # 只在「能明确认出厂商」且「与现值不同」时改。
            # 认不出（want=None）不动 —— 宁可留旧值也不写空。
            if want and cur != want and (cur, want) not in _VENDOR_SYNONYM:
                vfix.append((m["id"], (m.get("name") or "")[:26], cpu["model"], cur, want))
                if args.apply:
                    cpu["vendor"] = want

        ports = m.get("ports")
        if ports and unbalanced(ports):
            pr = params.get(m["id"]) or {}
            newp = mapmod.parse_ports(pr)
            if newp and not unbalanced(newp):
                pfix.append((m["id"], (m.get("name") or "")[:26], ports, newp))
                if args.apply:
                    m["ports"] = newp
                    bf = set(m.get("backfilled") or [])
                    bf.add("ports")
                    m["backfilled"] = sorted(bf)

    print("=== CPU 厂商错标：%d 台 ===" % len(vfix))
    for mid, name, model, cur, want in vfix:
        print("  %-16s %-28s %-22s %s -> %s" % (mid, name, model[:20], cur, want))
    print()
    print("=== 端口数组残缺（括号被劈开）：%d 台 ===" % len(pfix))
    for mid, name, old, new in pfix:
        print("  %-16s %-28s" % (mid, name))
        print("      旧: %s" % json.dumps(old, ensure_ascii=False))
        print("      新: %s" % json.dumps(new, ensure_ascii=False))

    if args.apply:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写入 %s（备份 %s）" % (POOL, bak))
    else:
        print("\n（演练模式，未写文件；加 --apply 落盘）")


if __name__ == "__main__":
    main()
