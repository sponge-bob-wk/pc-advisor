# -*- coding: utf-8 -*-
"""fix_expand_quality.py —— 修扩充数据的质量问题和分布失衡

## 发现的问题

① **「左右左右」重复**
   BUDGET 里已有「一万五左右」，模板又加了「左右」→「一万五左右左右」

② **语义矛盾**
   「我做视频的，主要上网课」—— 身份和用途不搭
   随机组合会产出这种不合逻辑的句子

③ **分布失衡**
   state_new_need 693 / 897 = 77%
   模型会倾向把所有句子判成 state_new_need

## 修法

① 预算模板里去掉「左右」，改成纯数值
② 身份↔用途做兼容表，只组合合理的
③ 对 state_new_need 采样降权（保留多样性，但控制总量）
   目标分布：state_new_need 不超过 45%
"""
import io
import itertools
import json
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")

OUT = r"E:\电脑涨价分析\tools\agent\lora_data\expand-seed.jsonl"

# ── 修后的槽位 ──
IDENTITY = [
    "我大二的", "我做设计的", "我给爸妈买", "我是程序员", "我在读研",
    "我上班用的", "我做视频的", "我是学生", "我老婆要用", "我弟弟上大学",
]

# 身份 → 合理的用途（避免「做视频的+上网课」这种矛盾）
IDENTITY_USE = {
    "我大二的": ["写代码", "办公", "上网课", "打游戏", "看文献", "做PPT"],
    "我做设计的": ["做三维", "剪视频", "做PPT", "办公"],
    "我给爸妈买": ["办公", "看文献", "做PPT"],
    "我是程序员": ["写代码", "跑数据", "办公"],
    "我在读研": ["写代码", "跑数据", "看文献", "做PPT"],
    "我上班用的": ["办公", "做PPT", "跑数据"],
    "我做视频的": ["剪视频", "做三维", "开直播"],
    "我是学生": ["写代码", "办公", "上网课", "看文献", "做PPT"],
    "我老婆要用": ["办公", "做PPT", "剪视频"],
    "我弟弟上大学": ["打游戏", "写代码", "上网课", "办公"],
}

USE = [
    "打游戏", "剪视频", "写代码", "办公", "上网课", "跑数据",
    "做三维", "开直播", "做PPT", "看文献",
]

HW = [
    "要32G内存", "要独显", "屏幕要好", "要轻", "续航要长",
    "内存大点", "要能跑得动", "性能要好", "散热要好", "硬盘大点",
]

SCENE = [
    "带去教室", "经常出差", "宿舍用", "摆家里", "要背来背去",
    "在图书馆用", "要带去咖啡厅", "公司办公用", "放桌面上不动",
    "上下班通勤带",
]

# 🔴 去掉「左右」，纯数值 —— 模板里再补「左右」
BUDGET = [
    "五千", "六千", "七千", "八千", "一万", "一万二",
    "六千到九千", "八千到一万二", "五千以内", "一万五",
]

BUILD_VERB = [
    "组装一台", "装机", "攒一台", "配一台", "弄个", "整一台",
    "搞一台", "自己装一台", "帮我配个", "想攒个",
]

S = set()


def add(user, intent, topic, cot):
    S.add((user, intent, topic, cot))


