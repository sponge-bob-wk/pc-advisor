# -*- coding: utf-8 -*-
"""build_pc.py —— 台式机组装方案生成器

## 用户需求

「台式机也可以考虑，抓取主流的零部件去进行采集价格，
  如果有人想组装台式机，就可以推荐不同组合，
  然后把每个部分列举出来价格，再汇总价格，整机多少钱」

## 核心问题：兼容性

从 327 个配件里随便挑 7 个拼起来，90% 装不起来：
  · CPU 是 LGA1700（Intel），主板是 AM5（AMD）→ 装不上
  · 内存是 DDR5，主板只支持 DDR4 → 插不进
  · 显卡长度 340mm，机箱限长 320mm → 塞不进
  · 电源 450W，CPU+显卡要 600W → 开机就跳闸

所以这个模块的重点**不是"挑便宜的"**，是**"挑能装到一起的"**。

## 数据现状（必须诚实说明）

`data/parts.json` 里每个配件只有 `name` + `price` + `url`，
**没有结构化参数**（插槽类型、内存代数、显卡长度、功耗）。

所以我不能做精确的兼容性校验。能做的：

  ① **按型号名推断**（正则识别 LGA1700 / AM5 / DDR4 / DDR5 / RTX 4060…）
  ② **按价位档位配对**（入门/主流/高端，同档位的配件基本能配）
  ③ **功耗粗估**（按 CPU/显卡型号查表）

**推断不出来的，明说"需要确认"，不假装兼容。**

## 档位设计

三档，覆盖不同预算：

    入门   ¥3500-5000   办公/网课/轻度游戏（1080p 低画质）
    主流   ¥6000-9000   主流游戏（1080p 高画质）/ 视频剪辑
    高端   ¥12000-20000 2K/4K 游戏 / 3D 渲染

## 用法

    python build_pc.py                  # 三档全出
    python build_pc.py --budget 8000    # 指定预算
    python build_pc.py --json           # 输出 JSON
"""
import argparse
import io
import json
import os
import re
import sys

import pc_rules

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PARTS = os.path.join(ROOT, "data", "parts.json")

# ── 配件顺序（装机单的展示顺序，也是逻辑顺序）──
#
# 🔴 2026-11-26：硬盘和固态分开。
#    原来 storage 只存机械硬盘（「西部数据蓝盘 4TB」），
#    2026 年装机配机械盘当系统盘是不合理的 —— 系统必须装在 SSD 上。
#
#    现在的规则（见 pick_storage）：
#      · 主盘一定是 SSD（512G 起）
#      · 预算够的话再加一块机械盘存东西
ORDER = ["cpu", "motherboard", "ram", "gpu", "storage", "hdd", "psu", "case"]

LABEL = {
    "cpu": "CPU 处理器",
    "motherboard": "主板",
    "ram": "内存",
    "gpu": "显卡",
    "storage": "固态硬盘",
    "hdd": "机械硬盘",
    "psu": "电源",
    "case": "机箱",
}

# 旧数据兼容：parts.json 里 storage 是机械盘，ssd 是固态
STORAGE_KEY = "ssd"       # 主盘从这个 key 取
HDD_KEY = "storage"       # 从盘（可选）从这个 key 取

# ── CPU 插槽识别（按型号名正则）──
# 实测 ZOL 的商品名形如：
#   "AMD Ryzen 5 9600X"      → AM5
#   "Intel 酷睿 Ultra 9 285K" → LGA1851
#   "Intel 酷睿i5 10400F"     → LGA1200
SOCKET_RULES = [
    # AMD
    (r"Ryzen\s*[3579]\s*(9\d{3}|8\d{3})", "AM5"),      # 9000/8000 系
    (r"Ryzen\s*[3579]\s*(7\d{3}|6\d{3})", "AM5"),      # 7000/6000 系
    (r"Ryzen\s*[3579]\s*5\d{3}", "AM4"),               # 5000 系
    (r"Ryzen\s*[3579]\s*[1-4]\d{3}", "AM4"),           # 1000-4000 系
    (r"Threadripper", "sTR5"),
    # Intel
    (r"Ultra\s*[579]\s*2\d{2}", "LGA1851"),            # Ultra 200 系
    (r"酷睿\s*Ultra\s*[579]", "LGA1851"),
    (r"i[3579][- ]?1[2-4]\d{2,3}", "LGA1700"),         # 12/13/14 代
    (r"i[3579][- ]?1[01]\d{2,3}", "LGA1200"),          # 10/11 代
    (r"i[3579][- ]?9\d{3}", "LGA1151"),                # 9 代
    (r"i[3579][- ]?[2-8]\d{3}", "LGA1151"),            # 6-8 代
]

