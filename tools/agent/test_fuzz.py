# -*- coding: utf-8 -*-
"""乱测夹具（fuzz）：不预测用户会说什么，只检查**不许发生的事**。

为什么要有（2026-11-12 用户原话：
  「按你的思路还是去列举所有可能的说法把他们归属到现有的分支中？
    没有真正的扛得住我乱测的方法吗」）

  答案是：**能枚举的是说法，不能枚举的是输入**。所以这里不写"这句话应该走哪个分支"，
  只立四条不变量 —— 任何说法都不许破：

    I1 不许装听懂：条件没改、也没问具体项，却把同一台再推一遍  → 违规
    I2 不许改品类：句中没明说品类，cat 却变了                  → 违规
    I3 不许编数字：untraceable_numbers 非空                     → 违规
    I4 不许空手而归：任何 stage 的 reply 都要有实质内容          → 违规

  这四条都对"输入长什么样"免疫。跑法：随机/穷举一堆说法喂进去，只数违规。
  新增的说法不需要维护期望值，只要跑一遍 —— 这就是它和普通单测的区别。

用法：
    python test_fuzz.py            # 跑内置组合
    python test_fuzz.py 200        # 跑 200 条（含随机拼接）
    python test_fuzz.py --gen 60   # 让模型现场生成 60 句"真实用户会说的话"再跑
"""
import itertools
import os
import random
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent
import core

# ── 说法素材：前缀 × 动作。不是"全集"，只是**足够杂**的采样池 ──────────
PREFIX = ["", "这台", "这个", "刚才那台", "它", "这两个", "另外的",
          "你刚说的", "这几个", "剩下那些", "还有", "别的"]
ACTION = ["怎么样", "值不值", "能不能便宜点", "还有吗", "列一下",
          "换一个", "哪个好", "适合我吗", "有什么区别", "说说看",
          "再看看", "有没有更好的", "就这样吧", "为什么", "行不行",
          "贵不贵", "多重", "保修多久", "能打游戏吗", "够用吗"]

BASE = "预算 7000，学设计，要带去教室"

# 判"这句是不是明确在换台/改条件" —— 只有这些情况允许同一台再出现
SWITCHISH = ["换一个", "换一台", "还有吗", "有没有更好的", "再看看", "别的"]
CHANGEISH = ["预算", "想买", "要 ", "不要"]
# 明确表达"用途"的说法 —— 用途本来就该驱动品类（说打游戏→游戏本）。
# 第一版 I2 不认这个，把「我就是要玩游戏」误判成"偷偷改品类"。
# 这正说明**判据本身也要有分辨力**，不然夹具会制造假 bug。
PURPOSE_WORDS = ["游戏", "打游戏", "网游", "3A", "办公", "上课", "写作业",
                 "剪视频", "写代码", "敲代码", "设计", "建模"]


def build_cases(limit=None, seed=7):
    cases = []
    for p, a in itertools.product(PREFIX, ACTION):
        s = (p + a).strip()
        if len(s) < 2:
            continue
        cases.append(s)
    random.Random(seed).shuffle(cases)
    if limit:
        cases = cases[:limit]
    return cases


def run(cases):
    st = {"known": None, "focus": None, "seen": None}
    rows = []
    # 先给一个真实起点，让后面每句都在"已经在聊某一台"的上下文里
    first = agent.run(BASE, known=None, asked=None, last_stage=None,
                      seen=None, focus=None)
    prev = {"top": first.get("picked"), "cat": (first.get("slots") or {}).get("cat"),
            "stage": first.get("stage")}
    st["known"] = first.get("known") or first.get("slots")
    st["focus"] = first.get("focus")
    st["seen"] = first.get("seen")

    for text in cases:
        try:
            r = agent.run(text, known=st["known"], asked=None, last_stage=None,
                          seen=st["seen"], focus=st["focus"])
        except Exception as e:                     # I0 不许崩
            rows.append((text, "I0 崩溃", repr(e)[:120]))
            continue
        sl = r.get("slots") or {}
        top = r.get("picked")
        v = []

        # I1 不许装听懂：又推了同一台，而且这句既不是换台也不是改条件
        explicit = any(w in text for w in SWITCHISH) or any(w in text for w in CHANGEISH)
        if (r.get("stage") == "recommend" and prev["top"] and top == prev["top"]
                and not explicit):
            v.append("I1 同一台连推（上一轮也是它）")
        # I2 不许偷偷改品类
        # 例外：句中明确表达了用途（"我就是要玩游戏"）—— 那本来就该换品类。
        _purpose_said = any(w in text for w in PURPOSE_WORDS)
        if (prev["cat"] and sl.get("cat") and sl["cat"] != prev["cat"]
                and not agent.CAT_EXPLICIT.search(text) and not _purpose_said):
            v.append("I2 品类被改 %s→%s" % (prev["cat"], sl["cat"]))
        # I3 不许编数字
        if r.get("untraceable_numbers"):
            v.append("I3 数字不可追溯 %s" % r["untraceable_numbers"])
        # I4 不许空手而归
        if len((r.get("reply") or "").strip()) < 8:
            v.append("I4 回复为空/过短")

        if v:
            rows.append((text, "；".join(v), "%s | %s" % (r.get("stage"), top)))
        prev = {"top": top or prev["top"], "cat": sl.get("cat") or prev["cat"],
                "stage": r.get("stage")}
        st["known"] = r.get("known") or st["known"]
        if r.get("focus") is not None:
            st["focus"] = r.get("focus")
        st["seen"] = r.get("seen") or st["seen"]
    return rows


