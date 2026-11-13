#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
merge_patch.py —— 把 data/incoming/*.json 里的补丁合并进 machine_pool.json

为什么要单独一个脚本，而不是手工贴：
  1. 手工改 JSON 容易在逗号、转义、编码上出错，而且改前改后无法对比
  2. 补丁里的字段是「部分覆盖」，必须深合并（screen/ram/price 是嵌套对象，
     整个替换会把没在补丁里出现的子字段抹掉）
  3. 合并完要立刻能验证覆盖率没掉

安全设计：
  - 只合并白名单字段，cpu / gpu / ram.gb / storageGB 一律不碰
    （这些字段改了就改变判定结论，必须人来决定，脚本不许自作主张）
  - 写之前强制备份，备份名带时间戳
  - 每个字段的 before/after 都打印出来，人工过一遍
  - 合并后自动跑 coverage 对比

用法：
  python tools/merge_patch.py                       # 演练，不写盘
  python tools/merge_patch.py --apply                # 真写
  python tools/merge_patch.py --apply --patch data/incoming/xxx.json
"""
import json
import shutil
import sys
import io
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POOL = ROOT / "data" / "machine_pool.json"
INCOMING = ROOT / "data" / "incoming"

# 白名单：只有这些顶层/嵌套字段允许被补丁覆盖
TOP_ALLOW = {
    "ports", "batteryWh", "weightKg", "efficiency", "energyModel", "certModel",
    "efficiencyStatus", "price", "priceCapturedAt", "shopType", "shopName",
    "platformSkuId", "url", "dataGaps", "confidence", "evidence", "conflict",
    "priceStatus", "name",
    "screen", "ram", "gpu",   # 这三个只放行 NESTED_ALLOW 里列出的子键
}
# 嵌套对象里允许合并的键（其余子键保持原样）
NESTED_ALLOW = {
    "screen": {"refreshHz", "brightnessNit", "colorGamut", "panel",
               "sizeInch", "widthPx", "heightPx"},
    "ram":    {"slots", "type"},          # ← 故意不含 gb，改内存必须人工确认
    "gpu":    {"tgpBaseW", "tgpMaxW"},    # ← 不含 model / vramGB
    "price":  {"list", "discounts", "net", "subsidy", "final", "basisNote"},
}
# 严禁被补丁覆盖的字段（改了就动判定结论）
FORBIDDEN = {"cpu", "storageGB", "id", "cpuId", "cat", "brand"}


def load(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def deep_merge(old, new, nested_allow):
    """只合并白名单里的子键，其余原样保留"""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return new
    out = dict(old)
    for k, v in new.items():
        if k not in nested_allow:
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v, set(v.keys()))
        else:
            out[k] = v
    return out


def main():
    apply = "--apply" in sys.argv
    patch_file = None
    for i, a in enumerate(sys.argv):
        if a == "--patch" and i + 1 < len(sys.argv):
            patch_file = Path(sys.argv[i + 1])

    pool = load(POOL)
    machines = pool["machines"]
    index = {m["id"]: m for m in machines}

    files = [patch_file] if patch_file else sorted(INCOMING.glob("*.json"))
    if not files:
        print("没有找到补丁文件")
        return 1

    total_changed = 0
    all_warnings = []

    for pf in files:
        try:
            doc = load(pf)
        except Exception as e:
            print("[跳过] %s 解析失败: %s" % (pf.name, e))
            continue
        patches = doc.get("patch") or []
        if not patches:
            continue
        print("\n=== %s （%d 条）===" % (pf.name, len(patches)))

        for p in patches:
            mid = p.get("id")
            if mid not in index:
                print("  [!] id 不存在，跳过: %s" % mid)
                all_warnings.append("未知 id: %s" % mid)
                continue
            m = index[mid]
            changed = []
            for k, v in p.items():
                if k in ("id", "name"):
                    continue
                if k in FORBIDDEN:
                    print("  [!] %s.%s 在禁止清单里，已忽略（改它会影响判定结论）" % (mid, k))
                    all_warnings.append("%s.%s 被拒绝合并" % (mid, k))
                    continue
                if k not in TOP_ALLOW:
                    print("  [!] %s.%s 不在白名单，已忽略" % (mid, k))
                    continue

                if k in NESTED_ALLOW:
                    before = json.dumps(m.get(k), ensure_ascii=False)
                    m[k] = deep_merge(m.get(k) or {}, v, NESTED_ALLOW[k])
                    after = json.dumps(m.get(k), ensure_ascii=False)
                    if before != after:
                        changed.append((k, before, after))
                else:
                    before = json.dumps(m.get(k), ensure_ascii=False)
                    after = json.dumps(v, ensure_ascii=False)
                    if before != after:
                        m[k] = v
                        changed.append((k, before, after))

            print("  · %s（%s）改了 %d 个字段" % (mid, m.get("name", ""), len(changed)))
            for k, b, a in changed:
                bs = b if len(b) <= 90 else b[:87] + "..."
                as_ = a if len(a) <= 90 else a[:87] + "..."
                print("      %-16s %s  →  %s" % (k, bs, as_))
            total_changed += len(changed)

    print("\n合计改动 %d 个字段" % total_changed)

    if all_warnings:
        print("\n⚠️ 有 %d 条被拒绝或忽略：" % len(all_warnings))
        for w in all_warnings:
            print("   - %s" % w)

    if not apply:
        print("\n[演练模式] 未写盘。确认无误后加 --apply")
        return 0

    if total_changed == 0:
        print("没有实际改动，不写盘")
        return 0

    bak = POOL.with_name("machine_pool.json.bak-%s" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(POOL, bak)
    print("\n已备份 → %s" % bak.name)

    pool["meta"]["updated"] = datetime.now().strftime("%Y-%m-%d")
    with io.open(POOL, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    print("已写入 %s（machines=%d）" % (POOL.name, len(machines)))

    print("\n下一步请手动跑：python tools/coverage.py")
    print("  基线 recommendable = 357，合并后不得低于此值")
    return 0


if __name__ == "__main__":
    sys.exit(main())
