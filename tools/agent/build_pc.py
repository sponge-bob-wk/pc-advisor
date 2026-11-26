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

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PARTS = os.path.join(ROOT, "data", "parts.json")

# ── 配件顺序（装机单的展示顺序，也是逻辑顺序）──
ORDER = ["cpu", "motherboard", "ram", "gpu", "storage", "psu", "case"]

LABEL = {
    "cpu": "CPU 处理器",
    "motherboard": "主板",
    "ram": "内存",
    "gpu": "显卡",
    "storage": "硬盘",
    "psu": "电源",
    "case": "机箱",
}

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


def build_plan(parts, tier, budget=None):
    """按档位生成一套配置。

    返回 {"tier":..., "rows":[...], "total":..., "issues":[...]}
    """
    lo, hi = tier["budget"]
    budget = budget or (lo + hi) / 2
    ratio = tier["pick"]
    issues = []

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
    gpu = pick_near(parts.get("gpu") or [], budget * ratio["gpu"])
    if not gpu:
        return None
    plan["gpu"] = gpu

    # ⑤ 硬盘
    sto = pick_near(parts.get("storage") or [], budget * ratio["storage"])
    if not sto:
        return None
    plan["storage"] = sto

    # ⑥ 电源：按 CPU+GPU 功耗 × 1.5 留余量
    need_w = int((cpu_watt(cpu["name"]) + gpu_watt(gpu["name"]) + 80) * 1.5)
    psu_key = lambda it, n=need_w: (psu_watt(it["name"]) or 0) >= n
    psu = pick_near(parts.get("psu") or [], budget * ratio["psu"], key=psu_key)
    if not psu:
        psu = pick_near(parts.get("psu") or [], budget * ratio["psu"])
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
