# -*- coding: utf-8 -*-
"""fix_missing_brand.py —— 补全缺失的 brand 字段

## 为什么要补

`test_analyze.py` 的分组测试抓到：
  各品牌台数之和 = 220，但总数 = 223
  → 有 3 台被静默丢弃

根因：这 3 台 `brand` 是 None。`analyze.py` 的 groupby 遇到 None 就 `continue`，
所以它们不进任何组。

## 为什么这是真 bug，不只是测试问题

用户问「各品牌机型数量对比」时，这 3 台**不存在于任何品牌里** ——
统计结果就不完整。而且「有多少台」和「各品牌加起来多少台」对不上，
用户如果自己加一遍会发现矛盾。

## 怎么补

**从机型名推断品牌**，规则保守（宁可留空也不错标）：
  · 机型名以品牌名开头 → 直接用
  · 已知厂商的别名（来酷→联想、Surface→微软）

推断不出来的保持 None，并在报告里列出来让人工确认。
"""
import io
import json
import shutil
import sys
import time

# 🔴 2026-11-26 加：**强制 stdout/stderr 用 UTF-8**。
#    Windows 控制台默认 GBK，打印 ⚠️/✓/✗ 这类字符会 UnicodeEncodeError 直接崩。
#    实测：server.py 第 588 行的 ⚠️ 导致服务起不来。
#    不依赖外部 PYTHONIOENCODING 环境变量，在代码里写死。
#    errors="replace" 是双保险 —— 宁可显示成 ? 也不能让脚本崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


sys.stdout.reconfigure(encoding='utf-8')

POOL = r'E:\电脑涨价分析\data\machine_pool.json'

# 已知品牌名（从现有数据里提取的 14 个）
KNOWN_BRANDS = [
    "联想", "惠普", "华硕", "戴尔", "微星", "机械革命", "荣耀",
    "华为", "小米", "红米", "苹果", "宏碁", "雷神", "神舟",
    # 补：数据里可能出现的
    "东芝", "机械师", "来酷", "微软", "vivo", "OPPO", "一加",
    "真我", "摩托罗拉", "iQOO",
]

# 别名/子品牌 → 主品牌
ALIAS = {
    "来酷": "联想",       # Lecoo 是联想子品牌
    "Surface": "微软",     # 微软 Surface
    "ThinkPad": "联想",
    "拯救者": "联想",
    "天选": "华硕",
    "ROG": "华硕",
    "无畏": "华硕",
    "暗影精灵": "惠普",
    "战66": "惠普",
    "星Book": "惠普",
    "小新": "联想",
    "ThinkBook": "联想",
    "Redmi": "红米",
    "RedmiBook": "红米",
    "MagicBook": "荣耀",
    "MateBook": "华为",
    "蛟龙": "机械革命",
    "极光": "机械革命",
    "无界": "机械革命",
    "耀世": "机械革命",
    "苍龙": "机械革命",
    "隐星": "雷神",
    "猎刃": "雷神",
    "战神": "神舟",
    "蛟龙": "机械革命",
}


def infer_brand(m):
    """从机型名推断品牌。返回 (brand, 依据) 或 (None, 原因)。"""
    name = (m.get("name") or "").strip()
    if not name:
        return None, "机型名为空"

    # ① 名字以已知品牌开头
    for b in KNOWN_BRANDS:
        if name.startswith(b):
            return b, "名称前缀"

    # ② 名字里含品牌名（更宽松，但要防止误判）
    for b in KNOWN_BRANDS:
        if b in name:
            return b, "名称包含"

    # ③ 别名/子品牌
    for alias, b in ALIAS.items():
        if alias in name:
            return b, "别名 %s" % alias

    return None, "无法从名称推断"


def main():
    pool = json.load(io.open(POOL, encoding='utf-8'))
    machines = pool.get("machines") or []

    missing = [m for m in machines if not m.get("brand")]
    print("机器总数: %d，缺 brand: %d" % (len(machines), len(missing)))
    print()

    if not missing:
        print("没有需要补的。")
        return

    patches = []
    still = []
    print("=== 推断结果 ===")
    for m in missing:
        b, why = infer_brand(m)
        if b:
            patches.append((m, b, why))
            print("  ✓ %-38s → %s（%s）" % (m.get("name", "?")[:36], b, why))
        else:
            still.append((m, why))
            print("  ✗ %-38s 推断不出（%s）" % (m.get("name", "?")[:36], why))

    print()
    print("可补 %d 台，仍需人工确认 %d 台" % (len(patches), len(still)))

    if not patches:
        print()
        print("（演练模式，无可补项）")
        return

    bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(POOL, bak)
    for m, b, why in patches:
        m["brand"] = b
        df = dict(m.get("dataFrom") or {})
        df["brand"] = "从机型名推断（%s）" % why
        m["dataFrom"] = df
        bf = set(m.get("backfilled") or [])
        bf.add("brand")
        m["backfilled"] = sorted(bf)

    json.dump(pool, open(POOL, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print()
    print("已写入 %s（备份 %s）" % (POOL, bak))


if __name__ == "__main__":
    main()
