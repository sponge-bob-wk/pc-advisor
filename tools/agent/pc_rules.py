# -*- coding: utf-8 -*-
"""pc_rules.py —— 装机规则库（每个部件「为什么这么配」的判据）

## 为什么单独一个文件

原来 `build_pc.py` 里散着一堆魔法数字：
    ratio = {"cpu": 0.20, "gpu": 0.38, ...}
    if ps & {"video", "threeD", "ai"}: minRam = 32
    if budget >= 7000: 加机械盘

这些数字**没有出处**，改的时候不知道能改到哪、改了会影响什么。
而且实战暴露了一堆问题：
    · 办公配了 GTX 1650（不需要独显）
    · i5-12490F 配 RTX 5060 Ti（CPU 瓶颈）
    · 办公配 1250W 电源（按预算比例选是错的）
    · 打游戏只给 500G 固态（3A 游戏一个 100G+）

所以把「每个部件的选型规则」抽成一个**可查、可解释**的表：

    RULES[用途] = {
        "部件": {"min": 最低要求, "why": 为什么},
        ...
    }

`build_plan` 按这张表筛，`issues` 里能说清「为什么给你配这个」。

## 规则从哪来

不是拍脑袋，三条来源：
  ① 厂商官方推荐（Adobe/微软/游戏厂商的配置要求）
  ② 装机社区的共识（CPU 显卡搭配比例、电源余量）
  ③ 本项目实测踩的坑（见每个规则的注释）

## 一条元规则

**「够用」优先于「便宜」**。
学生装机最常见的错误是"预算全砸显卡，其他配件凑合"，
结果 CPU 瓶颈、固态塞满、电源虚标 —— 三年后整个平台都得换。
所以每个部件都有 min，不能因为预算紧就无限压缩。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════
# 一、各用途的部件硬约束
#
# 格式：用途 → 部件 → (最低要求, 理由)
#
# 「最低要求」的含义按部件不同：
#   cpu/gpu/ram/storage  → 价格或容量的下限
#   psu                  → 按功耗算，不在这里管
#   motherboard          → 跟随 CPU 插槽，不在这里管
# ══════════════════════════════════════════════════════════════════

RULES = {
    # ── 打游戏 ──
    "gaming": {
        "minRam": (16, "16G 是 2026 年游戏底线；32G 更好但预算紧时 16G 够"),
        "minSsdGB": (1000, "3A 游戏单个 80-150G，500G 装两个就满了"),
        "minCpuRatio": (0.18, "CPU 不能太弱，否则喂不饱显卡（实测 i5-12490F "
                              "配 RTX 5060 Ti 会瓶颈）"),
        "needDgpu": (True, "游戏必须独显，核显跑不动 3A"),
        "wantReview": ("gpu", "显卡是游戏机第一重要的部件"),
    },

    # ── 平面设计 / 修图 ──
    "design": {
        "minRam": (32, "PS/AI 多图层叠加很吃内存，16G 开三个大图就报错"),
        "minSsdGB": (1000, "素材库 + 软件缓存，512G 不够"),
        "minCpuRatio": (0.20, "设计软件吃单核性能和多核，CPU 不能省"),
        "needDgpu": (False, "平面设计不吃独显（majors.json 里 tagline "
                            "就是「吃色准和内存，不吃独显」）"),
        "wantReview": ("screen", "设计最看重屏幕色域，但库里色域数据缺失多"),
    },

    # ── 视频剪辑 ──
    "video": {
        "minRam": (32, "4K 剪辑的缓存区很吃内存，PR 官方推荐 32G 起"),
        "minSsdGB": (2000, "视频素材体积极大，1T 只够一个项目"),
        "minCpuRatio": (0.22, "编码解码靠 CPU 多核，是剪辑的命门"),
        "needDgpu": (True, "硬件编码加速（NVENC）能快 3-5 倍"),
        "wantReview": ("cpu", "剪辑第一看 CPU 多核性能"),
    },

    # ── 3D 建模渲染 ──
    "threeD": {
        "minRam": (32, "场景和贴图加载很吃内存，16G 打开大场景直接崩"),
        "minSsdGB": (1000, "模型和贴图库体积大"),
        "minCpuRatio": (0.24, "渲染主要靠 CPU 多核（除非用 GPU 渲染器）"),
        "needDgpu": (True, "实时预览和 GPU 渲染都需要独显"),
        "wantReview": ("cpu", "渲染速度取决于 CPU 核心数"),
    },

    # ── 本地跑模型 ──
    "ai": {
        "minRam": (32, "加载模型权重时内存和显存一起占"),
        "minSsdGB": (2000, "模型文件动辄 10-50G，要存多个版本"),
        "minCpuRatio": (0.12, "CPU 不是瓶颈，够用即可"),
        "needDgpu": (True, "显存决定能跑多大的模型，这是硬门槛"),
        "wantReview": ("gpu", "第一看显存，第二看显存带宽"),
    },

    # ── 写代码 ──
    "dev": {
        "minRam": (32, "IDE + 虚拟机 + 浏览器几十个标签，16G 第三年就不够"),
        "minSsdGB": (1000, "项目文件 + Docker 镜像占空间"),
        "minCpuRatio": (0.20, "编译和多任务吃 CPU"),
        "needDgpu": (False, "本科/日常开发不需要独显，除非做深度学习"),
        "wantReview": ("ram", "第一看内存，第二看能否加装"),
    },

    # ── 办公 / 网课 ──
    "office": {
        "minRam": (16, "Office + 浏览器够用；32G 是浪费"),
        "minSsdGB": (500, "文档不占空间，512G 够用四五年"),
        "minCpuRatio": (0.22, "办公吃单核和响应速度，CPU 别太差"),
        "needDgpu": (False, "办公完全不需要独显，那笔钱给 SSD 更值"),
        "wantReview": ("none", "办公不看特定部件，稳定够用就行"),
    },
}

# 没说用途时的默认（保守配置）
DEFAULT_RULE = {
    "minRam": (16, "没说用途时按通用标准配 16G"),
    "minSsdGB": (1000, "1T 是通用甜点，装完系统还有空间"),
    "minCpuRatio": (0.18, "CPU 占总预算至少 18%，避免头重脚轻"),
    "needDgpu": (True, "没说用途时保守给独显"),
    "wantReview": ("none", ""),
}


def rule_for(purposes):
    """把多个用途合成一条规则（取最严格的）。

    比如「设计 + 剪视频」→ 内存要 32G（视频的要求）、
    固态要 2T（视频的要求）、要独显（视频的要求）。
    """
    ps = [p for p in (purposes or []) if p in RULES]
    if not ps:
        return dict(DEFAULT_RULE)

    merged = {}
    for key in ("minRam", "minSsdGB", "minCpuRatio"):
        # 取最严格的那个（数值最大）
        vals = [RULES[p][key] for p in ps if key in RULES[p]]
        if vals:
            merged[key] = max(vals, key=lambda x: x[0])
        else:
            merged[key] = DEFAULT_RULE[key]

    # needDgpu：任一用途需要就给
    need = [RULES[p]["needDgpu"] for p in ps if "needDgpu" in RULES[p]]
    if need:
        # 取 True 优先（有任何一个需要就配）
        merged["needDgpu"] = next((x for x in need if x[0]), need[0])
    else:
        merged["needDgpu"] = DEFAULT_RULE["needDgpu"]

    # wantReview：取第一个非 none 的
    wr = [RULES[p].get("wantReview") for p in ps]
    wr = [x for x in wr if x and x[0] != "none"]
    merged["wantReview"] = wr[0] if wr else DEFAULT_RULE["wantReview"]

    return merged


# ══════════════════════════════════════════════════════════════════
# 二、通用工程规则（跟用途无关，所有配置都适用）
# ══════════════════════════════════════════════════════════════════

# ── 电源：只按功率选，不按价格选 ──
#
# 🔴 实测坑：按预算比例选电源，办公配置里出现了 1250W 电源。
#    电源不是越贵越好 —— 够用就行，多的钱给 CPU/显卡。
#
# 余量取 1.5 倍：
#   ① 电源在 50-60% 负载时效率最高、最安静
#   ② 显卡有瞬时功耗尖峰（比标称高 30-50%）
#   ③ 留升级空间（以后换更强的显卡不用换电源）
PSU_HEADROOM = 1.5
PSU_BASE_LOAD = 80      # 主板+内存+固态+风扇的固定开销

# ── CPU/显卡配比 ──
#
# 🔴 实测坑：i5-12490F（¥1280）配 RTX 5060 Ti（¥4199）。
#    显卡价格是 CPU 的 3.3 倍 —— 这是典型的"头重脚轻"：
#    游戏时 CPU 先满载，显卡的算力发挥不出来，等于白花钱。
#
# 经验比例：显卡价格不宜超过 CPU 的 2.5 倍。
# （这个值来自装机社区的共识，不是精确科学，但足以抓住离谱配置）
MAX_GPU_CPU_RATIO = 2.5

# ── 固态容量底线 ──
#
# 256G 装完 Windows + 常用软件就剩不到 100G，2026 年根本不够。
# 500G 是绝对底线，1T 是甜点。
SSD_ABS_FLOOR_GB = 500

# ── 机械盘：什么时候加 ──
#
# 只在预算充足时加，作用是存素材/游戏库，不装系统。
HDD_MIN_BUDGET = 7000    # 低于这个预算不加机械盘，钱花在刀刃上

# ── 内存：代数必须跟主板一致 ──
#
# AM5 / LGA1851 只有 DDR5；AM4 只有 DDR4；LGA1700 两种都有。
# 这个映射在 build_pc.board_ram_gen 里，这里只是记一下原则。

# ══════════════════════════════════════════════════════════════════
# 三、解释生成
# ══════════════════════════════════════════════════════════════════

def explain(rule, purposes):
    """把规则翻译成人话，放进装机单的「需要确认」区。

    用户看到的不是"minSsdGB=1000"，而是
    「固态至少 1T —— 3A 游戏单个 80-150G，500G 装两个就满了」
    """
    ps = purposes or []
    out = []

    if ps:
        _cn = {
            "gaming": "打游戏", "design": "平面设计", "video": "视频剪辑",
            "threeD": "3D 建模", "ai": "本地跑模型", "dev": "写代码",
            "office": "办公",
        }
        names = [_cn.get(p, p) for p in ps]
        out.append("按「%s」配的" % " + ".join(names[:2]))

    for key, label in (("minRam", "内存"),
                       ("minSsdGB", "固态")):
        v = rule.get(key)
        if v and v[1]:
            unit = "G" if key != "minSsdGB" else "G"
            out.append("%s至少 %d%s（%s）" % (label, v[0], unit, v[1]))

    nd = rule.get("needDgpu")
    if nd and not nd[0]:
        out.append("不配独显（%s）" % nd[1])

    return out
