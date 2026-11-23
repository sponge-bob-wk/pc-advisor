# -*- coding: utf-8 -*-
"""seed_analyze.py —— 生成 analyze 意图的训练样本

## 为什么需要

2026-11-23 实测发现：`lora_v4` 把「有多少台有独显」判成 `ask_current`，
应该判 `analyze`。

查训练集：**`analyze` 样本数 = 0**。

`analyze` 意图是 2026-11-21 加的，只写了代码（`analyze.py` + `agent.py` 的
0b2 分支 + 规则兜底），**从没加进训练集**。

所以模型压根没见过这个类别，只能把它归到最像的 `ask_current`。

## 生成策略

`analyze` 的判据（来自 SYS_TMPL）：
  ① 有明确的统计动作（多少台/几台/平均/中位数/分布/排行/各品牌对比）
  ② **不是**在问当前这一台

所以要造两类样本：
  · **正例**：明确的统计问句 → analyze
  · **负例**：容易混的非 analyze（"这个价算什么水平" / "哪个最便宜"）
    → 标成正确的类别，防止模型把 analyze 用滥

## 样本设计

正例覆盖 8 种聚合操作：
  count / mean / median / min / max / groupby / distribution / top

每类 4-6 条，共约 40 条正例 + 12 条负例。

## 带思维链

30% 的样本带 CoT（和现有训练集比例一致：144/497 = 29%）。
CoT 格式和现有数据一致：判断依据 + `\n` + JSON。
"""
import io
import json
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")

OUT = r"E:\电脑涨价分析\tools\agent\lora_data\analyze-seed.jsonl"

# ── 正例：(用户问句, intent, topic, fit, 思维链) ──
# topic 统一给 none（统计问题不问某一台机器的细节）
POS = [
    # count 计数
    ("有多少台有独显", "count", "有「多少台」是计数动作，问的是整个库不是某一台"),
    ("库里有几台是核显的", "count", "「几台」是计数，问全库"),
    ("多少台支持雷电4", "count", "「多少台」+ 接口条件 = 全库计数"),
    ("32G内存的机器有几台", "count", "「几台」+ 配置筛选 = 全库计数"),
    ("一万以内有多少台游戏本", "count", "「多少台」+ 价位和品类 = 全库计数"),
    ("有几个品牌", "count", "「几个」是计数，问全库维度"),
    # mean 平均值
    ("平均价格是多少", "mean", "「平均」是统计动作，问全库"),
    ("这些机器平均多重", "mean", "「平均多重」= 全库均值，不是问某一台"),
    ("游戏本平均重量多少", "mean", "「平均」+ 品类 = 全库统计"),
    ("轻薄本平均续航几小时", "mean", "「平均」+ 品类 = 全库统计"),
    ("8000以内的机器平均多少分", "mean", "「平均」+ 价位 = 全库统计"),
    # median 中位数
    ("价格中位数是多少", "median", "「中位数」是统计量，问全库"),
    ("中位数重量多少", "median", "「中位数」是统计量"),
    # min / max 极值
    ("最便宜的是哪台", "min", "「最便宜」是在全库找极值"),
    ("最轻的机器多重", "min", "「最轻」是在全库找极值"),
    ("最贵多少钱", "max", "「最贵」是全库极值"),
    ("性能最强的是哪台", "max", "「最强」是全库极值"),
    # groupby 分组
    ("各品牌机型数量对比", "groupby", "「各品牌」+「对比」= 分组统计"),
    ("各品牌平均价格", "groupby", "「各品牌」+「平均」= 分组统计"),
    ("按品类分组看看", "groupby", "「按品类分组」是明确的分组动作"),
    ("每个品牌的机器数", "groupby", "「每个品牌」= 分组计数"),
    ("联想和华硕各有多少台", "groupby", "「各有多少台」= 分组计数"),
    # distribution 分布
    ("价格分布是什么样的", "distribution", "「分布」是统计动作"),
    ("32G内存的价格分布", "distribution", "「分布」+ 筛选条件"),
    ("重量分布集中在哪个区间", "distribution", "「分布」是统计动作"),
    ("各价位段机器数量占比", "distribution", "「占比」是分布统计"),
    # top / bottom 排行
    ("性能最强的五台", "top", "「最强」+「五台」= 排行"),
    ("列出价格最低的三台", "top", "「最低」+「三台」= 排行"),
    ("续航最好的几台机器", "top", "「最好」+「几台」= 排行"),
    ("性价比排名前十", "top", "「排名」是排行统计"),
    ("按重量排个序", "top", "「排序」是排行动作"),
    # 口语化
    ("库里总共多少台机器", "count", "「总共多少台」= 全库计数"),
    ("统计一下有多少台带独显", "count", "「统计」是明确的统计动作"),
    ("帮我算算平均价格", "mean", "「算算平均」是统计动作"),
    ("对比一下各品牌的售后评分", "groupby", "「对比各品牌」= 分组统计"),
    # ── 补充：更口语化 / 带条件 / 不同句式（2026-11-23 加）──
    # 为什么补：32 条对 190 条 ask_current 偏少（占 6%），
    # 补到 55 条左右（占 10%）才够模型学到。
    ("帮我数数带独显的", "count", "「数数」是计数动作"),
    ("全库有多少台超过一万", "count", "「全库」+「多少台」明确是统计"),
    ("有没有统计过各品牌的平均价", "mean", "「统计」+「平均」双重要素"),
    ("平均下来一台多少钱", "mean", "「平均下来」是统计口吻"),
    ("中位数和平均差多少", "median", "「中位数」是统计量"),
    ("最便宜的那台多少钱", "min", "「最便宜」+「哪台」= 全库极值"),
    ("哪个牌子最贵", "groupby", "「哪个牌子」+「最贵」= 按品牌统计"),
    ("各价位的机器数量", "groupby", "「各价位」+「数量」= 分组统计"),
    ("按内存大小分个组", "groupby", "「分组」是明确动作"),
    ("不同品类的平均重量", "groupby", "「不同品类」+「平均」= 分组统计"),
    ("价格都集中在什么区间", "distribution", "「集中在什么区间」= 分布"),
    ("续航的分布情况", "distribution", "「分布情况」是统计"),
    ("屏幕尺寸都是多大的，占多少", "distribution", "「占多少」= 占比统计"),
    ("挑出最轻的三台", "top", "「最轻」+「三台」= 排行"),
    ("重量从轻到重排一下", "top", "「排序」是排行动作"),
    ("给我个性能排行榜", "top", "「排行榜」是排行统计"),
    ("哪几台性价比最高", "top", "「哪几台」+「最高」= 排行"),
    ("统计一下独显和核显的比例", "count", "「统计」+「比例」= 全库统计"),
    ("这些机器里16G和32G各多少", "groupby", "「各多少」= 分组计数"),
    ("全库平均续航多少小时", "mean", "「全库平均」明确是统计"),
]

