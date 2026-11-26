# -*- coding: utf-8 -*-
"""seed_boundary.py —— 意图边界专项种子

## 诊断出的三组边界混淆

### ① refuse_switch ↔ list_others（7 条错）
    「还有别的吗」      → 应该 refuse_switch（换台）
    「能看看别的吗」    → 应该 refuse_switch
    「推荐个别的」      → 应该 refuse_switch
    「把其余候选列出来」→ 应该 list_others（要清单）

  **判据**：
    换台 = 要**一台新的**（推一台就行，不用全列）
    要清单 = 要**全部剩余**（一次看到所有候选）

### ② state_new_need ↔ ask_current（16 条错，主要是装机）
    「组一台打游戏的」→ 判成 ask_current
    「配下来多少钱」  → 判成 ask_current（以为是问当前这台的价）

  **判据**：
    有动作词（组/配/弄/整/搞/攒/装）→ 加条件
    有「这台/它/这个」指代当前 → 问当前

### ③ analyze ↔ state_range（3 条错）
    「性能最强的五台」→ 判成 state_range
    「最便宜的是哪台」→ 判成 state_range
    「哪个品牌最多」  → 判成 state_range

  **判据**：
    analyze 有**聚合词**（几台/多少/平均/最强/最便宜/最多/分布/统计）
    state_range 只是**给出范围**（「8000以内」「五六千」），不带疑问

### ④ 数字区间 ↔ unknown（2 条错）
    「5000到6000」→ 判成 unknown
    「3000-5000」  → 判成 unknown

  **判据**：纯数字区间 = state_range（用户在说预算范围）
"""
import io
import json
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")

OUT = r"E:\电脑涨价分析\tools\agent\lora_data\boundary-seed.jsonl"

SEEDS = []


def add(user, intent, topic, cot):
    SEEDS.append((user, intent, topic, cot))


# ══════════════════════════════════════════════════════════════
# ① refuse_switch：换台（要一台新的）
# ══════════════════════════════════════════════════════════════
add("还有别的吗", "refuse_switch", "none",
    "「还有别的吗」是想要另一台，不是要清单 → refuse_switch")
add("能看看别的吗", "refuse_switch", "none",
    "「看看别的」= 换一台看 → refuse_switch")
add("再看看别的吧", "refuse_switch", "none",
    "「再看看别的」= 换台 → refuse_switch")
add("算了，看别的", "refuse_switch", "none",
    "「看别的」= 换台 → refuse_switch")
add("这个价位还有别的不", "refuse_switch", "none",
    "「还有别的不」= 换一台同价位的 → refuse_switch")
add("还有没有其他的", "refuse_switch", "none",
    "「有没有其他的」= 换台 → refuse_switch")
add("推荐个别的", "refuse_switch", "none",
    "「推荐个别的」= 换台 → refuse_switch")
add("换一台", "refuse_switch", "none", "「换一台」→ refuse_switch")
add("换一个", "refuse_switch", "none", "「换一个」→ refuse_switch")
add("换别的", "refuse_switch", "none", "「换别的」→ refuse_switch")
add("再来一台", "refuse_switch", "none",
    "「再来一台」= 再给一台新的 → refuse_switch")
add("这个不行", "refuse_switch", "none",
    "「不行」表示对当前这台不满意 → refuse_switch")
add("不行不行", "refuse_switch", "none",
    "「不行不行」= 不满意，要换 → refuse_switch")
add("不太满意", "refuse_switch", "none",
    "「不满意」→ 换台 → refuse_switch")
add("不合适", "refuse_switch", "none",
    "「不合适」→ 换台 → refuse_switch")
add("再看看", "refuse_switch", "none",
    "「再看看」= 想看别的 → refuse_switch")
add("那换一台7000的符合我的", "refuse_switch", "none",
    "「换一台...的」= 换了条件但还是要换台 → refuse_switch")
add("换个贵点的", "refuse_switch", "none",
    "「换个...的」→ 换台 → refuse_switch")
