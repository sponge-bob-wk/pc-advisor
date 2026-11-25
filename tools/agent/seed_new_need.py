# -*- coding: utf-8 -*-
"""seed_new_need.py —— 补充「我要 X」类 state_new_need 样本

## 为什么需要

实测：「我要一台游戏本」被判成 state_range（应该 state_new_need）。

查训练集：
  · 只有 3 条「想买+品类」样本，且**全标成了 state_range**（标错）
  · state_new_need 共 96 条，但「要/买 + 品类」这个模式几乎没有

**模型没见过「我要游戏本 → state_new_need」这个映射，所以按
「品类描述 → state_range」处理了。**

## 语义边界（按用户确认）

  「我要一台游戏本」        → state_new_need（加条件）
  「想买轻薄本」            → state_new_need（加条件）
  「6000 的游戏本」          → state_range（说范围）
  「六七千的轻薄本」         → state_range（说范围）
  「一万以内」              → state_range（说范围）

**判据**：有「我要/想买/想要/买个/来一台」等**主动索取动词** → new_need
        纯描述价位/品类 → range

## 生成策略

造 40 条正例（要+品类）覆盖不同说法，
再造 10 条对照负例（纯范围描述，标 state_range）防止模型过度泛化。
"""
import io
import json
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")

OUT = r"E:\电脑涨价分析\tools\agent\lora_data\new-need-seed.jsonl"

# ── 正例：「要/买 + 品类」→ state_new_need ──
# (问句, topic, fit, 思维链)
POS = [
    # 「我要 + 品类」
    ("我要一台游戏本", "none", False,
     "「我要」是主动索取，加了个品类条件 → state_new_need"),
    ("我要个轻薄本", "none", False,
     "「我要」+ 品类 = 加条件"),
    ("我要一台台式机", "none", False,
     "「我要」+ 品类 = 加条件"),
    ("我要轻薄本", "none", False,
     "「我要」+ 品类 = 加条件"),
    # 「想买 + 品类」
    ("想买轻薄本", "none", False,
     "「想买」是索取动作，品类是条件 → state_new_need"),
    ("想买游戏本", "none", False,
     "「想买」+ 品类 = 加条件"),
    ("想买台式机", "none", False,
     "「想买」+ 品类 = 加条件"),
    ("我想买个笔记本", "none", False,
     "「想买」+ 品类 = 加条件"),
    # 「想要 + 品类」
    ("我想要一台游戏本", "none", False,
     "「想要」+ 品类 = 加条件"),
    ("想要个轻薄点的", "none", False,
     "「想要」是索取动作"),
    ("我想要台式的", "none", False,
     "「想要」+ 品类 = 加条件"),
    # 「来一台 / 来个」
    ("来一台游戏本", "none", False,
     "「来一台」是索取动作 → state_new_need"),
    ("给我来个轻薄本", "none", False,
     "「给我来」+ 品类 = 加条件"),
    ("来个台式机吧", "none", False,
     "「来个」+ 品类 = 加条件"),
    # 「帮我找 / 推荐」
    ("帮我找台游戏本", "none", False,
     "「帮我找」是索取动作 → state_new_need"),
    ("给我推荐个游戏本", "none", False,
     "「给我推荐」是索取动作"),
    ("帮我挑台轻薄本", "none", False,
     "「帮我挑」是索取动作"),
    ("推荐一台台式机", "none", False,
     "「推荐」是索取动作"),
    # 「换 + 配置」
    ("换个 32G 的", "none", False,
     "「换个 32G」是加配置条件 → state_new_need"),
    ("我要 32G 内存的", "none", False,
     "「我要」+ 配置要求 = 加条件"),
    ("想要带独显的", "none", False,
     "「想要」+ 配置要求 = 加条件"),
    ("我要能跑 PS 的", "none", False,
     "「我要」+ 用途要求 = 加条件"),
    ("要个轻点的", "none", False,
     "「要个」+ 属性要求 = 加条件"),
    ("得要独显的", "none", False,
     "「得要」+ 配置要求 = 加条件"),
    ("我需要 16G 内存", "none", False,
     "「我需要」+ 配置要求 = 加条件"),
    # 口语化变体
    ("整一台游戏本", "none", False,
     "「整一台」是口语化索取 → state_new_need"),
    ("搞个轻薄本", "none", False,
     "「搞个」是口语化索取"),
    ("弄台台式吧", "none", False,
     "「弄台」是口语化索取"),
    ("给我整个能打游戏的", "none", False,
     "「给我整个」+ 用途 = 加条件"),
    ("我想要个大屏的", "none", False,
     "「想要」+ 屏幕要求 = 加条件"),
    ("要屏幕好点的", "none", False,
     "「要」+ 屏幕要求 = 加条件"),
    ("要续航长点的", "none", False,
     "「要」+ 续航要求 = 加条件"),
    ("想要个轻薄的游戏本", "none", False,
     "「想要」+ 品类和属性 = 加条件"),
    ("我要能跑设计的", "none", False,
     "「我要」+ 用途 = 加条件"),
    ("给我找一个一万以内的", "none", False,
     "「给我找」是索取动作 → state_new_need"),
    ("要个能带出门的", "none", False,
     "「要个」+ 便携要求 = 加条件"),
    ("想要接口多的", "none", False,
     "「想要」+ 接口要求 = 加条件"),
    ("我要 1T 硬盘的", "none", False,
     "「我要」+ 存储要求 = 加条件"),
    ("需要个能跑 AI 的", "none", False,
     "「需要」+ 用途 = 加条件"),
    ("要台散热好的", "none", False,
     "「要」+ 散热要求 = 加条件"),

    # ── 补充：「预算 + 索取动词」组合（2026-11-25 加）──
    # 为什么补：训练集里这个模式标注不一致 ——
    #   「预算 6000 学设计，要带去教室」→ state_new_need ✓
    #   「预算 7000，主要打游戏」       → state_range ✗
    #   「预算9000，打游戏，要游戏本」   → state_range ✗
    # 判据：有「想买/要/买」这类索取动词 → new_need
    ("预算 6000 想买轻薄本", "none", False, "「想买」是索取动作，预算是条件 → state_new_need"),
    ("预算6000想买轻薄本", "none", False, "同上（无空格）"),
    ("预算 7000，主要打游戏", "none", False, "「打游戏」是用途要求 → 加条件"),
    ("预算9000，打游戏，要游戏本", "none", False, "「要游戏本」是索取 → state_new_need"),
    ("预算八千，想买个轻的", "none", False, "「想买」+ 属性 → 加条件"),
    ("一万预算，要能跑设计", "none", False, "「要能跑」+ 用途 → 加条件"),
    ("六千左右，想买游戏本", "none", False, "「想买」+ 品类 → 加条件"),
    ("预算一万以内想要独显的", "none", False, "「想要」+ 配置 → 加条件"),
    ("五千预算，要带得动的", "none", False, "「要」+ 便携要求 → 加条件"),
    ("预算8000左右，想买台游戏本", "none", False, "「想买」+ 品类 → 加条件"),
]