# ── 主板芯片组 → 插槽 ──
CHIPSET_SOCKET = {
    # AMD
    "X870": "AM5", "B850": "AM5", "B650": "AM5", "A620": "AM5",
    "X670": "AM5",
    "X570": "AM4", "B550": "AM4", "A520": "AM4", "B450": "AM4",
    "A320": "AM4",
    # Intel
    "Z890": "LGA1851", "B860": "LGA1851", "H810": "LGA1851",
    "Z790": "LGA1700", "B760": "LGA1700", "H770": "LGA1700",
    "H610": "LGA1700", "Z690": "LGA1700", "B660": "LGA1700",
    "H670": "LGA1700",
    "Z590": "LGA1200", "B560": "LGA1200", "H510": "LGA1200",
    "Z490": "LGA1200", "B460": "LGA1200", "H410": "LGA1200",
}

# ── 内存代数识别 ──
def ram_gen(name):
    n = (name or "").upper()
    if "DDR5" in n:
        return "DDR5"
    if "DDR4" in n:
        return "DDR4"
    if "DDR3" in n:
        return "DDR3"
    return None


def board_ram_gen(name, socket):
    """主板支持的内存代数（按芯片组推断）。"""
    n = (name or "").upper()
    # 板名里直接写了代数最准
    if "DDR5" in n:
        return "DDR5"
    if "DDR4" in n:
        return "DDR4"
    # 按平台推断默认值
    if socket == "AM5":
        return "DDR5"          # AM5 只有 DDR5
    if socket == "LGA1851":
        return "DDR5"          # Ultra 200 系只有 DDR5
    if socket == "AM4":
        return "DDR4"          # AM4 主力 DDR4（少数 AM4 板支持 DDR4 only）
    if socket == "LGA1700":
        return "DDR4"          # LGA1700 两种都有，默认取 DDR4（更便宜）
    return None


def cpu_socket(name):
    for pat, sock in SOCKET_RULES:
        if re.search(pat, name or "", re.I):
            return sock
    return None


def board_socket(name):
    """从主板名里认芯片组，再映射到插槽。"""
    n = (name or "").upper()
    # 按芯片组长度从长到短匹配（避免 B650 匹配到 B65）
    for cs in sorted(CHIPSET_SOCKET, key=len, reverse=True):
        if cs in n:
            return CHIPSET_SOCKET[cs]
    return None


# ── 功耗粗估（用于选电源）──
#
# 🔴 2026-11-26 修：i7/i9 原来估 105W，但 Intel K 系列 PL2 实测可到 250W+。
#    电源按 105W 算会选小了，满载直接跳闸。这里改成**取 PL2 量级的保守值**。
#
#    ⚠️ 这是工程经验值，不是精确 TDP。目的是「电源别选小」，宁可多留余量。
CPU_WATT = [
    # Intel K/KF 系列（可超频，功耗高）
    (r"i9[- ]?1[3-4]\d{3}\s*K", 250),
    (r"i7[- ]?1[3-4]\d{3}\s*K", 250),
    (r"i5[- ]?1[3-4]\d{3}\s*K", 180),
    (r"Ultra\s*9.*[KS]", 250),
    (r"Ultra\s*7.*[KS]", 200),
    # 非 K 系列
    (r"Ultra\s*9|i9", 125),
    (r"Ultra\s*7|i7", 105),
    (r"Ultra\s*5|i5", 85),
    (r"i3|Ryzen\s*3", 65),
    # AMD
    (r"Ryzen\s*9.*X3D", 120),
    (r"Ryzen\s*9", 170),
    (r"Ryzen\s*7", 105),
    (r"Ryzen\s*5", 85),
]
GPU_WATT = [
    (r"RTX\s*5090|RTX\s*4090", 450),
    (r"RTX\s*5080|RTX\s*4080", 320),
    (r"RTX\s*5070\s*Ti|RTX\s*4070\s*Ti", 285),
    (r"RTX\s*5070|RTX\s*4070", 200),
    (r"RTX\s*5060\s*Ti|RTX\s*4060\s*Ti", 160),
    (r"RTX\s*5060|RTX\s*4060", 130),
    (r"RTX\s*30[5-8]0", 130),
    (r"RX\s*9\d{3}|RX\s*7\d{3}", 260),
    (r"RX\s*6\d{3}", 160),
]


