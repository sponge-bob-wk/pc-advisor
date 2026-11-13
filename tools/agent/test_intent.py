# -*- coding: utf-8 -*-
"""意图分类器的独立单测。

背景：第七轮之后把「这句是不是在问当前这台」从 9 条 if 顺序判断，
重构成 classify_intent() 的两阶段分类。重构最大的风险是**悄悄改掉行为** ——
原来的测试只验「refers_to_current 的 True/False」，验不到「为什么不 True」。
所以这里直接测五类意图本身。

分类错了的代价不对称，测的时候按这个来定权重：
  · 把 ASK_CURRENT 判成别的  → 用户被打回第一句（烦，但补一句还能救）
  · 把别的判成 ASK_CURRENT  → 用户提的新需求被当成追问，**需求直接丢了**（严重）
⇒ 后一类每一条都必须过。
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent

OK = 0
BAD = 0
FAILS = []


def ck(name, cond, note=""):
    global OK, BAD
    if cond:
        OK += 1
    else:
        BAD += 1
        FAILS.append((name, note))
        print("  [FAIL] %s   %s" % (name, note))


FOCUS = "thin-wujie14x"

print("=" * 70)
print("意图分类器单测")
print("=" * 70)

# ---------------------------------------------------------------- 1
print("\n[1] ASK_CURRENT：在问这台")
ASK = [
    # 有指代词
    "这台怎么样", "这个机器怎么样", "这机器保修几年", "该机型有货吗",
    "这笔记本能用四年吗", "它屏幕怎么样", "刚才那台多重",
    # 确认式
    "我说的就是刚刚你给我推荐的那一台", "就是这台", "指的就是它",
    # 无指代词但问维度
    "保修几年", "多重啊", "散热怎么样", "屏幕素质咋样",
    # 无指代词但问匹配度
    "还能打游戏吗", "够用吗", "能跑得动吗",
    # 2026-11-12 用户实测：口语里很少带"吗"
    #   「没独显确定够用？」→ 被判成新需求，把同一台又推了一遍
    #   用户原话："它又推荐一次不回答我问题"
    "没独显确定够用？", "这个配置够用", "没独显可以吗",
    # 价格评价（第六轮成果，不能回退）
    "这个价怎么样", "这个价在市场上算什么水平", "这价格划算吗",
]
for t in ASK:
    got = agent.classify_intent(t, FOCUS)
    ck("ASK_CURRENT：%s" % t, got == agent.INTENT_ASK_CURRENT, "实际 %s" % got)

# ---------------------------------------------------------------- 1b
print("\n[1b] LIST_OTHERS：要清单，不是换台")
LIST_ASK = [
    "其余推荐的给我列举出信息我看看",
    "把其他的列出来",
    "剩下的分别说说",
]
for t in LIST_ASK:
    got = agent.classify_intent(t, FOCUS)
    ck("LIST_OTHERS：%s" % t, got == agent.INTENT_LIST_OTHERS, "实际 %s" % got)
# 对照：这句话是换台，不是要清单 —— 判错会变成"列一遍清单"，用户想换机却没换
ck("对照·「还有别的吗」仍然是换台",
   agent.classify_intent("还有别的吗", FOCUS) == agent.INTENT_REFUSE_SWITCH,
   "实际 %s" % agent.classify_intent("还有别的吗", FOCUS))

# ---------------------------------------------------------------- 2
print("\n[2] 最危险的一类：不能把新需求判成 ASK_CURRENT（需求会丢）")
NOT_ASK_CRITICAL = [
    ("我要一台游戏本", agent.INTENT_STATE_NEW_NEED),
    ("给我推荐个 8000 的", agent.INTENT_STATE_NEW_NEED),
    ("预算 6000 想买轻薄本", agent.INTENT_STATE_NEW_NEED),
    ("我要这个机器的升级版", agent.INTENT_STATE_NEW_NEED),
    ("我要 32G 内存", agent.INTENT_STATE_NEW_NEED),
    # 说范围
    ("这个价位有什么", agent.INTENT_STATE_RANGE),
    ("这个预算能买什么", agent.INTENT_STATE_RANGE),
    ("这个价格的机器有什么", agent.INTENT_STATE_RANGE),
    ("这种机器的价格区间", agent.INTENT_STATE_RANGE),
    ("这个型号和那个型号哪个好", agent.INTENT_STATE_RANGE),
    # 换台 / 否定
    ("换一台", agent.INTENT_REFUSE_SWITCH),
    ("还有别的吗", agent.INTENT_REFUSE_SWITCH),
    ("这台不合适", agent.INTENT_REFUSE_SWITCH),
    ("这个不行", agent.INTENT_REFUSE_SWITCH),
    ("这台不要了", agent.INTENT_REFUSE_SWITCH),
]
for t, want in NOT_ASK_CRITICAL:
    got = agent.classify_intent(t, FOCUS)
    ck("%s → %s：%s" % (want, "不能是 ASK", t), got == want, "实际 %s" % got)

# ---------------------------------------------------------------- 3
print("\n[3] 疑问不能判成否定")
for t in ["这台行不行", "这个行不行", "这台合适不合适", "这个性能行不行",
          "这台不合适吗", "这台不适合我吗", "行不行啊"]:
    got = agent.classify_intent(t, FOCUS)
    ck("疑问非否定：%s" % t, got != agent.INTENT_REFUSE_SWITCH, "实际 %s" % got)

# 真否定必须是 REFUSE
for t in ["这台不合适", "这台不适合我", "这个不行", "换掉这台", "算了不要这台"]:
    got = agent.classify_intent(t, FOCUS)
    ck("真否定：%s" % t, got == agent.INTENT_REFUSE_SWITCH, "实际 %s" % got)

# ---------------------------------------------------------------- 4
print("\n[4] 空输入 / 无实义 → UNKNOWN（触发反问，这是对的）")
for t in ["", "   ", "？？？", "😀", "啊", "嗯"]:
    got = agent.classify_intent(t, FOCUS)
    ck("UNKNOWN：%r" % t, got == agent.INTENT_UNKNOWN, "实际 %s" % got)

# 但「啊」重复很多次仍然 UNKNOWN，不能因为变长就猜意图
got = agent.classify_intent("啊" * 400, FOCUS)
ck("超长无实义仍 UNKNOWN", got == agent.INTENT_UNKNOWN, "实际 %s" % got)

# ---------------------------------------------------------------- 5
print("\n[5] 没有 focus 时：不能凭空认成 ASK_CURRENT")
for t in ["这台怎么样", "保修几年", "这个机器怎么样", "就是这台"]:
    got = agent.classify_intent(t, None)
    ck("无 focus 不认 ASK：%s" % t, got != agent.INTENT_ASK_CURRENT, "实际 %s" % got)

# ---------------------------------------------------------------- 6
print("\n[6] 分类完整性：任何输入都必须落在五类里，不能返回 None")
ALL_INPUTS = ASK + [x[0] for x in NOT_ASK_CRITICAL] + ["", "😀", "啊" * 50]
LEGAL = {agent.INTENT_ASK_CURRENT, agent.INTENT_REFUSE_SWITCH,
         agent.INTENT_STATE_RANGE, agent.INTENT_STATE_NEW_NEED, agent.INTENT_UNKNOWN}
bad_type = [(t, agent.classify_intent(t, FOCUS)) for t in ALL_INPUTS
            if agent.classify_intent(t, FOCUS) not in LEGAL]
ck("分类值全合法", not bad_type, str(bad_type[:3]))

# 不能抛异常
crash = []
for t in ALL_INPUTS + ["\x00", "a" * 2000, "，" * 200]:
    try:
        agent.classify_intent(t, FOCUS)
    except Exception as e:
        crash.append((t[:20], type(e).__name__))
ck("任何输入都不抛异常", not crash, str(crash[:3]))

print("\n" + "=" * 70)
print("通过 %d / %d" % (OK, OK + BAD))
if FAILS:
    print("\n失败明细：")
    for n, note in FAILS:
        print("  - %s   %s" % (n, note))
else:
    print("全绿。")
print("=" * 70)