# ── 负例：纯范围描述 → state_range ──
NEG = [
    ("6000 的游戏本", "none", False,
     "纯价位+品类描述，没有索取动作 → state_range"),
    ("六七千的轻薄本", "none", False,
     "纯价位区间描述 → state_range"),
    ("一万以内", "none", False,
     "纯价位范围 → state_range"),
    ("八千左右的台式机", "none", False,
     "纯价位+品类 → state_range"),
    ("五千到七千这个区间", "none", False,
     "纯价位区间 → state_range"),
    ("游戏本", "none", False,
     "只有品类词，无索取动作 → state_range"),
    ("轻薄本这个价位", "none", False,
     "品类+价位描述 → state_range"),
    ("四千多的", "none", False,
     "只有价位 → state_range"),
    ("两万以上的旗舰", "none", False,
     "价位+定位描述 → state_range"),
    ("中端价位", "none", False,
     "只有价位定位 → state_range"),
]


def make(user, topic, fit, cot, intent, with_cot):
    gold = {"intent": intent, "topic": topic, "fit": fit}
    js = json.dumps(gold, ensure_ascii=False)
    content = ("%s。\n%s" % (cot, js)) if with_cot else js
    return {
        "messages": [
            {"role": "user", "content": user},
            {"role": "assistant", "content": content},
        ],
        "_meta": {"src": "new-need-seed", "has_cot": with_cot},
    }


def main():
    random.seed(20261125)
    rows = []
    for user, topic, fit, cot in POS:
        rows.append(make(user, topic, fit, cot, "state_new_need",
                         random.random() < 0.3))
    for user, topic, fit, cot in NEG:
        rows.append(make(user, topic, fit, cot, "state_range",
                         random.random() < 0.3))

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_cot = sum(1 for r in rows
                if len(r["messages"][1]["content"].split("\n")) > 1)
    print("写出 %s" % OUT)
    print("  共 %d 条（正例 %d / 负例 %d）" % (len(rows), len(POS), len(NEG)))
    print("  带思维链 %d 条" % n_cot)


if __name__ == "__main__":
    main()