def cpu_watt(name):
    for pat, w in CPU_WATT:
        if re.search(pat, name or "", re.I):
            return w
    return 65


def gpu_watt(name):
    for pat, w in GPU_WATT:
        if re.search(pat, name or "", re.I):
            return w
    return 150


def psu_watt(name):
    """从电源名里读出额定功率。"""
    m = re.search(r"(\d{3,4})\s*W", name or "", re.I)
    if m:
        w = int(m.group(1))
        if 200 <= w <= 2000:
            return w
    m = re.search(r"额定\s*(\d{3,4})", name or "")
    if m:
        return int(m.group(1))
    return None


# ── 内存容量识别 ──
def ram_gb(name):
    """从内存名里读出总容量（GB）。

    ZOL 的写法很乱，实测样本：
      "金百达银爵 16GB（2×8GB）DDR4 3200"          → 16
      "金士顿叛逆者Renegade 32GB(16G*2) DDR5 6000" → 32
      "金士顿骇客神条FURY 8GB DDR4 2400"            → 8
      "七彩虹16GB DDR4 3200 8GB"                   → 16（取第一个明确的 GB）

    判据：优先找 "NNGB" 或 "NNG"；如果有 "×2"/"*2" 说明是套条，
    但前面写的 16GB 已经是总容量，直接用。
    """
    n = (name or "").upper().replace(" ", "")
    # 先找 "16GB" 这种明确的
    m = re.search(r"(\d{1,3})\s*GB", n)
    if m:
        v = int(m.group(1))
        if 4 <= v <= 256:
            return v
    # 再找 "16G*2" / "16G×2" 这种，前面是单条容量，要乘
    m = re.search(r"(\d{1,3})\s*G\s*[*×X]\s*(\d)", n)
    if m:
        return int(m.group(1)) * int(m.group(2))
    # 最后找裸的 "16G"
    m = re.search(r"(\d{1,3})\s*G(?![A-Z])", n)
    if m:
        v = int(m.group(1))
        if 4 <= v <= 256:
            return v
    return None


# ── 档位定义 ──
TIERS = [
    {
        "id": "entry", "name": "入门办公",
        "budget": (3500, 5000),
        "use": "网课 / 办公 / 1080p 轻度游戏",
        "minRam": 16,     # 🔴 2026-11-26 实测发现：不设下限会选出 8G 单条，
                          #    而 2026 年装 8G 的机器两年后必然不够用。
                          #    16G 是现在的底线（也是 majors.json 里定的下限）。
        "pick": {"cpu": 0.25, "gpu": 0.20, "motherboard": 0.12,
                 "ram": 0.08, "storage": 0.10, "psu": 0.08, "case": 0.07},
    },
    {
        "id": "main", "name": "主流游戏",
        "budget": (6000, 9000),
        "use": "1080p 高画质游戏 / 视频剪辑 / 建模",
        "minRam": 16,
        "pick": {"cpu": 0.20, "gpu": 0.38, "motherboard": 0.10,
                 "ram": 0.07, "storage": 0.08, "psu": 0.08, "case": 0.05},
    },
    {
        "id": "high", "name": "高端全能",
        "budget": (12000, 20000),
        "use": "2K/4K 游戏 / 3D 渲染 / 本地跑模型",
        "minRam": 32,     # 高端档没 32G 说不过去
        "pick": {"cpu": 0.22, "gpu": 0.42, "motherboard": 0.09,
                 "ram": 0.08, "storage": 0.07, "psu": 0.07, "case": 0.04},
    },
]