def gen():
    rnd = random.Random(20261129)

    # ① 身份 + 用途（只组合合理的）
    for idn, uses in IDENTITY_USE.items():
        for use in uses:
            add("%s，%s用" % (idn, use), "state_new_need", "none",
                "身份+用途都是条件 → state_new_need")

    # ② 用途 + 预算
    for use, bud in itertools.product(USE, BUDGET):
        add("%s，预算%s" % (use, bud), "state_new_need", "none",
            "用途+预算 → 加条件 → state_new_need")

    # ③ 硬件 + 预算
    for hw, bud in itertools.product(HW, BUDGET):
        add("%s，%s左右" % (hw, bud), "state_new_need", "none",
            "硬件要求+预算 → 加条件 → state_new_need")

    # ④ 场景 + 预算
    for sc, bud in itertools.product(SCENE, BUDGET):
        add("%s，%s" % (sc, bud), "state_new_need", "none",
            "场景要求+预算 → 加条件 → state_new_need")

    # ⑤ 三段（用兼容表）
    for _ in range(100):
        idn = rnd.choice(list(IDENTITY_USE))
        use = rnd.choice(IDENTITY_USE[idn])
        bud = rnd.choice(BUDGET)
        add("%s，主要%s，预算%s" % (idn, use, bud),
            "state_new_need", "none",
            "身份+用途+预算 → 加条件 → state_new_need")

    # ⑥ 装机：动作 × 用途
    for verb, use in itertools.product(BUILD_VERB, USE):
        add("%s%s的" % (verb, use), "state_new_need", "none",
            "「%s」是组装动作 + 用途 → 加条件" % verb)

    # ⑦ 装机：动作 × 预算
    for verb, bud in itertools.product(BUILD_VERB, BUDGET[:8]):
        add("%s%s的电脑" % (verb, bud), "state_new_need", "none",
            "「%s」是组装动作 + 预算 → 加条件" % verb)

    # ═══ ask_current ═══
    SUBJ = ["这台", "这个", "它", "这个机器", "这机器", "这一款"]
    ATTR = [
        ("多重", "weight"), ("重量多少", "weight"),
        ("续航几小时", "battery"), ("电池多大", "battery"),
        ("屏幕怎么样", "screen"), ("分辨率多少", "screen"),
        ("散热行不行", "noise"), ("噪音大不大", "noise"),
        ("键盘手感如何", "keyboard"), ("打字舒服吗", "keyboard"),
        ("做工怎么样", "build"), ("结实吗", "build"),
        ("有几个USB", "service"), ("能接显示器吗", "service"),
        ("保修几年", "service"), ("多少钱", "price"),
        ("有别的颜色吗", "none"), ("内存多大", "config"),
        ("显卡什么型号", "config"), ("能加内存吗", "config"),
    ]
    for subj, (attr, topic) in itertools.product(SUBJ, ATTR):
        add("%s%s" % (subj, attr), "ask_current", topic,
            "「%s」指代当前 + 问%s → ask_current/%s"
            % (subj, attr, topic))

    # ═══ refuse_switch ═══
    SWITCH = [
        "换一台", "换一个", "换别的", "换个别的", "换一款",
        "再换一台", "还有别的吗", "能看看别的吗", "再看看别的",
        "还有没有其他的", "推荐个别的", "这个不行", "不太满意",
        "不合适", "再看看", "再来一台", "换个便宜的", "换个贵的",
        "换个轻的", "换个屏幕大的", "这个太贵了", "这个太重了",
        "这台不合适", "不想要这个", "pass 换下一个", "这个不好",
    ]
    for s in SWITCH:
        add(s, "refuse_switch", "none",
            "「%s」= 不要当前这台 → refuse_switch" % s)

    # ═══ list_others ═══
    LIST = [
        "把其余候选列出来", "全部列出", "都列出来", "还有哪些",
        "剩下的都说说", "把剩下的都列出来", "一次看完所有候选",
        "列个清单", "都给我看看", "其余的列个表", "剩下的都给我",
        "把所有候选列成表", "全部候选都说一下", "其他的一并列出",
        "给我完整清单", "所有候选列出来", "全部机器列个表",
        "一次性列完", "我要看全部", "把候选全列出来",
    ]
    for s in LIST:
        add(s, "list_others", "none",
            "「%s」= 要全部剩余清单 → list_others" % s)

    # ═══ state_range ═══
    RANGE = [
        "5000到6000", "3000-5000", "6000 的游戏本", "一万以内",
        "八千左右", "六七千的轻薄本", "一万五", "两万以上",
        "四千到七千", "8000到12000之间", "五千以下", "一万二",
        "六千上下", "三千多的", "两万左右", "9000-13000",
        "四千以内", "七千左右", "三千到六千", "一万到一万五",
        "5000-7000", "8000以内", "六千五", "一万八",
    ]
    for s in RANGE:
        add(s, "state_range", "none",
            "只给价位/范围，没疑问词 → state_range")

    # ═══ analyze ═══
    AGG = [
        ("有多少台有独显", "config"), ("多少台是32G的", "config"),
        ("各品牌数量对比", "none"), ("各品牌各多少台", "none"),
        ("平均价格是多少", "price"), ("平均多重", "weight"),
        ("平均续航多少", "battery"), ("价格分布是什么样的", "price"),
        ("重量分布集中在哪", "weight"), ("哪个品牌最多", "none"),
        ("哪个牌子最贵", "none"), ("最便宜的是哪台", "price"),
        ("最轻的机器多重", "weight"), ("性能最强的是哪台", "none"),
        ("最强的五台", "none"), ("库里一共多少台", "none"),
        ("统计一下品类", "none"), ("中位数多少", "price"),
        ("多少台支持雷电4", "service"), ("续航最长的哪台", "battery"),
        ("最贵的是哪台", "price"), ("最便宜的多少钱", "price"),
        ("平均下来一台多少", "price"), ("各品类平均重量", "weight"),
        ("重量中位数", "weight"), ("多少台是核显的", "config"),
        ("统计各品牌数量", "none"), ("性能排行前十", "none"),
        ("价格最高的几台", "price"), ("续航分布情况", "battery"),
    ]
    for s, topic in AGG:
        add(s, "analyze", topic,
            "「%s」是聚合/极值 → analyze/%s" % (s, topic))

    # ═══ unknown ═══
    UNK = [
        "啊啊啊", "在吗", "嗯", "那个", "？", "随便看看",
        "。。。", "??", "嗯嗯", "在不在", "不知道买啥", "你好",
        "有点纠结", "再说吧", "嗯这个", "不知道", "emmm",
        "啊这", "哦哦", "随便",
    ]
    for s in UNK:
        add(s, "unknown", "none", "没给出任何可用条件 → unknown")


