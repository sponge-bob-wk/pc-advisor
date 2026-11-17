# -*- coding: utf-8 -*-
"""意图层对抗集 —— 真人怎么说，不是测试怎么造（2026-11-13）

## 为什么需要这一份

`test_intent.py` 里的句子是**配合判据写的** ——
「这台怎么样」「保修几年」「我要 32G 内存」，每条都正好命中某个词表。
这类句子测的是"词表有没有写全"，测不出"用户的说法有没有超出词表"。

真人说话不走词表：
  · 省略主语：「屏幕会不会太小」—— 词表里 CURRENT_REF 有"这个/这台"，这句没有
  · 口语缩写：「贵不贵啊」「值不值」「行不」
  · 情绪+需求混在一起：「有点贵但还挺喜欢的，有没有类似但便宜些的」
  · 前提藏在从句里：「如果我要跑模型的话这个够吗」
  · 反问：「这屏幕你确定够我用」
  · 方言/网络语：「这机器咋样」「这台顶不顶」

## 这份数据干什么用

1. 量出规则在**真实说话方式**下的准确率（不是测词表覆盖率）
2. 当模型评测试金石：谁在这批上更准，谁就该接管这一层
3. 训好之后防止回退

## 标签口径

沿用 `agent.classify_intent` 的六类：
  ASK_CURRENT     问当前这台的细节（屏幕/保修/重量/够不够用/价格值不值）
  REFUSE_SWITCH   不要这台 / 要换台 / 要别的
  STATE_RANGE     说价位段 / 品类 / 条件范围，不指向某一台
  STATE_NEW_NEED  提新需求（要 32G、要游戏本、要能加内存）
  LIST_OTHERS     要把其余候选列出来
  UNKNOWN         什么都没说清

## 标注纪律（判错代价不对称，按这个定标签）

  · 把 ASK 判成别的 → 用户被打回第一句，烦但能补一句救回来
  · 把别的判成 ASK → **用户提的新需求被当成追问，需求直接丢了**
⇒ 拿不准时，**倒向"不是 ASK"**。
"""
import sys

FOCUS = "thin-wujie14x"

# (句子, 期望意图, 为什么这么标)
CASES = [
    # ══ 1. ASK_CURRENT：省略主语的追问 ══════════════════════════
    # 这组是重点：真人很少说"这台怎么样"，主语全省了
    ("屏幕会不会太小", "ask_current", "省主语，问的是当前这台的屏幕"),
    ("多重啊", "ask_current", "省主语"),
    ("能撑四年不", "ask_current", "省主语+口语"),
    ("散热行不行", "ask_current", "省主语"),
    ("带出去累不累", "ask_current", "问便携，只有当前这台有意义"),
    ("这屏幕咋样", "ask_current", "口语"),
    ("键盘手感怎么样", "ask_current", "维度词，指向当前"),
    ("电池能顶一天吗", "ask_current", "维度词"),
    ("打游戏行吗", "ask_current", "问匹配度"),
    ("我学计算机的够用吗", "ask_current", "问匹配度，专业已给过"),
    ("这机器顶不顶", "ask_current", "网络语"),
    ("值得买吗", "ask_current", "价格评价，当前这台才有意义"),
    ("卖这么贵值在哪", "ask_current", "价格评价"),
    ("有什么坑没有", "ask_current", "问短板"),
    ("有啥缺点", "ask_current", "口语，问短板"),
    ("续航怎么样", "ask_current", "维度词"),

    # ══ 2. REFUSE_SWITCH：不要 / 换 ═════════════════════════════
    ("这个不行", "refuse_switch", "明确否定"),
    ("不太喜欢这个", "refuse_switch", "情绪否定"),
    # 🔴 2026-11-13 晚：下面三条从 refuse_switch 改成 list_others。
    #
    #    理由不是"我觉得应该"，是**实测行为对照**（known: thin/5000/office，seen: 战66）：
    #       判 refuse_switch → 只推 1 台，回复 1 行，用户要再问两次才能看全
    #       判 list_others   → 一次给出 2、3 名，回复 4 行
    #
    #    这三句用户想说的是"你手上还有什么"，不是"把当前这台扔掉"。
    #    判成换台的后果：用户以为能一次看全，实际要问三次。
    #
    #    ⚠️ 同一批句子在 test_intent.py [1b] 里也是 list_others ——
    #       两处必须一致，否则就是"判据抄了两遍还不一样"（宪法第 9 条）。
    #       真想换台的人说的是"换一台/这个不行/这台不要了"，那几句仍旧判 REFUSE_SWITCH。
    ("有点丑，还有别的吗", "list_others", "否定+要其余候选（实测给清单更对）"),
    ("能看看别的不", "list_others", "口语要其余候选（实测）"),
    ("再给我看几个", "list_others", "要其余候选（实测）"),
    ("这个牌子我不太行", "refuse_switch", "否定品牌"),
    ("算了换一个吧", "refuse_switch", "换台"),
    ("太贵了买不起", "refuse_switch", "嫌贵，隐含换台"),
    ("这价格劝退", "refuse_switch", "网络语，拒绝"),
    ("pass，下一个", "refuse_switch", "网络语，换台"),

    # ══ 3. STATE_RANGE：说范围，不指向某台 ═══════════════════════
    ("六七千的有什么", "state_range", "说价位段"),
    ("五千到八千之间", "state_range", "说区间"),
    ("轻薄本都有啥", "state_range", "说品类"),
    ("这个价位段还有哪些", "state_range", "说范围"),
    ("一万以内能买啥", "state_range", "说区间"),
    ("同价位的对比一下", "state_range", "说范围+对比"),

    # ══ 4. STATE_NEW_NEED：提新需求 ══════════════════════════════
    ("我想学剪辑，要能跑剪映", "state_new_need", "新用途"),
    ("帮我找个能加内存的", "state_new_need", "新约束"),
    ("换个 32G 的", "state_new_need", "改配置"),
    ("有没有带独显的", "state_new_need", "新约束"),
    ("我改主意了，要台式的", "state_new_need", "改品类"),
    ("想要个白色或者银色的", "state_new_need", "新偏好"),
    ("最好轻一点", "state_new_need", "新约束"),
    ("要能跑得动 PS 的", "state_new_need", "新用途"),

    # ══ 5. LIST_OTHERS：要清单 ═══════════════════════════════════
    ("剩下的都列出来", "list_others", "要清单"),
    ("还有几个选择一次说完", "list_others", "要清单"),
    ("把候选都给我看看", "list_others", "要清单"),

    # ══ 6. UNKNOWN：无实义 ═══════════════════════════════════════
    ("嗯", "unknown", "无实义"),
    ("哦", "unknown", "无实义"),
    ("。。。", "unknown", "纯标点"),
    ("在吗", "unknown", "社交，无需求"),
]


def label_to_const(agent, name):
    return {
        "ask_current": agent.INTENT_ASK_CURRENT,
        "refuse_switch": agent.INTENT_REFUSE_SWITCH,
        "state_range": agent.INTENT_STATE_RANGE,
        "state_new_need": agent.INTENT_STATE_NEW_NEED,
        "list_others": agent.INTENT_LIST_OTHERS,
        "unknown": agent.INTENT_UNKNOWN,
    }[name]