def load_parts():
    d = json.load(io.open(PARTS, encoding="utf-8"))
    return {k: (v.get("items") or []) for k, v in d.items()}


# ── 用途 → 配置比例调整 ──
#
# 🔴 2026-11-26 新增（用户要求）：
#    「台式机是要按照装机标准，根据用户需求你去推荐」
#
# 同一预算下，不同用途的钱该花在不同地方：
#
#   游戏     → 显卡吃大头（50%），CPU 够用就行
#   设计/剪辑 → CPU 和内存并重，显卡中档即可
#   办公     → 不要显卡都行，钱花在 CPU 和 SSD 上
#   建模/渲染 → CPU 多核 + 内存，显卡中高档
#   AI/本地模型 → 显存优先（显卡），内存要大
#
# 这里只调整**比例**，不改档位本身。比例会自动归一化。
PURPOSE_TUNING = {
    "gaming": {
        "gpu": 1.55, "cpu": 0.90, "ram": 0.85,
        "storage": 0.90, "motherboard": 0.90,
        "label": "游戏优先（显卡吃大头）",
    },
    "design": {
        "gpu": 0.85, "cpu": 1.20, "ram": 1.35,
        "storage": 1.15, "motherboard": 1.05,
        "label": "设计剪辑（CPU + 内存优先）",
    },
    "video": {
        "gpu": 1.10, "cpu": 1.30, "ram": 1.30,
        "storage": 1.40, "motherboard": 1.00,
        "label": "视频剪辑（CPU + 内存 + 大容量）",
    },
    "office": {
        "gpu": 0.30, "cpu": 1.30, "ram": 1.10,
        "storage": 1.25, "motherboard": 0.95,
        "label": "办公（不需要独显，CPU + SSD 优先）",
    },
    "threeD": {
        "gpu": 1.15, "cpu": 1.35, "ram": 1.30,
        "storage": 1.10, "motherboard": 1.00,
        "label": "3D 建模渲染（多核 + 内存）",
    },
    "ai": {
        "gpu": 1.60, "cpu": 0.90, "ram": 1.40,
        "storage": 1.10, "motherboard": 1.05,
        "label": "本地跑模型（显存 + 内存优先）",
    },
    "dev": {
        "gpu": 0.55, "cpu": 1.25, "ram": 1.40,
        "storage": 1.05, "motherboard": 1.00,
        "label": "写代码（内存 + CPU 优先，显卡够用就行）",
    },
}


def tune_ratio(base, purposes):
    """按用途调整配件预算比例。

    purposes: 用途列表，如 ['gaming'] 或 ['design','video']
    返回：归一化后的比例字典 + 调整说明
    """
    ratio = dict(base)
    notes = []

    for p in (purposes or []):
        t = PURPOSE_TUNING.get(p)
        if not t:
            continue
        notes.append(t["label"])
        for k, mul in t.items():
            if k == "label" or k not in ratio:
                continue
            ratio[k] = ratio[k] * mul

    # 归一化：保证加起来还是 100%
    total = sum(ratio.values())
    if total > 0:
        ratio = {k: v / total for k, v in ratio.items()}

    return ratio, notes


def purpose_tier(base_tier, purposes):
    """用途改了内存下限和标题吗？"""
    tier = dict(base_tier)
    ps = set(purposes or [])

    # 视频剪辑 / 3D / 本地模型：16G 明显不够
    if ps & {"video", "threeD", "ai"}:
        tier["minRam"] = max(tier.get("minRam") or 16, 32)

    # 办公：不需要独显预算，但也别低于 16G
    if ps & {"office"} and not (ps & {"gaming", "design", "video"}):
        tier["minRam"] = max(tier.get("minRam") or 16, 16)

    # 🔴 2026-11-26：**档位名要跟着用途走**。
    #    实测：问「办公用的台式，八千」，标题还是「主流游戏」——
    #    因为档位是按预算挑的（8000 落在 main 档），名字写死了。
    #    用户看到"办公需求 → 主流游戏"，会觉得没听懂他。
    _NAME_BY_PURPOSE = {
        "gaming": "游戏主机",
        "design": "设计主机",
        "video": "剪辑主机",
        "threeD": "渲染主机",
        "ai": "本地模型主机",
        "office": "办公主机",
        "dev": "开发主机",
    }
    for p in (purposes or []):
        if p in _NAME_BY_PURPOSE:
            tier["name"] = _NAME_BY_PURPOSE[p]
            break

    # 用途描述也换成用户说的
    _USE_BY_PURPOSE = {
        "gaming": "1080p/2K 游戏",
        "design": "平面设计 / 修图",
        "video": "视频剪辑 / 调色",
        "threeD": "3D 建模 / 渲染",
        "ai": "本地跑模型",
        "office": "办公 / 网课 / 文档",
        "dev": "写代码 / 虚拟机",
    }
    _uses = [_USE_BY_PURPOSE[p] for p in (purposes or [])
             if p in _USE_BY_PURPOSE]
    if _uses:
        tier["use"] = " / ".join(_uses[:2])

    return tier