add("换台便宜的", "refuse_switch", "none",
    "「换台...」→ refuse_switch")
add("换台轻的", "refuse_switch", "none", "「换台...」→ refuse_switch")

# ── 对照：list_others（要全部剩余）──
add("把其余候选列出来", "list_others", "none",
    "「其余候选列出来」= 要全部剩余清单 → list_others")
add("全部列出", "list_others", "none",
    "「全部列出」= 要清单 → list_others")
add("都列出来", "list_others", "none", "「都列出来」→ list_others")
add("还有哪些", "list_others", "none",
    "「还有哪些（台）」问的是清单 → list_others")
add("把剩下的都说说", "list_others", "none",
    "「剩下的都说说」= 要清单 → list_others")
add("一次看完所有候选", "list_others", "none",
    "「所有候选」= 要清单 → list_others")
add("列个清单", "list_others", "none", "「清单」→ list_others")

# ══════════════════════════════════════════════════════════════
# ② state_new_need：加条件（不能判成 ask_current）
# ══════════════════════════════════════════════════════════════
add("打游戏", "state_new_need", "none",
    "「打游戏」是用途，加了个条件 → state_new_need")
add("主要是打游戏", "state_new_need", "none",
    "「主要是打游戏」= 说明用途 → state_new_need")
add("平时主要打游戏", "state_new_need", "none",
    "「平时...打游戏」= 用途描述 → state_new_need")
add("拿他打游戏", "state_new_need", "none",
    "「拿他打游戏」= 说明用途，不是问当前这台 → state_new_need")
add("用来剪视频", "state_new_need", "none",
    "「用来...」= 用途 → state_new_need")
add("写代码", "state_new_need", "none", "「写代码」= 用途 → state_new_need")
add("办公用的", "state_new_need", "none", "「...用的」= 用途 → state_new_need")
add("学设计", "state_new_need", "none", "「学设计」= 专业/用途 → state_new_need")
add("要能跑PS", "state_new_need", "none",
    "「要能跑...」= 加要求 → state_new_need")
add("预算 8000 打游戏", "state_new_need", "none",
    "预算 + 用途都是加条件 → state_new_need")
add("预算卡死 6000 不能再高了", "state_new_need", "none",
    "「预算卡死 6000」= 加预算条件 → state_new_need")
add("要 32G 内存的", "state_new_need", "none",
    "「要...的」= 加配置要求 → state_new_need")
add("要独立显卡", "state_new_need", "none",
    "「要独立显卡」= 加配置要求 → state_new_need")
add("屏幕要好点的", "state_new_need", "none",
    "「要好点的」= 加要求 → state_new_need")
add("要轻一点的", "state_new_need", "none",
    "「要轻一点」= 加要求 → state_new_need")
add("续航要长", "state_new_need", "none",
    "「续航要长」= 加要求 → state_new_need")
add("我是软件工程专业的，平时也喜欢打游戏", "state_new_need", "none",
    "「我是...专业的，也喜欢打游戏」= 给两个条件 → state_new_need")

# ── 对照：ask_current（问当前这台的某一面）──
add("这台多重", "ask_current", "weight",
    "「这台」指代当前 + 问重量 → ask_current/weight")
add("它的续航呢", "ask_current", "battery",
    "「它的」指代当前 + 问续航 → ask_current/battery")
add("能加内存吗", "ask_current", "config",
    "「能加内存吗」问的是当前这台的配置 → ask_current/config")
add("有没有机械革命的", "ask_current", "none",
    "问有没有同类的（在当前这台语境下）= 问替代品 → ask_current")
add("有没有机械革命的同配置的", "ask_current", "none",
    "「同配置的」是在比较当前这台 → ask_current")
add("华硕那个呢", "ask_current", "none",
    "「那个呢」指代另一个品牌 = 问替代 → ask_current")
add("有联想的吗", "ask_current", "none",
    "「有...的吗」在当前语境下问替代品牌 → ask_current")

