# -*- coding: utf-8 -*-
"""investigate_price_diff.py —— 调查 15 台可疑价格差异

## 先别裁决，先判断「差异是真的还是假的」

价格交叉验证报出 15 台 >30%。但**差异大不等于数据错**，可能是：

  ① **SKU 不同**：同型号不同配置（16G vs 32G、512G vs 1T），价格本来就差很多
  ② **来源口径不同**：zolRef 是「参考价」，平台价是「当前挂牌价」
  ③ **时间差**：参考价是旧的，平台价是新的（涨价）
  ④ **真错**：抓错页面、抓到配件、单位错

## 判据：先看配置是否一致

如果两个来源的**配置不同**，那价格差异是正常的，不该裁决 ——
应该标记「不可比」，不参与交叉验证。

只有**配置相同但价格差 >30%** 才是真冲突。
"""
import io
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

POOL = r'E:\电脑涨价分析\data\machine_pool.json'
pool = json.load(io.open(POOL, encoding='utf-8'))
machines = {m.get("id"): m for m in pool.get("machines") or []}

# 15 台可疑（从 price_cross_check.py 输出抄的，名字模糊匹配）
SUSPECT_NAMES = [
    "联想拯救者", "联想拯救者Y9000P", "联想拯救者 创世 Ultra",
    "联想小新14", "机械革命蛟龙16Pro", "联想拯救者R9000P",
    "msi微星微星泰坦16 Max", "华硕天选7 Pro 酷睿版", "华为MateBook Fold",
    "华硕天选6 Pro 酷睿版", "联想拯救者Y7000X", "红米Redmi  Book Pro",
    "ThinkPad",
]

print("=== 可疑机器的价格来源明细 ===")
print()

for m in pool.get("machines") or []:
    name = m.get("name") or ""
    if not any(s in name for s in SUSPECT_NAMES):
        continue

    prices = m.get("prices") or {}
    if not isinstance(prices, dict):
        continue

    # 只比标价类（同量纲）
    marks = {}
    for k in ("zolRef", "jd", "tmall", "merchantMin"):
        v = prices.get(k)
        if isinstance(v, (int, float)) and v > 0:
            marks[k] = v
    if len(marks) < 2:
        continue

    lo = min(marks.values())
    hi = max(marks.values())
    ratio = (hi - lo) / lo * 100 if lo else 0
    if ratio <= 30:
        continue

    print("  %s" % name[:44])
    print("    id: %s" % m.get("id"))
    print("    spec: %s" % (m.get("spec") or "(空)")[:60])
    for k, v in sorted(marks.items()):
        print("      %-12s ¥%d" % (k, v))
    print("    差异: %.0f%%" % ratio)

    # 看有没有别的线索
    pb = m.get("priceBasis")
    if isinstance(pb, dict):
        print("    priceBasis: %s" % str(pb)[:100])
    srcs = m.get("sources")
    if srcs:
        print("    sources: %s" % str(srcs)[:80])
    print()