# ── 模型现场生成"真实用户会说的话" ────────────────────────────────
# 只在**测试**里开温度：生产链路是 temperature=0（模型只做转述，不做创作）。
# 这里要的恰恰是"不受控" —— 目的就是撞出人手写不出来的说法。
GEN_SYS = """你在模拟一个真实用户。他是一名刚考上大学的准大学生，正在挑笔记本，预算 5000-8000。
你的任务：写出他**下一句**会对导购说的话。

要求：
1. 只输出那一句话本身，不要引号、不要解释、不要编号。
2. 10-30 个字，口语，像微信里随手打的。
3. 可以含糊、可以跳跃、可以突然想起一个条件、可以质疑、可以不客气。
4. 不要重复上面已经说过的内容。
5. 不要出现"我是一个AI"这类自我描述。
"""

SEEDS = [
    "用户：预算 7000，学设计，要带去教室。导购：推荐了联想小新 16，说屏幕素质同价位不算突出。",
    "用户：我是人工智能专业大一新生。导购：推荐惠普战 66，双内存槽可加装，屏幕色域是短板。",
    "用户：8000 打游戏，宿舍限电 500W。导购：推荐暗影精灵 11（RTX5060），说可能跳闸。",
    "用户：就要轻薄本，能带就行，但也要能打点游戏。导购：说了同价位轻薄本没有独显。",
]


def gen_utterances(n, seed=11):
    outs = []
    rnd = random.Random(seed)
    tries = 0
    while len(outs) < n and tries < n * 3:
        tries += 1
        ctx = SEEDS[len(outs) % len(SEEDS)] if rnd.random() < 0.6 else \
            SEEDS[rnd.randrange(len(SEEDS))]
        try:
            txt = agent.chat(
                [{"role": "system", "content": GEN_SYS},
                 {"role": "user", "content": "上文（供参考，不要重复）：" + ctx
                  + "\n\n只输出用户下一句话。"}],
                num_predict=48, temperature=0.8).strip()
        except Exception as e:
            print("  生成失败：%s" % e)
            break
        txt = txt.strip("“”\"'` \n")
        txt = re.sub(r"^(用户[:：]|A[:：])", "", txt).strip()
        if not (2 <= len(txt) <= 40):
            continue
        if not re.search(r"[\u4e00-\u9fa5]", txt):
            continue
        if txt in outs:
            continue
        outs.append(txt)
    return outs


def main():
    argv = sys.argv[1:]
    gen = False
    if argv and argv[0] == "--gen":
        gen = True
        argv = argv[1:]
    limit = int(argv[0]) if argv else 60

    if gen:
        cases = gen_utterances(limit)
        print("=" * 66)
        print("乱测夹具 · 模型现场生成 %d 句（temp 0.8，只在测试里开）" % len(cases))
        print("=" * 66)
        for i, c in enumerate(cases, 1):
            print("  %2d. %s" % (i, c))
    else:
        cases = build_cases(limit)
        print("=" * 66)
        print("乱测夹具：%d 条说法 × 4 条不变量（不预设它们该走哪条分支）" % len(cases))
        print("=" * 66)

    rows = run(cases)
    print("\n违规 %d / %d 条" % (len(rows), len(cases)))
    for text, inv, ctx in rows[:40]:
        print("  ✗ %-16s %s\n      ↳ %s" % (text, inv, ctx))
    if len(rows) > 40:
        print("  … 另有 %d 条" % (len(rows) - 40))
    print("=" * 66)
    return 0 if not rows else 1


if __name__ == "__main__":
    sys.exit(main())
