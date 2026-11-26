# -*- coding: utf-8 -*-
"""seed_fill_gaps.py —— 按缺口精确补齐（目标 1500 条）

## 当前缺口（合并去重后）

    state_range      150 / 需 240   缺 90
    refuse_switch    119 / 需 180   缺 61
    analyze          122 / 需 150   缺 28
    ask_current      308 / 需 330   缺 22
    list_others       81 / 需  90   缺  9
    state_new_need   450 / 需 450   ✓
    unknown          153 / 需  60   ✓（富余）

## state_range 为什么缺这么多

它只有「纯价位表达」一种形态，自然说法有限。
但换个角度：价位 × 品类 组合能扩出很多：
    五千的轻薄本 / 六千的游戏本 / 八千的创作本 ...
再加区间变体：五千到七千 / 6-8千 / 一万上下

## refuse_switch 补什么

除了直接的「换一台」，还要覆盖：
  · 否定 + 理由（太贵/太重/太大）
  · 疑问式换台（有没有更便宜的）
  · 带新条件换台（换个32G的）—— 注意这仍是 refuse_switch
    因为主语还是"换"，只是换了条件
"""
import io
import itertools
import json
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")

OUT = r"E:\电脑涨价分析\tools\agent\lora_data\gap-fill-seed.jsonl"

S = set()


def a(u, intent, topic, cot):
    S.add((u, intent, topic, cot))


# ══════════════════════════════════════════════════════════════════
# state_range（缺 90）：价位 × 品类 + 区间变体
# ══════════════════════════════════════════════════════════════════
PRICE = ["三千", "四千", "五千", "六千", "七千", "八千",
         "九千", "一万", "一万二", "一万五"]
CAT = ["轻薄本", "游戏本", "全能本", "办公本", "创作本", "本子"]

# ① 价位 + 品类
for p, c in itertools.product(PRICE, CAT):
    a("%s的%s" % (p, c), "state_range", "none",
      "「%s的%s」只有价位 + 品类，没提需求也没疑问 → state_range" % (p, c))

# ② 区间表达
RANGES = [
    "五千到七千", "6-8千", "7000-9000", "一万上下",
    "八千出头", "六千五左右", "五千多一点", "一万二以内",
    "四千到六千之间", "9000 到 12000", "七八千的", "四五千的",
    "一万以里", "两万封顶", "15000 以下", "5k-7k",
    "8000 至 10000", "六千往上", "不到一万", "一万出头",
    "三千块左右", "六千五上下", "1.2w 以内", "8k 左右",
    "五千上下", "七千来块", "一万以内都行", "九千以下",
]
for r in RANGES:
    a(r, "state_range", "none",
      "「%s」只是给出价位/范围，没有提出需求 → state_range" % r)

# ══════════════════════════════════════════════════════════════════
# refuse_switch（缺 61）：否定 + 理由 / 疑问式 / 带条件
# ══════════════════════════════════════════════════════════════════
# ① 否定 + 理由
REASON = ["太贵", "太重", "太大", "太小", "太丑", "太厚",
          "配置低", "屏幕小", "续航短", "太吵", "发热大"]
for r in REASON:
    a("这个%s了" % r, "refuse_switch", "none",
      "「这个%s了」表示否定当前这台 → refuse_switch" % r)
    a("%s，换一台" % r, "refuse_switch", "none",
      "「%s，换一台」明确要求换 → refuse_switch" % r)

# ② 疑问式换台
QUERY = [
    "有没有更便宜的", "有没有更轻的", "有没有屏幕大点的",
    "有没有续航长的", "有没有配置高点的", "有没有别的牌子",
    "还有便宜的吗", "还有轻一点的吗", "还能再便宜点吗",
    "有其他推荐吗", "有没有更好的", "有别家吗",
    "换个牌子有吗", "其他型号呢", "别的呢",
]
for q in QUERY:
    a(q, "refuse_switch", "none",
      "「%s」在当前已推一台的语境下 = 要换另一台 → refuse_switch" % q)

# ③ 带条件换台（注意：主语是"换"，所以仍是 refuse_switch）
COND = ["32G的", "独显的", "轻一点的", "屏幕大的", "续航长的",
        "便宜点的", "贵一点的", "内存大的", "固态大的", "安静点的"]
for c in COND:
    a("换个%s" % c, "refuse_switch", "none",
      "「换个%s」主语是「换」= 不要当前这台 → refuse_switch" % c)
    a("换一台%s" % c, "refuse_switch", "none",
      "「换一台%s」→ refuse_switch" % c)

