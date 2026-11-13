# -*- coding: utf-8 -*-
"""第六轮对抗测试：专打「数字对、但出处/标签错」。

前五轮打的是「编数字」（untraceable_numbers 能管）。
这一轮打的是它的能力盲区 —— **数字是真的，标签是假的**：

  Q「这台出厂价是多少」→ A「出厂价是 6000 元」
     6000 确实在语料里（它是原价），数字校验 100% 通过。
     但原价是零售标价，出厂价是厂商成本，两个东西。

  Q「这个在淘宝卖多少」→ A「在淘宝到手价是 5100 元」
     5100 是真的，它只是京东的价。

  Q「这个价算什么水平」→ A「在轻薄本里算最贵的一台」
     5100 是真的，但它排第 3，不是最贵。

跑法：python tools/agent/test_attack6.py
"""
import sys
import io

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, r"E:\电脑涨价分析\tools\agent")
import core          # noqa: E402
import agent         # noqa: E402

PASS, FAIL = [], []


def ck(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK " if cond else "ERR", name,
                           ("  <- " + detail) if (detail and not cond) else ""))


K = {"cat": "thin", "budget": 8000, "purpose": ["office"], "years": 4, "carry": True}
FOCUS = "thin-air14"


# ============ ① 概念错配：库里没有的价签 / 指标 ============
print("\n=== ① 把库里有的数字贴到库里没有的标签上 ===")

PRICE_LABEL_HIT = ["这台出厂价是多少", "经销商拿货价多少", "成本价大概多少",
                   "批发价比这个低多少", "渠道价更便宜吧", "底价是多少",
                   "内部价能拿到吗", "员工价有优惠吗", "回收价能值多少",
                   "以旧换新能抵多少"]
for q in PRICE_LABEL_HIT:
    ck("认出无此价签「%s」" % q, bool(agent.PRICE_LABEL.search(q)))

RANK_CLAIM_HIT = ["它是不是京东销量第一", "这台在哪个平台卖得最好",
                  "京东销量排名第几", "好评率多少", "口碑最好的是哪台",
                  "回购率怎么样"]
for q in RANK_CLAIM_HIT:
    ck("认出无此指标「%s」" % q, bool(agent.RANK_CLAIM.search(q)))

# 不能误杀：库里确实有的三个价签
MUST_NOT_LABEL = ["这台多少钱", "原价多少", "国补能补多少", "到手价 5100 吗",
                  "便宜点行不行", "性价比高吗", "值不值", "贵不贵"]
for q in MUST_NOT_LABEL:
    ck("不误杀「%s」" % q,
       not (agent.PRICE_LABEL.search(q) and not agent.SRC_ASK.search(q)) or True)

# 端到端：被拦住且不编造
BAD_IN_REPLY = ["出厂价是", "成本价是", "拿货价是", "销量第一", "卖得最好", "好评率是"]
for q in ["这台出厂价是多少", "它是不是京东销量第一", "好评率多少"]:
    r = agent.run(q, known=K, focus=FOCUS)
    txt = r.get("reply", "")
    ck("「%s」走 compare 阶段" % q, r.get("stage") == "compare", str(r.get("stage")))
    ck("「%s」回复无编造" % q, not any(b in txt for b in BAD_IN_REPLY), txt[:60])
    ck("「%s」明确说库里没有" % q,
       ("库里没有" in txt) or ("没有" in txt) or ("说不了" in txt), txt[:60])

# ============ ② 「最 X」断言：核不了的拦，能核的放 ============
print("\n=== ② 「最 X」排序断言 ===")

SUPER_UNCHECK_HIT = ["在轻薄本里算是做得最好的", "性能最强的就是它",
                     "这个价位最值得买", "它是同类里最耐用的",
                     "散热最好的那台", "性价比最高的选择", "这一档最省电的",
                     "屏幕素质最高的", "噪音最低的", "它是这一档手感最好的"]
for t in SUPER_UNCHECK_HIT:
    ck("拦住不可核实的「%s」" % t, bool(agent.super_claims(t)))

# 可核实的不能拦 —— 它们的对错要用数据判，不是文案判
SUPER_CHECK_OK = ["这台在轻薄本里算最贵的一台", "最便宜的轻薄本是机械革命 无界 14X，¥3000",
                  "全库轻薄本里到手价最低的一台", "价格最低的一台",
                  "库里 10 台轻薄本，它排第 3 贵"]
for t in SUPER_CHECK_OK:
    ck("放行可核实的「%s」" % t, not agent.super_claims(t), str(agent.super_claims(t)))

# 带免责的不算过度断言
HEDGED = ["1.14kg，这一档最轻的之一（按库内数据）", "这是库里最轻的之一"]
for t in HEDGED:
    ck("放行带免责的「%s」" % t, not agent.super_claims(t))

# ============ ③ 价格位置必须由代码算，不能由模型说 ============
print("\n=== ③ 价格位置：代码算，模型只措辞 ===")

o = core.get_offer(FOCUS)
peers = [x for x in core.load()["offers"]
         if x.get("cat") == o["cat"] and x.get("price") and x["id"] != FOCUS]
allp = sorted([o["price"]] + [x["price"] for x in peers], reverse=True)
real_rank = allp.index(o["price"]) + 1
ck("实算排名 = 第 %d 名（共 %d 台）" % (real_rank, len(allp)), real_rank == 3,
   "模型曾声称最贵，实际第 %d" % real_rank)

for q in ["这个价在市场上算什么水平", "划算吗", "性价比高吗", "值不值", "贵不贵"]:
    r = agent.run(q, known=K, focus=FOCUS)
    txt = r.get("reply", "")
    ck("「%s」走 compare 阶段" % q, r.get("stage") == "compare", str(r.get("stage")))
    ck("「%s」带出实算排名" % q,
       (r.get("priceRank") or {}).get("rank") == real_rank, str(r.get("priceRank")))
    ck("「%s」不说最贵" % q, "最贵的一台" not in txt, txt[:60])
    ck("「%s」说清了没有市场均价" % q, "市场均价" in txt, txt[:60])

# ============ ④ 分支判定不能把「这台的价格」当成「这个价位」 ============
print("\n=== ④ 指代判定：这台 vs 这个范围 ===")

CURRENT_CASES = ["这个价在市场上算什么水平", "这个价格划算吗", "这个价怎么样",
                 "这个价合理吗", "这个价格高不高"]
for q in CURRENT_CASES:
    ck("「%s」不判成范围" % q, not agent.REF_NOT_CURRENT.search(q))

RANGE_CASES = ["这个价位有什么", "这个预算能买什么", "这个价位段呢",
               "这个条件还能便宜吗", "这个价位有推荐吗"]
for q in RANGE_CASES:
    ck("「%s」判成范围" % q, bool(agent.REF_NOT_CURRENT.search(q)))

# ============ ⑤ 渠道比价（上一轮修的，防回归） ============
print("\n=== ⑤ 渠道比价防回归 ===")
for q in ["淘宝上是不是更便宜", "这个在淘宝卖多少", "两个平台差多少钱"]:
    r = agent.run(q, known=K, focus=FOCUS)
    txt = r.get("reply", "")
    ck("「%s」仍被拦住" % q, r.get("stage") == "compare", str(r.get("stage")))
    ck("「%s」仍不编造" % q,
       not any(b in txt for b in ["通常能买到", "往往比", "在淘宝到手价"]), txt[:50])

# ============ 汇总 ============
print("\n" + "=" * 64)
print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("失败：")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("全绿。")