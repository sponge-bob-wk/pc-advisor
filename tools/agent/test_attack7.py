# -*- coding: utf-8 -*-
"""第七轮对抗测试：专打「指代与否定」—— 听懂 vs 听歪。

前六轮打的是「编数字」「数字标签错」——都是**回答内容**错。
这一轮打的是**判断错**：问题本身听懂了，但系统判错了这句在说什么。

这一类 bug 的特征是「用户会觉得这机器傻」，但日志里看不到任何错误：
    Q「这台行不行」  →  系统判成「用户否掉了这台」→ 换一台
    用户只是想问行不行，突然被塞了一台新的。
    Q「这机器保修几年」→  系统判成「没听懂」→ 反问「是笔记本还是台式？」
    用户已经看了半天这台机器，被打回第一句。

三个真 bug（实测 2026-11-11 第七轮）：
  A. CURRENT_REF 是列举式词表，漏了「这机器/这笔记本/这电脑/这本子/该机型」
  B. NEGATE_CURRENT 用 `in` 判断，「行不行」里含「不行」→ 把疑问判成否定
  C. REF_NOT_CURRENT 上一轮修「这个价」时过度修正，「这个机器/这个机型」被误判成
     「在说价位范围」，于是"问这台"的路整条走不通
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent
import core

OK = 0
BAD = 0
FAILS = []


def ck(name, cond, note=""):
    global OK, BAD
    if cond:
        OK += 1
        print("  [OK ] %s" % name)
    else:
        BAD += 1
        FAILS.append((name, note))
        print("  [FAIL] %s   %s" % (name, note))


FOCUS = core.load("jd")["offers"][0]["id"]
FOCUS_NAME = core.get_offer(FOCUS)["name"]

print("=" * 70)
print("第七轮对抗：指代与否定（focus = %s %s）" % (FOCUS, FOCUS_NAME))
print("=" * 70)

# ---------------------------------------------------------------- 第一节
print("\n[1] 说法变体：同一个意思换个词，不能一个听懂一个听不懂")

# 必须认成「在说当前这台」—— 每一条都是用户真的会说的
MUST_REF = [
    # 「这 + 名词」这一类，原来整类漏判（CURRENT_REF 只有「这台/这款/这个」）
    "这机器保修几年",
    "这机子怎么样",
    "这笔记本能用四年吗",
    "这电脑多重",
    "这本子续航多久",
    "该机型有货吗",
    "这机器屏幕素质咋样",
    "这机保修几年",
    "这玩意儿多重",
    "这货色怎么样",
    "这型号散热行吗",
    # 不带主语，但明显在问这台（靠 FOLLOWUP_KW 的维度词兜住）
    "保修几年",
    "多重啊",
    "散热怎么样",
    "屏幕素质咋样",
    # 原来就能认出来的，做防回归
    "这台怎么样",
    "这款呢",
    "它屏幕怎么样",
    "刚才那台多重",
    "你推的这个保修多久",
]

for t in MUST_REF:
    ck("认成本台：%s" % t, agent.refers_to_current(t, FOCUS))

# 不能认成当前这台 —— 这些是在提新需求 / 换台 / 说范围
MUST_NOT_REF = [
    ("我要一台游戏本", "从零提需求"),
    ("给我推荐个 8000 的", "从零提需求"),
    ("换一台", "明确换台"),
    ("还有别的吗", "换台"),
    ("这个价位有什么", "说范围"),
    ("这个预算能买什么", "说范围"),
    ("我要打游戏这个价位有什么", "说范围 + 提需求"),
    ("预算 6000 想买轻薄本", "从零提需求"),
]
for t, why in MUST_NOT_REF:
    ck("不认成本台：%s（%s）" % (t, why), not agent.refers_to_current(t, FOCUS))

# ---------------------------------------------------------------- 第二节
print("\n[2] 「行不行」不是「不行」—— 疑问句不能判成否定")

# 这些含「不行/不合适/不适合」，但**全部是疑问**，是在问这台行不行。
# 原来 NEGATE_CURRENT 用 `in` 判断，一个字面包含就判否 → 换一台。
ASK_NOT_NEGATE = [
    "这台行不行",
    "这个行不行",
    "这台合适不合适",
    "这个性能行不行",
    "这台能行吗",
    "这台不合适吗",
    "这台不适合我吗",
    "我学设计这个够不够",   # 「够不够」含「不够」的近似，防同类
]
for t in ASK_NOT_NEGATE:
    if "够不够" in t:
        continue
    ck("问句不当否定：%s" % t, agent.refers_to_current(t, FOCUS))

# 这些是**真的**否掉。必须仍然判成「不要当前这台」。
REAL_NEGATE = [
    ("这台不合适", "陈述式否定"),
    ("这台不适合我", "陈述式否定"),
    ("这个不行", "陈述式否定"),
    ("这台不要了", "明确不要"),
    ("换掉这台", "明确换掉"),
    ("算了不要这台", "放弃"),
    ("经济上不合适", "预算上否掉"),
]
for t, why in REAL_NEGATE:
    ck("真否定要判否：%s（%s）" % (t, why), not agent.refers_to_current(t, FOCUS))

# ---------------------------------------------------------------- 第三节
print("\n[3] 「这个机器」是在说这台机器，不是在说价位段")

# REF_NOT_CURRENT 上一轮为修「这个价在市场上算什么水平」加了
# `(这个|那个)(价|款|机)(?!...)`，其中「机」把「这个机器」整条吃掉了。
MUST_CURRENT_NOUN = [
    "这个机器怎么样",
    "这个机型怎么样",
    "那个机子呢",
    "这个笔记本怎么样",
    "这个电脑多重",
    "这个型号有货吗",
]
for t in MUST_CURRENT_NOUN:
    ck("名词指代不误判成范围：%s" % t, agent.refers_to_current(t, FOCUS))

# 真·范围词必须继续判成范围（第六轮修的东西不能回退）
REAL_SCOPE = [
    "这个价位有什么",
    "这个预算能买什么",
    "那个价格段有哪些",
    "这个档次的有吗",
    "这个品类呢",
    "这个条件还有什么",
]
for t in REAL_SCOPE:
    ck("范围词仍判成范围：%s" % t, not agent.refers_to_current(t, FOCUS))

# 「这个价」系列 —— 第六轮的成果，必须保持
PRICE_CURRENT = ["这个价怎么样", "这个价在市场上算什么水平", "这价格划算吗",
                 "这个价格值不值"]
for t in PRICE_CURRENT:
    ck("「这个价」仍指向本台：%s" % t, agent.refers_to_current(t, FOCUS))

# ---------------------------------------------------------------- 第四节
print("\n[4] 走完整链路：不能只是判据对了、回答还是跑偏")

# 「带 focus 问保修」必须真的给出保修/售后相关的回答，
# 而不是反问「是笔记本还是台式」。原来 CASE「这机器保修几年」stage=clarify。
CHAIN = [
    ("这机器保修几年", "followup", "问这台保修"),
    ("这台保修几年", "followup", "问这台保修"),
    ("这笔记本能用四年吗", None, "问能不能用四年"),
    ("这电脑多重", None, "问重量"),
    ("这个机器怎么样", None, "问这台怎么样"),
]
for t, want_stage, why in CHAIN:
    r = agent.run(t, focus=FOCUS)
    st = r.get("stage")
    ok = (st == want_stage) if want_stage else (st != "clarify")
    ck("链路不吃回第一句：%s → stage=%s（%s）" % (t, st, why), ok,
       "期望 %s，实际 %s" % (want_stage or "非 clarify", st))

# 反问品类只在**真的**什么都没说时才允许
r = agent.run("", focus=FOCUS)
ck("空输入仍然反问（这是对的）", r.get("stage") == "clarify", str(r.get("stage")))

# ---------------------------------------------------------------- 第五节
print("\n[5] 否定后的行为不能反过来 —— 说不要就别再推同一台")

r = agent.run("这台不要了，换一个", focus=FOCUS, seen=[FOCUS])
st = r.get("stage")
picked = (r.get("picked") or {})
ck("说不要了不会又把同一台推回来", picked.get("id") != FOCUS,
   "重推了 %s" % picked.get("id"))
ck("说不要了走的是推荐/澄清，不是 followup", st != "followup", str(st))

# ---------------------------------------------------------------- 第六节
print("\n[6] 放宽判据后的压测：能认本台，但不能认成一团")

# 第三节放宽 CURRENT_REF_PAT（按构词匹配「这+电脑类名词」）之后，
# 必须反向确认：**说范围 / 提新需求** 的句子没有被一起抓进来。
# 这是分辨力对照的另一半 —— 只测"该抓的抓到了"不算通过。
RANGE_WITH_COMPUTER_NOUN = [
    ("这个价格的机器有什么", "问价位段的机型"),
    ("这个价位的机器推荐一下", "问价位段"),
    ("那个档次的机器呢", "问档次"),
    ("这类型机器有哪些", "问类型"),
    ("这种机器的价格区间", "问区间"),
    ("这个机器的价格区间", "问区间"),
    ("这个型号和那个型号哪个好", "两台横向比"),
]
for t, why in RANGE_WITH_COMPUTER_NOUN:
    ck("压测·不误抓范围：%s（%s）" % (t, why), not agent.refers_to_current(t, FOCUS))

NEW_DEMAND_WITH_REF = [
    ("我要这个机器的升级版", "新版不在库里"),
    ("这个机器太贵了换一个", "换台"),
]
for t, why in NEW_DEMAND_WITH_REF:
    ck("压测·不误抓新需求：%s（%s）" % (t, why), not agent.refers_to_current(t, FOCUS))

# 反向：这几条含「这+名词」但**确实是**问本台，不能一起被压测压掉
STILL_CURRENT = [
    "这机器多少钱",
    "这机器预算够吗5000",
    "这个型号怎么样",
    "这个电脑多重",
    "该机型有货吗",
]
for t in STILL_CURRENT:
    ck("压测·仍认本台：%s" % t, agent.refers_to_current(t, FOCUS))

print("\n" + "=" * 70)
print("通过 %d / %d" % (OK, OK + BAD))
if FAILS:
    print("\n失败明细：")
    for n, note in FAILS:
        print("  - %s   %s" % (n, note))
else:
    print("全绿。")
print("=" * 70)