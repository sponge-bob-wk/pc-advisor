# -*- coding: utf-8 -*-
"""derive_notes.py —— 从数据推导「客观提示」，补足无人工评价的机器

## 要解决的问题

`agent` 现在只有 **34 台**有人工写的 pros/cons（来自 `prices.json`）。
推荐池 223 台里其余 189 台，用户问「有什么缺点」时只能答"没评价" ——
这等于把最常见的追问挡在门外。

## 为什么不能靠抓评测文章自动抽

2026-11-16 实测试过两条路，都不可靠：

1. **ZOL 点评页**：`class="empty-comment"` —— 绝大多数机型没有用户点评。
2. **ZOL 评测文章**：能抓到正文，但用「但/不过/不足」这类转折词抽缺点，
   抽出来的多半是**导购文里对多台机器的泛泛比较**（"但这游戏对硬件要求不低"），
   不是本机缺点。硬抽等于把噪声当结论。

项目红线写着「没数据 ≠ 有数据」「宁可弃权不许自信地做错」，
所以**不写主观评价**。

## 那就换一种：写「客观提示」

人工层的 103 条缺点里，主题分布是：

    内存形态（封装/板载/焊死）  19 条
    重量                        12 条
    屏幕（色域/素质）           11 条
    售后网点                    11 条

**这四类全都能从数据推导**，而且不需要主观判断：

    upgrade == 0           → 「内存焊死，买定没有升级空间」（事实）
    weightKg > 2.3         → 「2.45kg，通勤背着累」（事实 + 常识）
    gpuKind == 'integrated'→ 「核显，不适合跑独显需求的任务」（事实）
    efficiency 缺失        → 「未查到能效备案，国补不一定拿得到」（事实）

这些不是"评价"，是**从规格直接推出的事实陈述**。区别在于：
评价说"这台做工一般"，我们只说"1.98kg / 核显 / 内存焊死"——
用户自己会判断。

## 与人工层的关系（不许覆盖）

- 人工层有 pros/cons → **一个字都不动**
- 只在 `derivedNotes` 字段写推导结果，与 `pros`/`cons` 分开存
- `agent` 回答时优先人工层，没有才用 derivedNotes，并**明说这是从规格推的**

## 用法

    python derive_notes.py              # 统计能推导出多少
    python derive_notes.py --write      # 写进 machine_pool.json 的 derivedNotes
"""
import argparse
import json
import os
import shutil
import sys
import time
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
POOL = os.path.join(ROOT, "data", "machine_pool.json")

sys.path.insert(0, HERE)
import core  # noqa: E402  （要 core.effective_weight，见重量那段注释）

# 🔴 每条规则都必须只陈述**数据里有的事实**，不许带主观形容词。
#    「内存焊死，买定没有升级空间」是事实（upgrade=0 意味着物理上不可换）。
#    「做工一般」不是事实，是评价 —— 不许写。
#    阈值也都写清来源，避免下一个人以为是我拍的。


