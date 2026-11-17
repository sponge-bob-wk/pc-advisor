# -*- coding: utf-8 -*-
"""fix_weight_unit.py —— 修「克当成千克」的重量单位错误

## 现象

`core.effective_weight()` 扫出多台机器的重量是 1000.00 / 992.00 kg。
笔记本不可能 1 吨重 —— 这是**把克数当成了千克**：

    ZOL 参数页写「笔记本重量 992g」
    解析时直接取数字 992，单位没换算 → 记成 992 kg

## 判据（怎么区分"真错"和"真重"）

笔记本重量现实区间：**0.7 ~ 4.5 kg**（含 18 寸游戏本）。
台式整机也不超过 ~25 kg。

所以：
    > 30        → 一定是单位错，按 /1000 换算（克→千克）
    > 4.5 且 ≤30 → 可疑（可能是台式整机），只报告不自动改
    < 0.5       → 可疑（可能是磅/斤），只报告

## 为什么必须修

`agent` 的「带出门这件事」会报「同品类最重 1000 kg」——
这种数字出现在回答里，整个项目的可信度就没了。
而且它会污染排序（重量是便携性的输入）。

## 用法

    python fix_weight_unit.py            # 演练
    python fix_weight_unit.py --apply    # 落盘（自动备份）
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
POOL = os.path.join(ROOT, "data", "machine_pool.json")

# 现实区间（见文件头）
HARD_MAX = 30.0      # 超过必然是单位错
SOFT_MAX = 4.5       # 超过要人工看（台式整机可能）
SOFT_MIN = 0.5       # 低于要人工看（可能单位是磅/斤）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    pool = json.load(open(POOL, encoding="utf-8"))
    ms = pool["machines"]

    fixed, suspect = [], []
    for m in ms:
        w = m.get("weightKg")
        if not isinstance(w, (int, float)) or w <= 0:
            continue
        if w > HARD_MAX:
            new = round(w / 1000.0, 2)
            fixed.append((m, w, new))
            if args.apply:
                m["weightKg"] = new
                # 留痕：说明这是单位换算，不是重新采集
                df = dict(m.get("dataFrom") or {})
                df["weightKg"] = "单位修正：原值 %s 疑为克，按 /1000 换算为 %s kg" % (w, new)
                m["dataFrom"] = df
                bf = set(m.get("backfilled") or [])
                bf.add("weightKg")
                m["backfilled"] = sorted(bf)
        elif w > SOFT_MAX or w < SOFT_MIN:
            suspect.append((m, w))

    print("单位错（>%gkg，按克→千克换算）：%d 台" % (HARD_MAX, len(fixed)))
    for m, old, new in fixed[:20]:
        print("  %-30s %8.2f kg → %.2f kg" % (m.get("name", "")[:28], old, new))
    if len(fixed) > 20:
        print("  … 还有 %d 台" % (len(fixed) - 20))

    if suspect:
        print()
        print("可疑（%g~%gkg 或 <%gkg，需人看）：%d 台"
              % (SOFT_MAX, HARD_MAX, SOFT_MIN, len(suspect)))
        for m, w in suspect[:12]:
            print("  %-30s %8.2f kg" % (m.get("name", "")[:28], w))

    if args.apply and fixed:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print()
        print("已写入 %s（备份 %s）" % (POOL, bak))
    elif not args.apply:
        print()
        print("（演练模式。加 --apply 落盘）")


if __name__ == "__main__":
    main()