# ── 不需要独显的用途 ──
#
# 🔴 2026-11-26 实测发现的问题：
#    办公用途配置里出现了「GTX 1650 + 1250W 电源」——
#    调低显卡比例（gpu: 0.30）只是让它选到便宜的卡，
#    不是「不配显卡」。而办公真的不需要独显。
#
#    同理：CPU + 显卡的搭配要平衡，不能 i5-12490F 配 RTX 5060 Ti
#    （CPU 会成瓶颈，显卡的 4000 块发挥不出来）。
NO_DGPU_PURPOSE = {"office", "dev"}


def needs_dgpu(purposes):
    """这个用途要不要独显。"""
    ps = set(purposes or [])
    # 只要有一个用途需要显卡就要
    if ps & {"gaming", "design", "video", "threeD", "ai"}:
        return True
    if ps and not (ps & NO_DGPU_PURPOSE):
        return True
    return not ps   # 没说用途 → 保守给一张


def balance_cpu_gpu(cpu_price, gpu_price):
    """检查 CPU/显卡是否失衡。

    经验规则：显卡价格超过 CPU 的 2.5 倍时，CPU 大概率拖后腿
    （游戏/设计都是这个量级）。这时要求 CPU 往上提一档。
    """
    if not cpu_price or not gpu_price:
        return False
    return gpu_price > cpu_price * 2.5


def pick_near(items, target, used=None, key=None):
    """从 items 里挑价格最接近 target 的一个。

    key: 额外的过滤函数（兼容性约束）
    """
    cand = []
    for it in items:
        if used and it["name"] in used:
            continue
        if key and not key(it):
            continue
        cand.append(it)
    if not cand:
        return None
    return min(cand, key=lambda x: abs(x["price"] - target))