# ④ 口语化拒绝
VULGAR = [
    "下一个吧", "接着看", "再翻一个", "这个不中意",
    "pass掉", "淘汰这个", "划掉这个", "不要这个",
    "这个不行换", "不行换吧", "再找找", "再搜搜",
    "我看看别的", "让我看别的", "不想看这个了",
]
for v in VULGAR:
    a(v, "refuse_switch", "none",
      "「%s」= 不要当前这台 → refuse_switch" % v)

# ══════════════════════════════════════════════════════════════════
# analyze（缺 28）
# ══════════════════════════════════════════════════════════════════
AGG_MORE = [
    ("有多少台", "none"), ("总共几台", "none"),
    ("几台是16寸的", "screen"), ("多少台14寸", "screen"),
    ("多少台超过1.5kg", "weight"), ("几台低于1.2kg", "weight"),
    ("平均屏幕多大", "screen"), ("平均硬盘多大", "config"),
    ("各尺寸各几台", "screen"), ("按尺寸分组统计", "screen"),
    ("内存分布情况", "config"), ("显卡配置分布", "config"),
    ("不同显存有多少台", "config"), ("统计各硬件配置数量", "config"),
    ("哪台最重", "weight"), ("最重多少kg", "weight"),
    ("分数最低的是哪台", "none"), ("跑分最低的几台", "none"),
    ("价格最低的三台", "price"), ("最省电的是哪台", "battery"),
    ("容量最大的是哪台", "config"), ("屏幕最大的是哪台", "screen"),
    ("有多少台支持快充", "service"), ("几台是OLED", "screen"),
    ("整体平均配置是什么", "config"), ("这些机器的共同点", "none"),
    ("价格跨度多大", "price"), ("最贵和最便宜差多少", "price"),
]
for u, t in AGG_MORE:
    a(u, "analyze", t,
      "「%s」要跨全库做聚合/计数/极值 → analyze/%s" % (u, t))

# ══════════════════════════════════════════════════════════════════
# ask_current（缺 22）
# ══════════════════════════════════════════════════════════════════
ASK_MORE = [
    ("这台的屏幕是IPS吗", "screen"),
    ("有高刷吗", "screen"),
    ("刷新率多少", "screen"),
    ("touch 屏吗", "screen"),
    ("这台是金属机身吗", "build"),
    ("外壳什么材质", "build"),
    ("能装几个硬盘", "config"),
    ("固态是什么接口", "config"),
    ("支持PCIE4吗", "config"),
    ("风扇声音大吗", "noise"),
    ("满载会烫吗", "noise"),
    ("这台有摄像头吗", "service"),
    ("支持WiFi6吗", "service"),
    ("有雷电接口吗", "service"),
    ("能插网线吗", "service"),
    ("有指纹解锁吗", "service"),
    ("背光键盘有吗", "keyboard"),
    ("键程多长", "keyboard"),
    ("这台二手能卖多少", "price"),
    ("比官网便宜吗", "price"),
    ("有赠品吗", "price"),
    ("现在有活动吗", "price"),
]
for u, t in ASK_MORE:
    a(u, "ask_current", t,
      "「%s」问的是当前这台的具体属性 → ask_current/%s" % (u, t))

# ══════════════════════════════════════════════════════════════════
# list_others（缺 9）
# ══════════════════════════════════════════════════════════════════
LIST_MORE = [
    "把符合的都列出来", "符合条件的一个都别漏", "全部符合条件的列出",
    "完整的候选项", "列一份完整清单", "把候选一次性列全",
    "所有满足的都给我", "别漏了，全列", "全部列成表格",
]
for u in LIST_MORE:
    a(u, "list_others", "none",
      "「%s」要全部剩余候选的一份完整清单 → list_others" % u)


def make(user, intent, topic, cot, with_cot):
    gold = {"intent": intent, "topic": topic, "fit": False}
    js = json.dumps(gold, ensure_ascii=False)
    content = ("%s。\n%s" % (cot, js)) if with_cot else js
    return {
        "messages": [
            {"role": "user", "content": user},
            {"role": "assistant", "content": content},
        ],
        "_meta": {"src": "gap-fill-seed", "has_cot": with_cot},
    }


def main():
    rnd = random.Random(20261135)
    print("=" * 76)
    print("按缺口补齐（目标总量 1500）")
    print("=" * 76)
    print()

    from collections import Counter
    cnt = Counter(s[1] for s in S)
    print("  本批生成:")
    for k, v in cnt.most_common():
        print("    %-16s %4d 条" % (k, v))
    print("    合计 %d" % len(S))
    print()

    rows = [make(u, i, t, c, rnd.random() < 0.40) for u, i, t, c in S]
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("  写出 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
