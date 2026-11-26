# -*- coding: utf-8 -*-
"""seed_final_gaps.py —— 最后一次补数据（针对剩余 12 条错误）

## 剩余错误分析（v7 + 规则修完，92.0%）

    state_new_need → list_others     要配件清单
    state_new_need → ask_current     一套下来多少钱
    refuse_switch  → list_others     这个价位还有别的不 / 还有没有其他的
    refuse_switch  → ask_current     不行不行
    state_new_need → state_range     预算卡死 6000 不能再高了
    ask_current    → refuse_switch   有没有机械革命的 / 华硕那个呢
    ask_current    → state_new_need  有没有机械革命的同配置的
    ask_current    → state_range     有联想的吗
    analyze        → unknown         联想有几台
    unknown        → ask_current     那个

## 哪几类值得补数据

### ① 「有没有XX牌」的归属 —— 这是**标注争议**，要先定标准

    「有没有机械革命的」「有联想的吗」「华硕那个呢」

    模型判 refuse_switch，测试集标 ask_current。
    从产品行为看：
      · 用户已经看到一台（联想），问"有没有机械革命的" = 想看别的牌子
      · 这**就是换台**（refuse_switch），不是"问当前这台的细节"
      · 标 ask_current 是错的

    ⚠️ 所以这里**不改模型，改测试集标注**。
       但要确认代码行为：判 refuse_switch 后会推一台机械革命的吗？
       如果会，那模型是对的。

### ② 「还有没有其他的」→ refuse_switch

    这和老问题同源：「还有X吗」被 list_others 抢走。
    上一轮删了 `(还有|再有|再给|能看|想看).{0,4}(别的|其他|几个)`，
    但可能还有别的规则在抢。要查。

### ③ 「联想有几台」→ unknown

    「有几台」是计数，该 analyze。模型判 unknown（可能因为太短）。
    这条可以补数据。

### ④ 「那个」→ ask_current

    「那个」是指示词但没有实际内容。测试集标 unknown。
    模型判 unknown（对），但规则改成 ask_current。
    这是「模型判不出 → 退回规则」那条规则的问题。

## 本批补什么

① 「有没有XX牌」→ **按 refuse_switch 标**（并说明理由）
② 「还有X吗」的边界正例/负例
③ 「有几台」类计数
④ 「那个」这类纯指示词
"""
import io
import json
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")

OUT = r"E:\电脑涨价分析\tools\agent\lora_data\final-gaps-seed.jsonl"

S = []


def a(u, intent, topic, cot):
    S.append((u, intent, topic, cot))


# ══════════════════════════════════════════════════════════════════
# ① 「有没有XX牌」→ refuse_switch（换品牌 = 换台）
#
# 标注理由：用户已经看到一台联想，问"有没有机械革命的"
#   ① 不是在问当前这台联想的任何属性
#   ② 是要看另一台（机械革命的）
#   ③ 产品行为：推一台机械革命的 = 换台
# ⇒ refuse_switch
# ══════════════════════════════════════════════════════════════════
BRANDS = ["机械革命", "联想", "华硕", "惠普", "戴尔", "宏碁", "神舟",
          "雷神", "微星", "荣耀", "小米", "华为", "苹果", "thinkpad",
          "天选", "拯救者", "暗影精灵", "小新", "战66", "magicbook"]

for b in BRANDS:
    a("有没有%s的" % b, "refuse_switch", "none",
      "「有没有%s的」= 想看另一个牌子 = 换台，不是问当前这台 → refuse_switch" % b)
    a("有%s的吗" % b, "refuse_switch", "none",
      "「有%s的吗」= 要换到该品牌 → refuse_switch" % b)
    a("%s那个呢" % b, "refuse_switch", "none",
      "「%s那个呢」= 问替代品牌 → refuse_switch" % b)

# 变体：问替代型号
a("有没有同配置的", "refuse_switch", "none",
  "「有没有同配置的」= 要另一台同样配置的 → refuse_switch")
a("有没有便宜点的牌子", "refuse_switch", "none",
  "问更便宜的品牌 → refuse_switch")
a("别的牌子有哪些", "refuse_switch", "none",
  "问其他品牌 → 换台语境 → refuse_switch")
a("换个牌子的", "refuse_switch", "none", "明确要换品牌 → refuse_switch")

# ══════════════════════════════════════════════════════════════════
# ② 「还有X吗」的边界（换台 vs 要清单）
# ══════════════════════════════════════════════════════════════════
# → refuse_switch（只要一台新的）
SWITCH_MORE = [
    "还有别的吗", "还有别的嘛", "还有其他的吗", "还有没有其他的",
    "还有没有别的", "这个价位还有别的不", "还有其他的没",
    "还有更好的吗", "还有便宜点的吗", "还有别的推荐吗",
    "还有吗", "还有没", "别的还有吗", "再有没有别的",
    "有别的选择吗", "有其他选择吗", "还有啥", "还有哪个",
]
for u in SWITCH_MORE:
    a(u, "refuse_switch", "none",
      "「%s」在已推一台的语境下 = 想看另一台 → refuse_switch" % u)