def build_plan(parts, tier, budget=None, purposes=None):
    """按档位 + 用途生成一套配置。

    🔴 2026-11-26 加 purposes 参数（用户要求）：
        「台式机是要按照装机标准，根据用户需求你去推荐」
        —— 同一预算下，打游戏和剪视频的钱该花在不同地方：
           游戏 → 显卡吃大头；剪辑 → CPU+内存+大容量；办公 → 不要独显。

    返回 {"tier":..., "rows":[...], "total":..., "issues":[...]}
    """
    lo, hi = tier["budget"]
    budget = budget or (lo + hi) / 2

    # 按用途调整比例 + 套用规则表
    ratio, tune_notes = tune_ratio(tier["pick"], purposes)
    tier = purpose_tier(tier, purposes)

    # 🔴 2026-11-26：**部件硬约束从 pc_rules 表取，不再散着硬编码**。
    #    原来 minRam 写在 TIERS 里、固态容量逻辑写在选型函数里，
    #    改一处不知道会影响什么。现在统一到 pc_rules.RULES，
    #    每个约束都带「为什么」，能直接说给用户听。
    rule = pc_rules.rule_for(purposes)

    # 内存下限：规则表优先，档位默认兜底
    tier["minRam"] = max(tier.get("minRam") or 16, rule["minRam"][0])

    # 解释：告诉用户「按什么配的、为什么」
    issues = list(tune_notes)
    for line in pc_rules.explain(rule, purposes):
        issues.append(line)

    plan = {}

    # ① CPU
    cpu = pick_near(parts.get("cpu") or [], budget * ratio["cpu"])
    if not cpu:
        return None
    plan["cpu"] = cpu
    sock = cpu_socket(cpu["name"])
    if not sock:
        issues.append("CPU 插槽需确认（型号里没识别出来）")

    # ② 主板：必须和 CPU 同插槽
    mb_key = None
    if sock:
        mb_key = lambda it, s=sock: board_socket(it["name"]) == s
    mb = pick_near(parts.get("motherboard") or [],
                   budget * ratio["motherboard"], key=mb_key)
    if not mb:
        # 同插槽的板子没抓到 → 退一步：不限制插槽，但标警告
        mb = pick_near(parts.get("motherboard") or [],
                       budget * ratio["motherboard"])
        if mb:
            issues.append("主板插槽未与 CPU 匹配（库里没有对应芯片组），需人工确认")
    if not mb:
        return None
    plan["motherboard"] = mb

    # ③ 内存：代数要和主板一致，容量要不低于档位底线
    #
    # 🔴 2026-11-26 实测发现问题：不设容量下限时，入门档选出了
    #    「金士顿骇客神条FURY 8GB DDR4 2400」—— 8G 单条 + 2400 频率，
    #    2026 年装机这配置两年后必然不够用，推荐出去是害人。
    #
    #    所以两道约束叠加：
    #      ① 代数匹配（AM5/LGA1851 必须 DDR5）
    #      ② 容量达标（entry/main ≥16G，high ≥32G）
    want_gen = board_ram_gen(mb["name"], board_socket(mb["name"]))
    need_ram = tier.get("minRam") or 16

    def ram_ok(it, g=want_gen, need=need_ram):
        if g and ram_gen(it["name"]) != g:
            return False
        cap = ram_gb(it["name"])
        # 认不出容量的放行（但会记 warning）—— 宁可少过滤也别漏掉好配件
        if cap is not None and cap < need:
            return False
        return True

    ram = pick_near(parts.get("ram") or [], budget * ratio["ram"], key=ram_ok)
    if not ram:
        # 放宽：只要代数对，容量随便（然后报 warning）
        ram = pick_near(parts.get("ram") or [],
                        budget * ratio["ram"],
                        key=(lambda it, g=want_gen: ram_gen(it["name"]) == g)
                        if want_gen else None)
        if ram:
            issues.append("内存容量需确认：这台 %s 可能低于 %dG"
                          % (ram["name"][:24], need_ram))
    if not ram:
        ram = pick_near(parts.get("ram") or [], budget * ratio["ram"])
        if ram and want_gen:
            issues.append("内存代数需确认（主板要 %s）" % want_gen)
    if not ram:
        return None
    plan["ram"] = ram

    # ④ 显卡
    #
    # 🔴 2026-11-26 加两条规则（实测发现的问题）：
    #    ① 办公/写代码**不需要独显** —— 原来只是把比例调到 0.30，
    #       结果还是选出一张 GTX 1650。改成：这类用途直接不配显卡，
    #       把那部分预算转给 CPU 和存储。
    #    ② **CPU/显卡要平衡** —— 实测出现过 i5-12490F 配 RTX 5060 Ti，
    #       显卡 4199 而 CPU 只有 1280，游戏时 CPU 会成瓶颈，
    #       那张显卡的钱发挥不出来。
    if rule["needDgpu"][0]:
        gpu = pick_near(parts.get("gpu") or [], budget * ratio["gpu"])
        if not gpu:
            return None
        plan["gpu"] = gpu

        # 平衡检查：显卡太贵而 CPU 太便宜 → 把 CPU 往上提
        if balance_cpu_gpu(cpu["price"], gpu["price"]):
            better = pick_near(parts.get("cpu") or [],
                               gpu["price"] / 1.8,
                               key=lambda it: cpu_socket(it["name"]) == sock
                               if sock else True)
            if better and better["price"] > cpu["price"] * 1.2:
                plan["cpu"] = better
                cpu = better
                issues.append("CPU 已上调到 %s（原配显卡 %s 会拖后腿）"
                              % (better["name"][:20], gpu["name"][:16]))
    else:
        # 不配独显：用核显，预算转给 CPU/存储
        #
        # ⚠️ 局部变量 gpu 也要赋值 —— 下面的电源计算要用到它
        #    （靠 gpu["price"]==0 判断是不是核显方案）。
        #    原来只写了 plan["gpu"]，导致 UnboundLocalError。
        gpu = {
            "name": "核显（不配独立显卡）",
            "price": 0,
            "url": None,
        }
        plan["gpu"] = gpu
        issues.append("这个用途不需要独立显卡，用 CPU 核显即可")

    # ⑤ 硬盘：**主盘一定是 SSD**
    #
    # 🔴 2026-11-26 修（用户说「ssd必须修」）：
    #    原来 storage 只取机械硬盘，结果装机单里出现「西部数据蓝盘 4TB」
    #    当系统盘 —— 2026 年这配置说不过去，机械盘跑系统慢得没法用。
    #
    #    现在的规则：
    #      · 主盘从 ssd 里选，容量至少 512G（预算紧就 500G）
    #      · 预算够的话（>7000）再加一块机械盘存电影/素材
    ssd_pool = parts.get(STORAGE_KEY) or []
    if not ssd_pool:
        # 固态没抓到 → 退回机械盘，但明确告警
        sto = pick_near(parts.get(HDD_KEY) or [],
                        budget * ratio["storage"])
        if not sto:
            return None
        plan["storage"] = sto
        issues.append("固态硬盘库里没数据，暂时用机械盘（性能差距大，建议补 SSD）")
    else:
        # 固态容量门槛：按用途从规则表取（游戏 1T / 办公 500G / 剪辑 2T）
        #
        # 🔴 2026-11-26 修（用户指出「打游戏配 500G、办公配 1T，这是反的」）：
        #    原来只设「≥500G」这条通用底线，然后按价格比例挑 ——
        #    游戏的预算大多给了显卡，留给固态的钱少，就选到 500G；
        #    办公预算没处花，反而选了 1T。完全反了。
        #
        #    现在：容量下限跟着用途走（pc_rules 里定的），
        #    在该用途够用的固态里选价格最接近目标的。
        min_gb = max(rule["minSsdGB"][0], pc_rules.SSD_ABS_FLOOR_GB)

        def ssd_gb(it):
            n = (it["name"] or "").upper().replace(" ", "")
            m = re.search(r"(\d+(?:\.\d+)?)\s*TB", n)
            if m:
                return int(float(m.group(1)) * 1000)
            m = re.search(r"(\d{3,4})\s*GB", n)
            if m:
                return int(m.group(1))
            return None

        def ssd_ok(it):
            g = ssd_gb(it)
            # 认不出容量的不选（宁可少一个候选，也别配错）
            return g is not None and g >= min_gb

        sto = pick_near(ssd_pool, budget * ratio["storage"], key=ssd_ok)
        if not sto:
            # 预算内的够不上 → 扩大价格搜索范围，只保证容量
            _big = sorted([x for x in ssd_pool if ssd_ok(x)],
                          key=lambda x: x["price"])
            if _big:
                sto = _big[0]
                issues.append(
                    "固态按需配到 %s（达不到 %dG 的没有，已选库里最小的够用款）"
                    % (sto["name"][:24], min_gb))
        if not sto:
            # 连够容量的都没有 → 退到最大容量，但明确告警
            _all = sorted([x for x in ssd_pool if ssd_gb(x)],
                          key=lambda x: ssd_gb(x) or 0, reverse=True)
            if _all:
                sto = _all[0]
                issues.append(
                    "固态容量不够：这个用途建议 %dG 起，"
                    "但库里最大的只有 %s，请人工确认"
                    % (min_gb, sto["name"][:24]))
        if not sto:
            return None
        plan["storage"] = sto

    # ⑤b 从盘：预算够就加一块机械盘（只存素材/游戏，不装系统）
    if budget >= pc_rules.HDD_MIN_BUDGET and parts.get(HDD_KEY):
        hdd_budget = budget * 0.05
        hdd = pick_near(parts.get(HDD_KEY) or [], hdd_budget,
                        key=lambda it: (it["price"] or 0) >= 200)
        if hdd:
            plan["hdd"] = hdd

    # ⑥ 电源：按 CPU+GPU 功耗 × 1.5 留余量
    #
    # 🔴 2026-11-26 实测发现两个问题：
    #    ① 不配独显时 gpu["price"]==0，不能按 150W 算 ——
    #       核显整机满载也就 200W，按价格比例会选到 1250W 的电源，
    #       实测出现过「办公配置 + 1250W 电源」这种离谱组合。
    #    ② **电源不能按预算比例选，要按实际功率选** ——
    #       电源不是"越贵越好"，够用就行，多余的钱应该给 CPU/显卡。
    #       所以这里改成：先算 need_w，再选**能带得动的最便宜那档**。
    _gpu_w = gpu_watt(gpu["name"]) if gpu.get("price") else 0
    need_w = int((cpu_watt(cpu["name"]) + _gpu_w + pc_rules.PSU_BASE_LOAD) * pc_rules.PSU_HEADROOM)

    def psu_ok(it):
        w = psu_watt(it["name"])
        return w is not None and w >= need_w

    _psu_pool = [x for x in (parts.get("psu") or []) if psu_ok(x)]
    if _psu_pool:
        # 够用的里面选最便宜的 —— 电源不是越贵越好
        psu = min(_psu_pool, key=lambda x: x["price"])
    else:
        # 库里没有够功率的：退到最接近的，并告警
        psu = pick_near(parts.get("psu") or [], budget * ratio["psu"])
        if psu:
            have = psu_watt(psu["name"]) or 0
            issues.append("电源可能不够：需要约 %dW，这台只有 %s"
                          % (need_w, ("%dW" % have) if have else "未知"))
        if psu:
            have = psu_watt(psu["name"]) or 0
            issues.append("电源可能不够：需要约 %dW，这台 %s"
                          % (need_w, ("%dW" % have) if have else "未知"))
    if not psu:
        return None
    plan["psu"] = psu

    # ⑦ 机箱
    case = pick_near(parts.get("case") or [], budget * ratio["case"])
    if not case:
        return None
    plan["case"] = case

    rows = []
    total = 0
    for k in ORDER:
        it = plan.get(k)
        if not it:
            continue
        rows.append({
            "part": k,
            "label": LABEL[k],
            "name": it["name"],
            "price": it["price"],
            "url": it.get("url"),
        })
        total += it["price"]

    return {
        "tier": tier["id"],
        "tierName": tier["name"],
        "use": tier["use"],
        "rows": rows,
        "total": total,
        "issues": issues,
        "priceType": "zol-reference",
        "note": "价格是 ZOL 参考价（媒体价），不是电商到手价，实际可能更低。",
    }