# ── 负例：容易误判成 analyze 的，必须保持原类别 ──
# (用户问句, 正确 intent, topic, fit, 为什么不是 analyze)
NEG = [
    ("这个价在市场上算什么水平", "ask_current", "price", False,
     "问的是**这一台**的价位，不是全库统计"),
    ("哪个最便宜", "state_range", "none", False,
     "是在找**一台**机器，不是要统计数字"),
    ("有便宜点的吗", "state_new_need", "none", False,
     "是提新要求（更便宜），不是统计"),
    ("这台多重", "ask_current", "weight", False,
     "问当前这台的重量，是单机细节"),
    ("它续航多久", "ask_current", "battery", False,
     "问当前这台的续航，是单机细节"),
    ("换个平均点的", "state_new_need", "none", False,
     "「平均」在这里是形容词，不是统计动作"),
    ("性能最强的这台怎么样", "ask_current", "pros_cons", False,
     "「最强」是在形容当前这台，不是在排行全库"),
    ("这些机器里哪个值得买", "state_range", "none", False,
     "是让推荐一台，不是要统计"),
]


def make_sample(user, intent, topic, fit, cot, with_cot):
    """造一条训练样本。"""
    gold = {"intent": intent, "topic": topic, "fit": fit}
    js = json.dumps(gold, ensure_ascii=False)
    if with_cot:
        content = "%s。\n%s" % (cot, js)
    else:
        content = js
    return {
        "messages": [
            {"role": "user", "content": user},
            {"role": "assistant", "content": content},
        ],
        "_meta": {"src": "analyze-seed", "has_cot": with_cot},
    }


def main():
    random.seed(20261123)
    rows = []

    # 正例：30% 带 CoT
    for user, kind, cot in POS:
        with_cot = random.random() < 0.30
        rows.append(make_sample(user, "analyze", "none", False, cot, with_cot))

    # 负例：也 30% 带 CoT
    for user, intent, topic, fit, why in NEG:
        with_cot = random.random() < 0.30
        rows.append(make_sample(user, intent, topic, fit, why, with_cot))

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_cot = sum(1 for r in rows
                if len(r["messages"][1]["content"].split("\n")) > 1)
    print("写出 %s" % OUT)
    print("  共 %d 条（正例 %d / 负例 %d）"
          % (len(rows), len(POS), len(NEG)))
    print("  其中带思维链 %d 条（%.0f%%）"
          % (n_cot, 100.0 * n_cot / len(rows)))

    # 分布
    import collections
    c = collections.Counter()
    for r in rows:
        a = r["messages"][1]["content"]
        i = a.rfind("{")
        c[json.loads(a[i:])["intent"]] += 1
    print("  intent 分布：%s" % dict(c))


if __name__ == "__main__":
    main()