def make(user, intent, topic, cot, with_cot):
    gold = {"intent": intent, "topic": topic, "fit": False}
    js = json.dumps(gold, ensure_ascii=False)
    content = ("%s。\n%s" % (cot, js)) if with_cot else js
    return {
        "messages": [
            {"role": "user", "content": user},
            {"role": "assistant", "content": content},
        ],
        "_meta": {"src": "expand-seed", "has_cot": with_cot},
    }


def main():
    gen()
    rnd = random.Random(20261130)
    print("=" * 76)
    print("扩充数据质量修复 + 分布均衡")
    print("=" * 76)
    print()

    # 按意图分组
    from collections import Counter, defaultdict
    by_intent = defaultdict(list)
    for item in S:
        by_intent[item[1]].append(item)

    print("  修复前分布:")
    for k, v in sorted(by_intent.items(), key=lambda x: -len(x[1])):
        print("    %-16s %4d" % (k, len(v)))
    print("    合计 %d" % len(S))
    print()

    # 🔴 均衡：样本多的意图降采样到 240 条上限
    CAP = 240
    balanced = []
    for k, items in by_intent.items():
        if len(items) > CAP:
            rnd.shuffle(items)
            kept = items[:CAP]
            print("  %s: %d → %d（降采样）" % (k, len(items), CAP))
            balanced.extend(kept)
        else:
            balanced.append if False else balanced.extend(items)

    print()
    cnt = Counter(x[1] for x in balanced)
    print("  最终分布:")
    total = len(balanced)
    for k, v in cnt.most_common():
        print("    %-16s %4d  %5.1f%%" % (k, v, 100.0 * v / total))
    print("    合计 %d" % total)

    rows = [make(u, i, t, c, rnd.random() < 0.30) for u, i, t, c in balanced]
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print()
    print("  写出 %s（%d 条）" % (OUT, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