# ══════════════════════════════════════════════════════════════
# ③ analyze：聚合统计（不能判成 state_range）
# ══════════════════════════════════════════════════════════════
add("有多少台有独显", "analyze", "config",
    "「有多少台」是计数 → analyze")
add("各品牌数量对比", "analyze", "none",
    "「数量对比」是分组统计 → analyze")
add("8000以内平均多重", "analyze", "weight",
    "「平均」是聚合 → analyze")
add("性能最强的五台", "analyze", "none",
    "「最强的五台」是排行 → analyze")
add("最便宜的是哪台", "analyze", "price",
    "「最便宜的」是极值查询 → analyze")
add("联想有几台", "analyze", "none",
    "「有几台」是计数 → analyze")
add("游戏本平均多少钱", "analyze", "price",
    "「平均多少钱」是聚合 → analyze")
add("内存 32G 的有几台", "analyze", "config",
    "「有几台」是计数 → analyze")
add("哪个品牌最多", "analyze", "none",
    "「哪个最多」是分组求最大 → analyze")
add("价格分布是什么样的", "analyze", "price",
    "「分布」是分布统计 → analyze")
add("库里一共多少台", "analyze", "none",
    "「一共多少台」是计数 → analyze")
add("统计一下品类", "analyze", "none",
    "「统计」是聚合 → analyze")

# ── 对照：state_range（纯给范围，不带疑问）──
add("5000到6000", "state_range", "none",
    "纯数字区间，没有疑问词 → state_range")
add("3000-5000", "state_range", "none",
    "纯数字区间 → state_range")
add("6000 的游戏本", "state_range", "none",
    "价位 + 品类描述 → state_range")
add("一万以内", "state_range", "none",
    "只有价位上限 → state_range")
add("八千左右", "state_range", "none", "只有价位 → state_range")
add("六七千的轻薄本", "state_range", "none",
    "价位区间 + 品类 → state_range")
add("一万五", "state_range", "none", "单个价位 → state_range")
add("两万以上", "state_range", "none", "价位下限 → state_range")
add("8000到12000之间", "state_range", "none",
    "数字区间 → state_range")

# ══════════════════════════════════════════════════════════════
# ④ unknown：真没说清
# ══════════════════════════════════════════════════════════════
add("啊啊啊", "unknown", "none",
    "无意义字符串 → unknown")
add("那个", "unknown", "none",
    "只有指示词，没有实际内容 → unknown")
add("随便看看", "unknown", "none",
    "「随便看看」没给任何条件 → unknown")
add("嗯", "unknown", "none", "只有语气词 → unknown")
add("在吗", "unknown", "none", "打招呼但没提需求 → unknown")
add("？", "unknown", "none", "只有标点 → unknown")


def make(user, intent, topic, cot, with_cot):
    gold = {"intent": intent, "topic": topic, "fit": False}
    js = json.dumps(gold, ensure_ascii=False)
    content = ("%s。\n%s" % (cot, js)) if with_cot else js
    return {
        "messages": [
            {"role": "user", "content": user},
            {"role": "assistant", "content": content},
        ],
        "_meta": {"src": "boundary-seed", "has_cot": with_cot},
    }


def main():
    random.seed(20261127)
    rows = []
    print("=" * 74)
    print("生成意图边界专项种子")
    print("=" * 74)
    print()

    from collections import Counter
    cnt = Counter(s[1] for s in SEEDS)

    for user, intent, topic, cot in SEEDS:
        # 边界类样本思维链比例高一些（50%），因为判据需要说清
        rows.append(make(user, intent, topic, cot, random.random() < 0.5))

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_cot = sum(1 for r in rows
                if len(r["messages"][1]["content"].split("\n")) > 1)
    print("  各意图:")
    for k, v in cnt.most_common():
        print("    %-16s %3d 条" % (k, v))
    print()
    print("  合计 %d 条（带思维链 %d 条 = %.0f%%）"
          % (len(rows), n_cot, 100.0 * n_cot / len(rows)))
    print("  写出 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