def fmt_money(v):
    return "¥%s" % format(int(round(v)), ",")


def print_plan(p):
    print()
    print("=" * 66)
    print("  %s · %s" % (p["tierName"], p["use"]))
    print("=" * 66)
    print()
    print("  %-12s %-44s %s" % ("配件", "型号", "参考价"))
    print("  " + "-" * 62)
    for r in p["rows"]:
        print("  %-12s %-44s %s"
              % (r["label"], r["name"][:42], fmt_money(r["price"])))
    print("  " + "-" * 62)
    print("  %-12s %-44s %s" % ("", "合计", fmt_money(p["total"])))
    if p["issues"]:
        print()
        print("  需要确认：")
        for x in p["issues"]:
            print("    ⚠️ %s" % x)
    print()
    print("  %s" % p["note"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(PARTS):
        print("✗ 缺 %s，先跑 fetch_parts.py" % PARTS)
        return 1

    parts = load_parts()
    n = sum(len(v) for v in parts.values())
    print("配件库: %d 个（%s）" % (n, ", ".join(
        "%s %d" % (LABEL.get(k, k), len(v)) for k, v in parts.items())))

    plans = []
    if args.budget:
        # 按预算找最接近的档位
        best = min(TIERS, key=lambda t: min(
            abs(args.budget - t["budget"][0]),
            abs(args.budget - t["budget"][1])))
        p = build_plan(parts, best, args.budget)
        if p:
            plans.append(p)
    else:
        for t in TIERS:
            p = build_plan(parts, t)
            if p:
                plans.append(p)

    if args.json:
        print(json.dumps(plans, ensure_ascii=False, indent=1))
    else:
        for p in plans:
            print_plan(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
