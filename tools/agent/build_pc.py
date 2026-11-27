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
import fit_rules
import vrm_rules

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
ORDER = ["cpu", "cooler", "motherboard", "ram", "gpu",
         "storage", "hdd", "psu", "case"]

LABEL = {
    "cpu": "CPU 处理器",
    "cooler": "散热器",
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

# 🔴 2026-11-26：物理尺寸参数（fetch_fit_params.py 抓的）
FITS_PATH = os.path.join(ROOT, "data", "parts_fit.json")
_FITS_CACHE = {}


def load_fits():
    """加载配件尺寸参数。没有就返回空 dict（不影响主流程）。"""
    global _FITS_CACHE
    if _FITS_CACHE:
        return _FITS_CACHE
    try:
        d = json.load(io.open(FITS_PATH, encoding="utf-8"))
        _FITS_CACHE = {k: (v.get("items") or {}) for k, v in d.items()}
    except Exception:
        _FITS_CACHE = {}
    return _FITS_CACHE


def fit_of(category, name):
    """按名字取某个配件的尺寸参数。"""
    return (load_fits().get(category) or {}).get(name) or {}


def case_spec_of(name):
    """把机箱的参数拼成一行，供 fit_rules 解析。"""
    p = fit_of("case", name).get("params") or {}
    return "；".join("%s%s" % (k, v) for k, v in p.items())

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
    # Intel K/KF 系列（可超频，功耗高到 PL2）
    #
    # 🔴 2026-11-26 修：原来只写了 1[3-4] 代，12 代 K 系列被漏掉 ——
    #    实测 i5-12600KF 只算 85W，结果给它配了 ¥89 的入门风冷。
    #    12600K 的 PL2 实测 150W，风冷压不住会降频。
    #    改成 1[2-4] 代全覆盖。
    (r"i9[- ]?1[2-4]\d{3}\s*K", 250),
    (r"i7[- ]?1[2-4]\d{3}\s*K", 250),
    (r"i5[- ]?1[2-4]\d{3}\s*K", 180),   # 12600K/13600K/14600K 的 PL2 都 ~180W
    (r"i5[- ]?1[2-4]\d{3}\s*F", 90),    # F 系列不带 K，不能超频
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
    # 🔴🔴 2026-11-27：**多用途改加权平均，不连乘**。
    #    连乘语义是错的 —— 6 个用途把 gpu 权重缩到 0.52 倍、cpu 放大 2.2 倍，
    #    实测「预算9000 六用途」推荐出 ¥12,000（超 33%）。
    #    改成：每个用途各算一份完整 ratio（内部归一化），再等权平均。
    ps = [p for p in (purposes or []) if PURPOSE_TUNING.get(p)]
    notes = [PURPOSE_TUNING[p]["label"] for p in ps]

    if not ps:
        return dict(base), []

    n = len(ps)
    acc = {k: 0.0 for k in base}
    for p in ps:
        t = PURPOSE_TUNING[p]
        one = dict(base)
        for k, mul in t.items():
            if k == "label" or k not in one:
                continue
            one[k] = one[k] * mul
        _t = sum(one.values())
        if _t > 0:
            one = {k: v / _t for k, v in one.items()}
        for k in acc:
            acc[k] += one.get(k, 0) / n

    _tot = sum(acc.values())
    if _tot > 0:
        acc = {k: v / _tot for k, v in acc.items()}

    if len(notes) > 1:
        notes = ["多用途折中（%s）" % " / ".join(notes[:4])]

    return acc, notes


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


def balance_cpu_gpu(cpu_price, gpu_price):
    """检查 CPU/显卡是否失衡。

    经验规则：显卡价格超过 CPU 的 2.5 倍时，CPU 大概率拖后腿
    （游戏/设计都是这个量级）。这时要求 CPU 往上提一档。
    """
    if not cpu_price or not gpu_price:
        return False
    return gpu_price > cpu_price * 2.5


# ── 散热器选型 ──
#
# 🔴 2026-11-26 新增：装机单原来没有散热器，说不过去。
#
# 散热器要和 CPU 发热量匹配：
#   · 65W 的 i3/R5 用 ¥65 风冷就够
#   · 125W 的 i7 要 4-6 热管风冷或 240 水冷
#   · 250W 的 i7-K/i9 必须水冷，风冷压不住
# 选小了会降频（实测性能低 20-30%），选大了浪费钱。
COOLER_TIERS = [
    # (CPU 功耗上限, 价格区间, 类型说明)
    (85, (40, 130), "入门风冷"),
    (140, (130, 320), "中端风冷"),
    (9999, (320, 900), "水冷"),
]


def pick_cooler(pool, cpu_name):
    """按 CPU 功耗挑散热器。返回 (配件 dict 或 None, 说明)。

    🔴 2026-11-26 实测发现的问题：
        i5-14600KF（180W）本该配水冷，却选了 ¥85 的风冷。
        「宽恕」逻辑写错了 —— 该档位没货时会退到「价格 ≤ 上限的任意款」，
        这把 ¥85 的入门风冷也放进来了，压不住 180W 的 CPU。

        修法：**按类型硬性过滤**，不靠价格区间猜：
          · 水冷档 → 名字里必须有「水冷/AIO/冷排/一体式/360/240」
          · 风冷档 → 名字里不能有水冷字样
        价格区间只是辅助（在类型对的前提下选价格合适的）。
    """
    if not pool:
        return None, "配件库里没有散热器数据"

    def _is_liquid(it):
        n = (it["name"] or "").upper()
        return any(k in n for k in ("水冷", "AIO", "冷排", "一体式",
                                    "360", "240", "280", "120"))

    w = cpu_watt(cpu_name)

    # 高功耗（>140W）→ 必须水冷
    if w > 140:
        liquid = [x for x in pool if _is_liquid(x)]
        if liquid:
            # 水冷里选价格居中的（¥320-900 区间内）
            cand = [x for x in liquid if 300 <= (x["price"] or 0) <= 1000]
            if not cand:
                cand = liquid
            best = min(cand, key=lambda x: abs((x["price"] or 0) - 500))
            return best, "水冷（CPU 约 %dW，风冷压不住）" % w
        # 库里没水冷 → 只能给最好的风冷，并明确告警
        air = sorted(pool, key=lambda x: -(x["price"] or 0))
        return air[0], ("CPU 约 %dW 需要水冷，但库里没有水冷数据，"
                        "配了库里最好的风冷，建议人工确认" % w)

    # 中功耗（86-140W）→ 中端风冷
    if w > 85:
        air = [x for x in pool if not _is_liquid(x)]
        if air:
            cand = [x for x in air if 100 <= (x["price"] or 0) <= 350]
            if not cand:
                cand = air
            best = min(cand, key=lambda x: abs((x["price"] or 0) - 200))
            return best, "中端风冷（CPU 约 %dW）" % w

    # 低功耗（≤85W）→ 入门风冷够用
    air = [x for x in pool if not _is_liquid(x)]
    if air:
        cand = [x for x in air if 40 <= (x["price"] or 0) <= 150]
        if not cand:
            cand = air
        best = min(cand, key=lambda x: abs((x["price"] or 0) - 90))
        return best, "入门风冷（CPU 约 %dW，够用）" % w

    return None, "没找到合适的散热器"


def pick_near(items, target, used=None, key=None, max_price=None):
    """从 items 里挑价格最接近 target 的一个。

    key: 额外的过滤函数（兼容性约束）
    max_price: 价格硬上限（超过的不参与）。预算自洽用 ——
               每个件都"最接近"的话，叠加起来总价会爆
               （实测预算 8000 的单子跑到 ¥41,731）。
    """
    cand = []
    for it in items:
        if used and it["name"] in used:
            continue
        if key and not key(it):
            continue
        if max_price is not None and (it.get("price") or 0) > max_price:
            continue
        cand.append(it)
    if not cand:
        return None
    return min(cand, key=lambda x: abs(x["price"] - target))



# ── 统一的价格目标/上限计算（2026-11-27 去重）──
#
# 问题：budget * ratio[k] 这个计算散落 20 处，上限系数
#       有的 1.25 有的 1.3 有的没有 —— 改一处漏一处。
# 这两个函数是唯一计算处，新代码必须用它们。

CAP_FACTOR = 1.3      # 单件价格相对目标的上限倍数（留给同价位好型号）


def _target(ratio, budget, key):
    """某类配件的目标价 = 总预算 × 该类的比例。"""
    return budget * ratio.get(key, 0)


def _cap_of(ratio, budget, key, factor=None):
    """某类配件的价格上限。缺省用 CAP_FACTOR。"""
    return _target(ratio, budget, key) * (factor or CAP_FACTOR)


def _pick_psu(parts, ratio, budget, cpu, gpu):
    """按 CPU+GPU 实际功耗选电源（不按预算比例）。

    🔴 2026-11-27 从 build_plan 拆出（原内联 35 行）。

       电源不是"越贵越好"，够用就行 —— 多余的钱给 CPU/显卡。
       核显方案不能按 150W 算（实测出现过「办公配置 + 1250W 电源」）。

    ⚠️ 顺手修了一个重复 bug：原代码里 `if psu: issues.append(...)`
       写了两遍（"这台只有" 和 "这台" 两版），用户会看到两条
       几乎一样的警告。这里只保留一条。
    """
    gpu_w = gpu_watt(gpu["name"]) if gpu.get("price") else 0
    need_w = int((cpu_watt(cpu["name"]) + gpu_w + pc_rules.PSU_BASE_LOAD)
                 * pc_rules.PSU_HEADROOM)

    def psu_ok(it):
        w = psu_watt(it["name"])
        return w is not None and w >= need_w

    pool = [x for x in (parts.get("psu") or []) if psu_ok(x)]
    if pool:
        # 够用的里面选最便宜的 —— 电源不是越贵越好
        return min(pool, key=lambda x: x["price"]), []

    # 库里没有够功率的：退到最接近的，并告警
    psu = pick_near(parts.get("psu") or [], _target(ratio, budget, "psu"),
                    max_price=_cap_of(ratio, budget, "psu"))
    if not psu:
        return None, []
    have = psu_watt(psu["name"]) or 0
    return psu, ["电源可能不够：需要约 %dW，这台只有 %s"
                 % (need_w, ("%dW" % have) if have else "未知")]



def _pick_case(parts, ratio, budget, gpu, cooler, mb):
    """选机箱 —— 必须装得下显卡、散热器、主板。

    🔴 2026-11-27 从 build_plan 拆出（原内联 66 行）。

       机箱的约束是"装得下前面所有东西"，所以必须在显卡/散热器/主板
       都定了之后才能选（今天第四次踩顺序坑）。

       三项校验：
         ① 显卡长度 ≤ 机箱显卡限长（留 10mm 给供电线）
         ② 散热器：风冷验高度 / 水冷验冷排尺寸
         ③ 主板板型 ∈ 机箱适用主板

    返回 (case, issues)。
    """
    _iss = []
    _case = None
    # ⑦ 机箱：**必须装得下显卡、散热器、主板**
    #
    # 🔴 2026-11-26 新增（用户要求）
    #
    #    原来机箱只按价格选，完全不看尺寸。实际会配出这种：
    #      显卡 340mm + 限长 320mm 的机箱 → 装不进去
    #      360 水冷 + 只支持 240 的机箱 → 装不上
    #      ATX 主板 + MATX 机箱 → 放不下
    #
    #    ⚠️ 顺序：机箱必须在显卡/散热器/主板都定了之后才能选 ——
    #       它的约束就是"装得下这些东西"。今天第四次踩顺序坑。
    _fits = load_fits()
    _gpu_size = (fit_of("gpu", gpu["name"]).get("params") or {}).get(
        "产品尺寸", "")
    # 散热器参数要**整个传**给 fit_rules —— 判水冷/风冷要靠「散热方式」字段，
    # 只传「产品尺寸」的话会把它当风冷（实测瓦尔基里B360 名字里没有「水冷」二字，
    # 但参数里写着 散热方式=水冷）。
    cooler_params = (fit_of("cooler", cooler["name"]).get("params") or {}) \
        if cooler else {}
    cooler_size = "；".join("%s%s" % (k2, v2)
                             for k2, v2 in cooler_params.items())

    def _case_ok(it):
        spec = case_spec_of(it["name"])
        if not spec:
            return True     # 机箱参数没抓到 → 不筛（宁可选便宜也别全排除）
        bad = fit_rules.check(
            _gpu_size, spec,
            cooler_name=(cooler["name"] if cooler else None),
            board_name=mb["name"], cooler_spec=cooler_size)
        return not any(lv == "hard" for lv, _ in bad)

    case = pick_near(parts.get("case") or [], budget * ratio["case"],
                     key=_case_ok)

    if not case:
        # 装得下的机箱里没有价格合适的 → 放宽价格，但必须装得下
        _ok_cases = [x for x in (parts.get("case") or []) if _case_ok(x)]
        if _ok_cases:
            case = min(_ok_cases, key=lambda x: x["price"] or 0)
            _iss.append(
                "机箱放宽了价格（装得下的最便宜是 %s ¥%d）"
                % (case["name"][:22], case["price"]))
        else:
            # 一个都装不下 → 退回原逻辑，但明确告警
            case = pick_near(parts.get("case") or [],
                             budget * ratio["case"])
            if case:
                _iss.append(
                    "⚠️ 没有尺寸合适的机箱：库里装得下这套配置的机箱为 0，"
                    "已选 %s，需人工确认能否装下" % case["name"][:24])
    if not case:
        return None, _iss
    _case =  case

    # ⑦b 最终整体校验：把剩下的尺寸问题都报出来
    _final = fit_rules.check(
        _gpu_size, case_spec_of(case["name"]),
        cooler_name=(cooler["name"] if cooler else None),
        board_name=mb["name"], cooler_spec=cooler_size)
    for _lv, _msg in _final:
        if _lv == "hard":
            _iss.append("⚠️ 尺寸问题：%s" % _msg)
        else:
            _iss.append("尺寸：%s" % _msg)
    return _case, _iss



def build_plan(parts, tier, budget=None, purposes=None, explicit_dgpu=None):
    """按档位 + 用途生成一套配置。

    🔴 2026-11-26 加 purposes 参数（用户要求）：
        「台式机是要按照装机标准，根据用户需求你去推荐」
        —— 同一预算下，打游戏和剪视频的钱该花在不同地方：
           游戏 → 显卡吃大头；剪辑 → CPU+内存+大容量；办公 → 不要独显。

    🔴 2026-11-27 加 explicit_dgpu（真实用户反馈）：
        用户说「要独显」「要有好点的显卡训练模型」，purpose 被标成 dev，
        dev 在 pc_rules 里 needDgpu=False → 配了核显，无视显式要求。

        explicit_dgpu=True 时强制配独显，覆盖 purpose 的自动判断。
        agent.py 从用户文本检测「独显/显卡/玩游戏/训练模型」等信号传入。

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

    # 🔴 2026-11-27：**用户显式要求独显 → 强制配 + 预算提到 ai 档**。
    #    purpose 的自动判断可能判错（比如「训练模型」被标成 dev），
    #    但用户嘴里说出来的"要独显"是最高优先级的信号。
    #    放在 rule_for 之后，直接改写 rule["needDgpu"]。
    #
    #    ⚠️ 光改 needDgpu 还不够（第二轮实测）：
    #       dev 的 gpu 权重只有 0.55，强制配独显后选出的是 RTX 3050（¥1899）
    #       —— 训练模型 4G 显存根本不够。用户说"要有好点的显卡"，
    #       预算也得跟着提。所以把 gpu 权重提到 ai 档（1.60）再重新归一化。
    _gpu_cap = None
    if explicit_dgpu:
        rule["needDgpu"] = (True,
                            "用户明确要求独显（显式要求优先于用途推断）")
    # 🔴 2026-11-27 第三轮：**gpu 抬档只在「原比例不够」时做**。
    #
    #    第一轮只改 needDgpu → dev 选出 RTX 3050（权重 0.24 太低）
    #    第二轮无条件抬到 1.60 → gaming 场景 gpu 占 77%，CPU target
    #      剩 ¥620、cap ¥774 → 所有 CPU 被上限排除 → 整单 None！
    #      （「台式机，八千，打游戏」直接坏了，端到端抓到）
    #
    #    教训：**gaming 原本 gpu 就占 0.53，显式独显对它是"确认"，
    #    不是"抬档"。只有 dev/office 这类原本不打算配独显的用途，
    #    显式要求才意味着"改预算分配"。**
    #
    #    最终逻辑：
    #      gpu 原比例 < 0.35 → 抬到 0.5（dev/office 场景）
    #      gpu 原比例 ≥ 0.35 → 完全不动 ratio（gaming/ai/3D 场景）
    #      gpu 绝对上限 budget × 0.6 仍保留（防单件爆预算）
    _gpu_cap = None
    if explicit_dgpu:
        rule["needDgpu"] = (True,
                            "用户明确要求独显（显式要求优先于用途推断）")
        if ratio["gpu"] < 0.35:
            _gpu_budget = budget * 0.5
            _rest = budget - _gpu_budget
            _others = {k: v for k, v in ratio.items() if k != "gpu"}
            _o_total = sum(_others.values())
            if _o_total > 0:
                for k in _others:
                    ratio[k] = _others[k] / _o_total
            ratio["gpu"] = _gpu_budget / budget
            _gpu_cap = _gpu_budget
            issues.append("用户要求独显：显卡预算提到 %s，"
                          "其余配件按剩余预算分配" % fmt_money(_gpu_budget))
        else:
            # gaming/ai 等本来就重显卡的用途：只设上限，不动比例
            _gpu_cap = budget * 0.6


    plan = {}

    # 🔴 2026-11-27：explicit_dgpu 时所有件都有价格上限。
    #    预算自洽逻辑：gpu 拿走 0.5，其余件分剩下的 —— 但如果每个件
    #    都允许"最接近 target"地选超，加起来还是爆
    #    （实测 8000 的单子跑到 ¥41,731）。
    #    所以上限 = budget × ratio × 1.25。
    # 🔴 2026-11-27：**所有场景都给价格上限**（不只 explicit_dgpu）。
    #    pick_near 是"最接近 target"，每件都可能选超，叠加就爆。
    _cap = {k: budget * ratio[k] * 1.3 for k in ratio}

    def _pick(k, pool):
        return pick_near(pool, budget * ratio[k], max_price=_cap.get(k))

    # ① CPU
    cpu = _pick("cpu", parts.get("cpu") or [])
    if not cpu:
        return None
    plan["cpu"] = cpu
    sock = cpu_socket(cpu["name"])
    if not sock:
        issues.append("CPU 插槽需确认（型号里没识别出来）")

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
        _gpu_target = budget * ratio["gpu"]
        # 🔴 2026-11-27：显式要求独显时，gpu 预算有硬上限
        if _gpu_cap is not None:
            _gpu_target = min(_gpu_target, _gpu_cap)
        gpu = _pick("gpu", parts.get("gpu") or []) if _gpu_cap is None else pick_near(parts.get("gpu") or [], _gpu_target)
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
        # 🔴 2026-11-27：**把 gpu 预算重新分配给其他件**。
        #    原来只写 plan["gpu"]=核显 就结束 —— gpu 那部分权重凭空消失，
        #    实测「预算8000 写代码」只配出 ¥6,442（浪费 1558 元）。
        _freed = ratio.get("gpu", 0)
        if _freed > 0:
            for _k, _share in (("cpu", 0.40), ("ram", 0.25),
                               ("storage", 0.20), ("motherboard", 0.15)):
                if _k in ratio:
                    ratio[_k] += _freed * _share
            ratio["gpu"] = 0
            _t2 = sum(ratio.values())
            if _t2 > 0:
                ratio = {k: v / _t2 for k, v in ratio.items()}
            issues.append("这个用途不需要独立显卡，"
                          "显卡预算已转给 CPU / 内存 / 固态")
        else:
            issues.append("这个用途不需要独立显卡，用 CPU 核显即可")

        gpu = {
            "name": "核显（不配独立显卡）",
            "price": 0,
            "url": None,
        }
        plan["gpu"] = gpu

    # 🔴 2026-11-26 重排：主板/内存的选型**必须在显卡平衡之后**。
    #    原因：CPU 的最终型号要到显卡选完才确定（平衡检查可能换芯），
    #    而主板的插槽和供电都要匹配 CPU —— 先选主板会配到带不动的板子。
    #    实测：i5-12490F(90W) 配 H610 没问题，但平衡后换成
    #    i5-14600KF(180W)，H610 就带不动了。

    # ② 主板：必须和 CPU 同插槽，且**供电要带得动 CPU**
    #
    # 🔴 2026-11-26 新增供电校验（实测问题）：
    #    i5-14600KF（PL2 180W）配了「微星PRO H610M-E」（¥599）。
    #    H610 是最低端芯片组，供电 4-6 相带不动 180W —— 满载 VRM 过热降频，
    #    花 2499 买的 CPU 实际只能跑在 65W。参数都对但配起来是坑。
    #
    #    判据在 vrm_rules.py（按芯片组分档 + 型号名里的相数）。
    _cpu_w = cpu_watt(cpu["name"])
    _mb_pool = parts.get("motherboard") or []

    def _mb_ok(it):
        # ① 插槽要对
        if sock and board_socket(it["name"]) != sock:
            return False
        # ② 供电要够
        ok, _ = vrm_rules.check(it["name"], _cpu_w)
        return ok

    mb = pick_near(_mb_pool, budget * ratio["motherboard"], key=_mb_ok,
                   max_price=(budget * ratio["motherboard"] * 1.25
                              if explicit_dgpu else None))

    if not mb:
        # 供电够的同插槽板子没有（或太贵）→ 找最接近的，然后明确告警
        _loose = None
        if sock:
            _loose = pick_near(
                _mb_pool, budget * ratio["motherboard"],
                key=lambda it, s=sock: board_socket(it["name"]) == s)
        if not _loose:
            _loose = pick_near(_mb_pool, budget * ratio["motherboard"])

        if _loose:
            mb = _loose
            _ok, _why = vrm_rules.check(mb["name"], _cpu_w)
            if not _ok:
                # 供电不足：把预算挪一点再试一次（往上一档找）
                _better = [
                    x for x in _mb_pool
                    if (sock is None or board_socket(x["name"]) == sock)
                    and vrm_rules.check(x["name"], _cpu_w)[0]
                    and (x["price"] or 0) <= budget * 0.25
                ]
                if _better:
                    mb = min(_better, key=lambda x: x["price"] or 0)
                    issues.append(
                        "主板从原方案换成 %s（原配板子供电不足：%s）"
                        % (mb["name"][:26], _why))
                else:
                    issues.append(
                        "主板供电可能不足：%s。%s。"
                        "预算内找不到更强的板子，建议加钱或换低功耗 CPU"
                        % (mb["name"][:30], _why))
        if not mb:
            return None
    else:
        # 正常选到了，但如果 CPU 功耗高，提示一下这套搭配的合理性
        if _cpu_w > 140:
            _lv, _cs = vrm_rules.board_level(mb["name"])
            if _lv is not None and _lv == 1:
                issues.append(
                    "%dW 的 CPU 配 B 系列板（%s），主流型号没问题，"
                    "但如果是丐版可能降频，建议确认具体供电相数"
                    % (_cpu_w, _cs))

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

    # ① 内存不设 max_price —— 容量/代数是硬约束（pc_rules），
    #    预算让位（实测：上限 1.25x 把符合 32G 的全排了，只剩 8G）
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

    # ①b 散热器：按 CPU **最终**功耗选
    #
    # ⚠️ 必须放在显卡平衡之后 —— 平衡检查可能把 CPU 换成功耗更高的型号
    #    （实测 i5-12490F 85W → i5-14600KF 180W）。
    #    先选散热器的话，会拿 85W 的入门风冷去压 180W 的 CPU，直接降频。
    #
    # 🔴 2026-11-26 新增：原来装机单没有散热器。
    _cooler, _cooler_note = pick_cooler(parts.get("cooler") or [],
                                        cpu["name"])
    if _cooler:
        plan["cooler"] = _cooler
        if cpu_watt(cpu["name"]) > 85:
            issues.append("散热器：%s；水冷要确认机箱支持对应冷排尺寸"
                          % _cooler_note)
    else:
        issues.append("配件库里没有散热器数据，这套配置需另购散热器")

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
                        budget * ratio["storage"],
                        max_price=(budget * ratio["storage"] * 1.25
                                   if explicit_dgpu else None))
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

    # ⑥ 电源：按 CPU+GPU 功耗选（已拆为 _pick_psu）
    psu, _iss_psu = _pick_psu(parts, ratio, budget, cpu, gpu)
    issues.extend(_iss_psu)
    if not psu:
        return None
    plan["psu"] = psu


    # ⑦ 机箱：必须装得下一切（已拆为 _pick_case）
    case, _iss_cas = _pick_case(parts, ratio, budget, gpu, _cooler, mb)
    issues.extend(_iss_cas)
    if not case:
        return None
    plan["case"] = case


    # 🔴 2026-11-27：**总价超预算时降档，顺序是先降大头**。
    #    原来只降 storage/hdd/ram/cooler/case（省几百块），
    #    但 CPU+显卡 占 65%，不降它们永远超。
    #    CPU/显卡有用途门槛（minCpuRatio / 15%），不能无限降。
    _budget_cap = budget * 1.15
    _cur = sum(x["price"] for x in plan.values() if x.get("price"))
    _cpu_floor = budget * max(0.12, rule["minCpuRatio"][0] * 0.8)
    _gpu_floor = budget * 0.15
    _guard = 0
    while _cur > _budget_cap and _guard < 15:
        _guard += 1
        _changed = False
        for _k in ("gpu", "cpu", "storage", "hdd", "ram", "cooler", "case"):
            if _k not in plan:
                continue
            _it = plan[_k]
            if _k == "cpu" and (_it["price"] or 0) <= _cpu_floor:
                continue
            if _k == "gpu" and (_it["price"] or 0) <= _gpu_floor:
                continue
            _pool = parts.get(_k) or []
            _cheaper = [x for x in _pool
                        if x.get("price") and x["price"] < _it["price"]]
            if not _cheaper:
                continue
            plan[_k] = max(_cheaper, key=lambda x: x["price"])
            _cur = sum(x["price"] for x in plan.values() if x.get("price"))
            _changed = True
            if _cur <= _budget_cap:
                break
        if not _changed:
            break
    if _guard > 0:
        issues.append("总价超出预算，已把部分配件降到预算内")

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


def _variant_plan(parts, tier, budget, purposes, explicit_dgpu,
                  cpu_mul=1.0, gpu_mul=1.0):
    """生成变体方案：在 base 比例上调整 CPU/显卡分配。

    ⚠️ 只是"预算分配倾向"，不是替用户做选择 —— 三个方案都给出，
       让用户自己挑（符合项目「不替用户做价值判断」原则）。
    """
    _orig = tune_ratio

    def _patched(base, ps):
        r, notes = _orig(base, ps)
        if cpu_mul != 1.0:
            r["cpu"] = r.get("cpu", 0) * cpu_mul
        if gpu_mul != 1.0 and r.get("gpu", 0) > 0:
            r["gpu"] = r["gpu"] * gpu_mul
        _t = sum(r.values())
        if _t > 0:
            r = {k: v / _t for k, v in r.items()}
        return r, notes

    globals()["tune_ratio"] = _patched
    try:
        return build_plan(parts, tier, budget, purposes=purposes,
                          explicit_dgpu=explicit_dgpu)
    finally:
        globals()["tune_ratio"] = _orig


def _variant_desc(plan, kind):
    """按方案实际组成生成特点说明（规则生成，不让模型编）。"""
    rows = {r["part"]: r for r in plan.get("rows", [])}
    cpu = rows.get("cpu") or {}
    gpu = rows.get("gpu") or {}
    total = plan.get("total") or 1
    cpu_pct = int(100 * (cpu.get("price") or 0) / total)
    gpu_pct = int(100 * (gpu.get("price") or 0) / total)
    has_dgpu = (gpu.get("price") or 0) > 0

    if kind == "gpu":
        return ("显卡占 %d%%（%s），游戏/AI 性能优先；CPU 够用即可"
                % (gpu_pct, (gpu.get("name") or "")[:16]))
    if kind == "cpu":
        return ("CPU 占 %d%%（%s），编译/剪辑/多任务优先；显卡让位"
                % (cpu_pct, (cpu.get("name") or "")[:16]))
    if has_dgpu:
        return ("CPU %d%% + 显卡 %d%%，两头均衡，说不准侧重时的稳妥选"
                % (cpu_pct, gpu_pct))
    return ("不配独显，预算集中 CPU（%d%%）和内存，纯办公/写代码够用"
            % cpu_pct)


def build_variants(parts, tier, budget, purposes=None, explicit_dgpu=None):
    """生成对比方案，**A 位是按用途最贴合的那个**。

    🔴 2026-11-27 用户要求：「打游戏一般是显卡优先，写代码是 cpu 优先」

        gaming / ai / threeD  → A 显卡优先  B 均衡  C CPU 优先
        dev / video           → A CPU 优先  B 均衡  C 显卡优先(有独显才出)
        office / 其他          → A 均衡      B CPU 优先（不出显卡优先）
    """
    ps = set(purposes or [])
    _gpu_first = bool(ps & {"gaming", "ai", "threeD"})
    _cpu_first = bool(ps & {"dev", "video"}) and not _gpu_first

    def _mk(cm, gm, kind):
        p = _variant_plan(parts, tier, budget, purposes, explicit_dgpu,
                          cpu_mul=cm, gpu_mul=gm)
        if p:
            p["variantKind"] = kind
        return p

    p_cpu = _mk(1.45, 0.70, "cpu")
    p_bal = _mk(1.0, 1.0, "balanced")
    p_gpu = _mk(0.80, 1.45, "gpu")

    # 只有真配了独显才保留显卡优先
    if p_gpu and not any(r["part"] == "gpu" and (r["price"] or 0) > 0
                         for r in p_gpu["rows"]):
        p_gpu = None
    # 🔴 2026-11-27：**不需要独显的用途，不出显卡优先方案**。
    #    判据两条（任一成立就不出）：
    #      ① explicit_dgpu 为假（用户没说要独显）
    #      ② 基准方案本身没配独显
    #    实测：写代码场景出了「显卡优先」配 RTX 5060（32%），
    #    用户只写代码，这笔钱是浪费。
    if p_gpu:
        if not explicit_dgpu:
            p_gpu = None
        elif p_bal:
            _b = [r for r in p_bal["rows"] if r["part"] == "gpu"]
            if _b and (_b[0]["price"] or 0) == 0:
                p_gpu = None

    if _gpu_first:
        order = [p_gpu, p_bal, p_cpu]
    elif _cpu_first:
        order = [p_cpu, p_bal, p_gpu]
    else:
        order = [p_bal, p_cpu, None]

    out, seen = [], set()
    for p in order:
        if not p or p["total"] in seen:
            continue
        seen.add(p["total"])
        out.append(p)

    for i, p in enumerate(out):
        _lbl = {"balanced": "均衡", "cpu": "CPU 优先",
                "gpu": "显卡优先"}[p["variantKind"]]
        _base = p["tierName"].split(" · 方案")[0]
        p["tierName"] = "%s · 方案 %s · %s" % (_base, "ABC"[i], _lbl)
        p["variantNote"] = _variant_desc(p, p["variantKind"])
    return out


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