def derive(m):
    """返回 [(kind, text, basis)]，kind ∈ {'con','pro','note'}。"""
    out = []
    ram = m.get("ram") or {}
    gpu = m.get("gpu") or {}
    scr = m.get("screen") or {}

    # --- 内存形态（人工层 19/103 条缺点都在说这个）---
    # 🔴 只在**证据明确**时才下结论：
    #    upgrade==0 或 form 明确板载 → con（可换性是 0，事实）
    #    slots>=2 且 form 不是板载 → pro（有物理插槽，事实）
    #    **upgrade 为 None 且只有 slots 时不下 pro** —— 曾出现「upgrade=None / slots=2
    #    就说'内存有插槽'」，但 upgrade 未知意味着"能不能加"其实没核实过。
    #    宁可少说一条，不许把未核实说成事实（项目红线第 2 条）。
    up = m.get("upgrade")
    form = (ram.get("form") or "").lower()
    slots = ram.get("slots")
    gb = ram.get("gb")
    soldered = (up == 0 or form in ("onboard", "soldered", "板载", "焊死")
                or slots == 0)
    if soldered:
        cap = ("，%sG 就是顶格" % gb) if gb else ""
        out.append(("con", "内存是板载焊死的，买定没有升级空间%s" % cap,
                    "upgrade=%s / ram.form=%s / slots=%s" % (up, form or "-", slots)))
    elif (isinstance(slots, int) and slots >= 2
          and form in ("sodimm", "so-dimm", "插槽", "socketed", "slot")):
        # 只有 form **明确写成插槽式**才算 pro。
        # `form` 为空 + slots=2 的情况：slots 可能来自参数页的"2xSO-DIMM"，
        # 但 form 没归一化 —— 证据不完整，不下结论（少说一条，不猜）。
        out.append(("pro", "内存有插槽，以后能自己加",
                    "ram.slots=%s / ram.form=%s" % (slots, form)))

    # --- 重量（人工层 12/103 条）---
    # 🔴 必须走 `core.effective_weight()`，不许直接读 `m["weightKg"]`。
    #    `test_weight.py` 的 W8 是一条**结构性检查**：出口层出现裸 weightKg 就报错。
    #    原因（见该测试的注释）：重量有四个出口（brief / build_recommendation /
    #    server 投影 / 字段卡），各读各的字段迟早会漂 —— 曾经同一台机器
    #    在正文说 1.48kg、字段卡说 2.1kg。
    #    `effective_weight` 是唯一入口：它会在结构化字段为空时从 pros/cons 文本兜底。
    w = core.effective_weight(m)
    if isinstance(w, (int, float)):
        if w >= 2.3:
            out.append(("con", "重 %skg，天天背着通勤会累" % w,
                        "effective_weight=%s（≥2.3kg 视为不便携，与人工层口径一致）" % w))
        elif w <= 1.5:
            out.append(("pro", "只有 %skg，带着走不累" % w,
                        "effective_weight=%s（≤1.5kg 视为便携）" % w))

    # --- 显卡形态（事实，不是评价）---
    # 🔴 核显归 `con` 而不是 `note`：对"问缺点"的用户来说这是**不利事实**。
    #    实测（2026-11-16，战66）：核显写成 note 时，模型不把它当短板，
    #    4 条里一条 con 都没有，于是输出了「库里记的缺点是：。」这种空模板。
    #    核显不是"中性信息"—— 它直接决定能不能跑独显需求的任务。
    gk = (gpu.get("type") or "").lower()
    vram = gpu.get("vramGB")
    if gk == "integrated" or vram == 0:
        out.append(("con", "核显机型，没有独立显存，跑不了要独显的任务",
                    "gpu.type=%s / vramGB=%s" % (gk or "-", vram)))
    elif gk == "discrete" and vram:
        out.append(("pro", "独显 %sG，能跑需要独显的软件" % vram,
                    "gpu.type=discrete / vramGB=%s" % vram))

    # --- 屏幕色域（人工层 11/103 条）---
    gam = scr.get("colorGamut") or {}
    if gam:
        srgb = gam.get("srgb")
        p3 = gam.get("dciP3")
        best = p3 or srgb
        if p3 and p3 >= 90:
            out.append(("pro", "色域覆盖 DCI-P3 %s%%，修图看片颜色够准" % p3,
                        "screen.colorGamut.dciP3=%s" % p3))
        elif srgb and srgb < 70:
            out.append(("con", "色域只有 sRGB %s%%，修图看片颜色会发灰" % srgb,
                        "screen.colorGamut.srgb=%s（<70%% 视为低色域）" % srgb))

    # --- 能效备案（影响国补，人工层也在意）---
    # 同样归 con：拿不到国补是**实际损失**，不是中性信息。
    if m.get("efficiency") is None:
        out.append(("con", "未查到能效备案，国补不一定拿得到",
                    "efficiency 为空"))

    # --- 跑分缺失（决定能不能比"快多少"）---
    cpu = ((m.get("cpu") or {}).get("model") or "").strip()
    if cpu and m.get("perf") is None and m.get("_noBench"):
        out.append(("note", "这颗 CPU 没有跑分数据，比不了快慢",
                    "cpu=%s 不在 bench.json" % cpu))

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    pool = json.load(open(POOL, encoding="utf-8"))
    ms = pool["machines"]

    # 人工层有 pros/cons 的 id（来自 prices.json）
    pj = os.path.join(ROOT, "data", "prices.json")
    human = set()
    if os.path.exists(pj):
        prices = json.load(open(pj, encoding="utf-8"))
        for o in prices.get("offers") or []:
            if o.get("pros") or o.get("cons"):
                human.add(o.get("name"))

    n_have = n_derived = n_none = 0
    kinds = Counter()
    samples = []
    for m in ms:
        if m.get("name") in human:
            n_have += 1
            continue
        notes = derive(m)
        if not notes:
            n_none += 1
            continue
        n_derived += 1
        for k, _t, _b in notes:
            kinds[k] += 1
        if len(samples) < 6:
            samples.append((m["name"], notes))
        if args.write:
            m["derivedNotes"] = [{"kind": k, "text": t, "basis": b}
                                 for k, t, b in notes]
            bf = set(m.get("backfilled") or [])
            bf.add("derivedNotes")
            m["backfilled"] = sorted(bf)

    total = len(ms)
    print("机型池 %d 台" % total)
    print("  人工层已有评价（不动）: %d" % n_have)
    print("  能推导出客观提示     : %d" % n_derived)
    print("  一条都推不出         : %d" % n_none)
    print()
    print("推导条目按类型：")
    for k, v in kinds.most_common():
        print("  %-6s %d" % (k, v))
    print()
    print("样例：")
    for name, notes in samples:
        print("  %s" % name[:34])
        for k, t, b in notes:
            print("     [%s] %s" % (k, t[:44]))
            print("          依据：%s" % b[:60])

    if args.write:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print()
        print("已写入 %s（备份 %s）" % (POOL, bak))
    else:
        print()
        print("（演练模式。加 --write 落盘）")


if __name__ == "__main__":
    main()