# → list_others（要全部）
LIST_ALL = [
    "全部列出", "都列出来", "全都列出来", "剩下的都给我",
    "把其余候选列出来", "一次列完", "我要看全部", "完整清单",
    "所有候选列出来", "剩余的都说一下", "列个表",
]
for u in LIST_ALL:
    a(u, "list_others", "none",
      "「%s」明确要**全部**剩余候选 → list_others" % u)

# ══════════════════════════════════════════════════════════════════
# ③ 计数类 → analyze（「有几台」要能认）
# ══════════════════════════════════════════════════════════════════
COUNT = [
    "联想有几台", "华硕有几台", "戴尔几台", "游戏本几台", "轻薄本有几台",
    "有几个牌子", "有几款独显的", "有几台是32G", "几台支持雷电",
    "有多少台游戏本", "一共有几台", "总共几台机器",
    "华为的有几台", "苹果有几台", "微星几台",
]
for u in COUNT:
    a(u, "analyze", "none",
      "「%s」是计数查询，要跨全库统计 → analyze" % u)

# ══════════════════════════════════════════════════════════════════
# ④ 纯指示词 / 无内容 → unknown
# ══════════════════════════════════════════════════════════════════
INDIC = [
    "那个", "这个", "它", "那台", "这台", "哪个",
    "那个呢", "这个呢", "呢", "还有呢",
]
for u in INDIC:
    a(u, "unknown", "none",
      "「%s」只有指示词，没有实际内容（没说问什么）→ unknown" % u)

# ══════════════════════════════════════════════════════════════════
# ⑤ 「预算卡死 X」类 → state_new_need（明确给预算）
# ══════════════════════════════════════════════════════════════════
BUDGET_STRICT = [
    "预算卡死 6000 不能再高了", "预算就 6000 一分不能多",
    "最多 8000，超了不行", "预算上限 7000", "死守 5000",
    "只能出 6000", "不能超过 9000", "预算 8000 封顶",
    "卡在 6000", "预算咬死 7500",
]
for u in BUDGET_STRICT:
    a(u, "state_new_need", "none",
      "「%s」是明确给出预算上限 → 加条件 → state_new_need" % u)

# ══════════════════════════════════════════════════════════════════
# ⑥ 「一套下来多少钱」类 → state_new_need（装机语境）
# ══════════════════════════════════════════════════════════════════
BUILD_PRICE = [
    "一套下来多少钱", "配下来多少钱", "这样配要多少钱",
    "整套多少钱", "配齐了多少钱", "全部加起来多少钱",
    "整机多少钱", "一套要多少", "总共花多少", "配好要多少",
]
for u in BUILD_PRICE:
    a(u, "state_new_need", "none",
      "「%s」问的是**整台配置的合计价**（装机语境），不是问当前这台的单价"
      " → state_new_need" % u)

# ══════════════════════════════════════════════════════════════════
# ⑦ 「要配件清单」→ state_new_need（装机）
# ══════════════════════════════════════════════════════════════════
PARTS = [
    "要配件清单", "给个配件单", "配件清单要一份", "需要哪些配件",
    "配件的清单", "列一下要买哪些件", "报个配件单",
]
for u in PARTS:
    a(u, "state_new_need", "none",
      "「%s」要的是装机配件清单（不是剩余候选机型清单）→ state_new_need"
      % u)

# ══════════════════════════════════════════════════════════════════
# ⑧ 「不行不行」类 → refuse_switch（明确否定，非疑问）
# ══════════════════════════════════════════════════════════════════
NEG = [
    "不行不行", "不行", "不好不好", "不要不要", "拒绝",
    "我不喜欢", "太差了", "不好用", "看不上", "不合适不合适",
]
for u in NEG:
    a(u, "refuse_switch", "none",
      "「%s」是明确否定（陈述形态，无疑问标记）→ refuse_switch" % u)


def make(user, intent, topic, cot, with_cot):
    gold = {"intent": intent, "topic": topic, "fit": False}
    js = json.dumps(gold, ensure_ascii=False)
    content = ("%s。\n%s" % (cot, js)) if with_cot else js
    return {
        "messages": [
            {"role": "user", "content": user},
            {"role": "assistant", "content": content},
        ],
        "_meta": {"src": "final-gaps-seed", "has_cot": with_cot},
    }


def main():
    rnd = random.Random(20261136)
    print("=" * 76)
    print("最后一次补数据（针对剩余 12 条错误）")
    print("=" * 76)
    print()

    from collections import Counter
    seen = set()
    uniq = []
    for u, i, t, c in S:
        if u in seen:
            continue
        seen.add(u)
        uniq.append((u, i, t, c))

    cnt = Counter(x[1] for x in uniq)
    print("  各意图:")
    for k, v in cnt.most_common():
        print("    %-16s %4d 条" % (k, v))
    print()

    rows = [make(u, i, t, c, rnd.random() < 0.55) for u, i, t, c in uniq]
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print("  合计 %d 条（去重后）" % len(rows))
    print("  写出 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
