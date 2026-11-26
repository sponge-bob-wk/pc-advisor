# -*- coding: utf-8 -*-
"""vrm_rules.py —— 主板供电（VRM）能力表

## 为什么需要

实测问题：i5-14600KF（PL2 180W）配了「微星PRO H610M-E DDR4」（¥599）。

H610 是最低端芯片组，供电通常只有 4-6 相，带不动 180W 的 CPU：
  · 满载时 VRM 过热 → 触发降频保护 → CPU 只能跑在 65W
  · 花了 2499 买的 CPU，实际性能可能只有一半
  · 长期高温还会缩短主板寿命

这是「参数都对但配起来不合理」的典型 —— 
单看每一件都没错，合起来是坑。

## 数据现状（诚实说明）

`parts.json` 里的主板只有 `name` + `price`，**没有结构化的供电相数**。
名称里偶尔带（「16+1+2」「10+2+1」），但不是所有型号都有。

所以规则只能**按芯片组分档**，这是行业共识的粗略划分：

  H 系列（H610 / H510 / H810）    → 入门，4-6 相，建议配 ≤95W 的 CPU
  B 系列（B760 / B650 / B850 …）  → 主流，8-14 相，建议配 ≤150W
  Z / X 系列（Z790 / X870 …）     → 高端，12-20 相，可配 250W+

⚠️ 同一芯片组内不同型号供电差别很大（比如 B760M-K 和 B760 重炮手的差距），
   所以这只是**下限保护**：确保不出现「H610 配 i9」这种明显错的组合。
   精确判断需要抓主板的供电相数参数，那是后续工作。

## 名称里的供电信息

有的话优先用：
  「16+1+2相」 → 16 相 CPU 供电，很强
  「10+2相」   → 10 相，中等
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")


# ── 芯片组 → 供电档位 ──
#
# 等级：0=入门 1=主流 2=高端
CHIPSET_VRM = {
    # Intel 入门
    "H610": 0, "H510": 0, "H410": 0, "H810": 0,
    # Intel 主流
    "B660": 1, "B760": 1, "B560": 1, "B460": 1, "B860": 1,
    "H670": 1, "H770": 1,
    # Intel 高端
    "Z690": 2, "Z790": 2, "Z890": 2, "Z590": 2, "Z490": 2,
    # AMD 入门
    "A320": 0, "A520": 0, "A620": 0,
    # AMD 主流
    "B450": 1, "B550": 1, "B650": 1, "B850": 1,
    # AMD 高端
    "X570": 2, "X670": 2, "X870": 2,
    # 工作站/HEDT
    "X99": 2, "X299": 2, "TRX40": 2, "WRX80": 2,
}

# 等级 → 建议承载的 CPU 最大功耗（PL2）
#
# 🔴 2026-11-26 调（实测发现过严）：
#    原来 B 系列定 150W，结果「华硕TUF B760M-PLUS 重炮手」配 i5-14600KF
#    （180W）被拦了 —— 但重炮手是 B760 里的主力板，10+1 相供电
#    带 180W 完全没问题。
#
#    B 系列内部差距很大：
#      B760M-K（丐版，4+1 相）        → 实际只能带 125W
#      B760M 重炮手（10+1 相）        → 带 180W 没问题
#    按芯片组一刀切会误伤好板子。
#
#    所以把 B 系列阈值提到 180W（覆盖主流型号），
#    同时保留「名称里有相数就用相数」的精确判断。
VRM_MAX_WATT = {
    0: 95,    # H610 这类，带 65-95W 的 i3/i5 没问题，带 180W 的 K 系列会降频
    1: 180,   # B 系列主流，带 180W 的 i5-K/i7 可以，250W 的 i9 会降频
    2: 300,   # Z/X 系列，高端 U 都行
}

LEVEL_CN = {0: "入门", 1: "主流", 2: "高端"}


def board_level(name):
    """从主板型号名认出供电等级。认不出返回 None。"""
    n = (name or "").upper()
    # 按芯片组长度从长到短匹配（避免 B650 匹配到 B65）
    for cs in sorted(CHIPSET_VRM, key=len, reverse=True):
        if cs in n:
            return CHIPSET_VRM[cs], cs
    return None, None


def vrm_phase(name):
    """从型号名里抠供电相数（有的话）。认得「16+1+2」「10+2」这种。"""
    m = re.search(r"(\d{1,2})\s*\+\s*\d+(?:\s*\+\s*\d+)?\s*相?", name or "")
    if m:
        v = int(m.group(1))
        if 2 <= v <= 30:
            return v
    return None


def check(name, cpu_watt):
    """检查主板能不能带这颗 CPU。

    返回 (ok, 说明)
      ok=True  → 没问题
      ok=False → 供电不足，说明里写原因
    """
    level, cs = board_level(name)
    if level is None:
        # 认不出芯片组 → 不拦，但标记需确认
        return True, None

    limit = VRM_MAX_WATT[level]
    phase = vrm_phase(name)

    # 名称里写了相数 → 用相数更精确判断
    # 经验：1 相大约带 20-25W（考虑散热），10 相约 200-250W
    if phase is not None:
        phase_limit = phase * 25
        if cpu_watt > phase_limit:
            return False, (
                "主板 %s 是 %d 相供电，约能带 %dW，"
                "这颗 CPU 峰值 %dW，会降频"
                % (cs, phase, phase_limit, cpu_watt))
        return True, None

    # 没有相数 → 按芯片组档位判断
    if cpu_watt > limit:
        return False, (
            "%s 是%s芯片组，供电约能带 %dW，这颗 CPU 峰值 %dW，"
            "满载会因供电过热降频" % (cs, LEVEL_CN[level], limit, cpu_watt))

    return True, None


if __name__ == "__main__":
    print("=" * 74)
    print("主板供电检查测试")
    print("=" * 74)
    CASES = [
        ("微星PRO H610M-E DDR4", 180, "H610 配 i5-14600KF —— 应该拦"),
        ("微星PRO H610M-E DDR4", 65, "H610 配 i3 —— 没问题"),
        ("华硕TUF GAMING B760M-PLUS D4重炮手", 180, "B760 配 180W —— 可以"),
        ("华硕TUF GAMING B760M-PLUS D4重炮手", 250, "B760 配 250W —— 勉强，应该警告"),
        ("华硕ROG STRIX Z890-A GAMING WIFI S吹雪16+1+2+", 250,
         "Z890 16相 配 250W —— 没问题"),
        ("技嘉H610M K DDR4", 180, "H610 配 180W —— 应该拦"),
    ]
    for name, w, desc in CASES:
        ok, why = check(name, w)
        mark = "✓" if ok else "✗"
        print()
        print("  %s %s" % (mark, desc))
        print("     主板: %s" % name[:50])
        print("     CPU: %dW" % w)
        if why:
            print("     → %s" % why)
        else:
            print("     → 供电够")
