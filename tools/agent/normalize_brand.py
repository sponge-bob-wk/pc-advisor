# -*- coding: utf-8 -*-
"""normalize_brand.py —— 统一品牌名

## 为什么要统一

品牌名不统一会导致两个问题：

① **分组统计重复计数**
   实测：「各品牌数量对比」里 `联想` 和 `联想来酷` 是两组，
   但用户心里的「联想」是包含来酷的。

② **筛选结果不一致**
   `analyze.py` 的 `_apply_filter` 用子串匹配（`'联想' in brand`），
   所以 `brand=联想` 会匹配到 `联想来酷`。
   但 `groupby` 是按原值分组的 —— 两处口径不同。

实测数字：精确匹配 98 台（¥13,206），子串匹配 100 台（¥13,070）。
差 2 台、136 元 —— 用户不会发现，但这就是「同一件事两个答案」。

## 归并规则

子品牌/产品线 → 主品牌。只归并有明确归属关系的：
  · 联想来酷 → 联想（Lecoo 是联想子品牌）
  · 其余保持原样（不确定的不动，宁可多一组也不要错并）
"""
import io
import json
import shutil
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

POOL = r'E:\电脑涨价分析\data\machine_pool.json'

# 子品牌/产品线 → 主品牌
MERGE = {
    "联想来酷": "联想",
    "Lecoo": "联想",
    "ThinkPad": "联想",
    "拯救者": "联想",
    "小新": "联想",
    "Redmi": "红米",
    "RedmiBook": "红米",
    "MagicBook": "荣耀",
    "MateBook": "华为",
    "Surface": "微软",
}


def main():
    pool = json.load(io.open(POOL, encoding='utf-8'))
    machines = pool.get("machines") or []

    print("机器总数: %d" % len(machines))
    print()

    changes = []
    for m in machines:
        b = (m.get("brand") or "").strip()
        if b in MERGE:
            new = MERGE[b]
            changes.append((m, b, new))
            m["brand"] = new
            df = dict(m.get("dataFrom") or {})
            df["brand"] = "%s 归入 %s（子品牌统一）" % (b, new)
            m["dataFrom"] = df

    if not changes:
        print("没有需要归并的。")
        return

    print("=== 归并 %d 台 ===" % len(changes))
    for m, old, new in changes:
        print("  %-34s %s → %s" % (m.get("name", "?")[:32], old, new))

    bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(POOL, bak)
    json.dump(pool, open(POOL, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print()
    print("已写入 %s（备份 %s）" % (POOL, bak))

    # 验证
    import collections
    c = collections.Counter(m.get("brand") for m in machines if m.get("brand"))
    print()
    print("归并后品牌数: %d" % len(c))


if __name__ == "__main__":
    main()
