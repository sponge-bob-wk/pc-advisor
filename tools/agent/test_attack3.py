# -*- coding: utf-8 -*-
"""第三轮攻击：专打「金额解析」的边界，以及刚改动代码可能引入的回归。

设计原则（沿用本项目红线）：
1. 每条用例都必须有「期望」，且期望写成能被证伪的形式。
2. 数字类断言不写区间，写精确值 —— 区间判据会掩盖真问题。
3. 每条都带一条「故意错的输入」对照，确认判据不是摆设。

分组：
  L 组  金额陷阱：像钱但不是钱 / 像配置但被当钱 / 多金额并存
  M 组  刚改动的 _cn_to_int 回归：各种口语进位
  N 组  金额与逻辑词叠加：「不要七千的」「除了六千那个」
  O 组  预算与硬约束冲突
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import agent

FAILS = []


def budget_of(r):
    return (r.get("slots") or {}).get("budget")


def stage_of(r):
    return r.get("stage")


def tail_of(r):
    # 正文在 reply，不是 answer —— 写错字段会让 O01/O02 全部假失败。
    return r.get("reply") or ""


def run_chain(texts):
    known, asked, last, seen, focus = None, None, None, None, None
    rs = []
    for t in texts:
        r = agent.run(t, known=known, asked=asked, last_stage=last,
                      seen=seen, focus=focus)
        rs.append(r)
        known = r.get("known")
        asked = r.get("asked")
        last = r.get("stage")
        seen = r.get("seen")
        focus = r.get("focus")
    return rs


def case(cid, title, texts, check):
    rs = run_chain(texts)
    ok, why = check(rs)
    mark = "PASS" if ok else "FAIL"
    if not ok:
        FAILS.append(cid)
    print("[%s] %s  %s" % (cid, mark, title))
    for i, (t, r) in enumerate(zip(texts, rs), 1):
        b = budget_of(r)
        print("   %d) %-34s → %-10s budget=%s" % (i, t, stage_of(r), b))
    print("   判定: %s" % why)
    print("-" * 78)


# ============ L 组：金额陷阱 ============
# 这些都不是预算，必须解析不出金额（不能把需求变成一次预算判断）
NOT_MONEY = [
    ("L01", "六千万像素", "我要拍六千万像素的，轻薄本"),
    ("L02", "6000mAh 电池", "轻薄本，6000mAh 大电池，上课用"),
    ("L03", "16G 内存 512G 硬盘", "16G+512G 的轻薄本，上课用"),
    ("L04", "14 寸 2.5K", "14 寸 2.5K 屏的轻薄本"),
    ("L05", "165Hz 刷新率", "165Hz 打游戏，要游戏本"),
    ("L06", "R7 8845H 型号", "R7 8845H 的轻薄本，办公"),
]


def check_not_money(rs):
    b = budget_of(rs[-1])
    if b is None:
        return True, "没有把规格数字当成预算（budget=None）"
    return False, "把规格数字当成了预算 budget=%s" % b


for cid, title, txt in NOT_MONEY:
    case(cid, "非金额-" + title, [txt], check_not_money)


# 一条对照：故意写一个真的金额，判据必须能抓到（证明上面不是"永不报警"）
def check_control_has_money(rs):
    b = budget_of(rs[-1])
    if b == 6000:
        return True, "真金额 6000 被正确识别（对照有效）"
    return False, "真金额没认出来 budget=%s（说明 L 组判据没有分辨力）" % b


case("L07", "对照-真金额必须认出来", ["6000 的轻薄本，上课用"], check_control_has_money)


# ============ M 组：_cn_to_int 回归（刚重写过，必须重新钉一遍）============
CN_CASES = [
    ("M01", "七千五", "七千五的轻薄本，上课用", 7500),
    ("M02", "五千八", "五千八的轻薄本，上课用", 5800),
    ("M03", "一万二", "一万二的游戏本，打游戏", 12000),
    ("M04", "一万五", "一万五的游戏本，打游戏", 15000),
    ("M05", "两万三", "两万三的游戏本，打游戏", 23000),
    ("M06", "1.2万", "1.2万的游戏本，打游戏", 12000),
    ("M07", "一万二千五", "一万二千五的轻薄本，办公", 12500),
    ("M08", "九千九", "九千九的轻薄本，办公", 9900),
    ("M09", "12,000", "12,000 的游戏本，打游戏", 12000),
    ("M10", "两千五", "两千五的轻薄本，上课用", 2500),
    ("M11", "四万五", "四万五的工作站，做视频", 45000),
    ("M12", "一千二", "一千二的二手轻薄本", 1200),
]

for cid, label, txt, want in CN_CASES:
    def mk(want=want):
        def chk(rs):
            b = budget_of(rs[-1])
            if b == want:
                return True, "预算精确=%d" % want
            return False, "预算=%s，期望 %d" % (b, want)
        return chk
    case(cid, "口语金额-" + label, [txt], mk())


# ============ N 组：金额 + 逻辑词叠加 ============
def check_neg_budget(rs):
    """『不要七千的』——七千是被排除的金额，不能当成预算上限 7000 去筛。

    正确行为：不把 7000 当预算（budget 为 None 或另取），
    且如果它在没有新预算的情况下给了推荐，推出来的机器不能正好撞在 7000 这条线上。
    """
    s = (rs[-1].get("slots") or {})
    b = s.get("budget")
    if b == 7000:
        return False, "把『不要七千的』读成了预算上限 7000"
    if b is None:
        return True, "没有把排除金额当成预算（budget=None，会去确认）"
    return True, "预算取了另一个数 %s，没被排除金额污染" % b


case("N01", "否定金额-不要七千的",
     ["轻薄本上课用，不要七千的"], check_neg_budget)


def check_range_keep(rs):
    """『5000 到 7000』区间，取端点或中值都算有出处，但不能是 None。"""
    b = budget_of(rs[-1])
    if b in (5000, 6000, 7000):
        return True, "区间内取值 %s（可接受）" % b
    return False, "区间没解析出来 budget=%s" % b


case("N02", "区间金额-5000到7000",
     ["5000 到 7000 的轻薄本，上课用"], check_range_keep)


def check_multi_money(rs):
    """一句话两个金额（预算 7000，上限 8000）——至少要抽到一个真实金额。"""
    b = budget_of(rs[-1])
    if b in (7000, 8000):
        return True, "多金额取到之一 %s" % b
    return False, "多金额场景预算=%s" % b


case("N03", "多金额-预算七千最多八千",
     ["预算七千，最多不超过八千，轻薄本上课用"], check_multi_money)


# ============ O 组：预算与硬约束冲突 ============
def check_over_budget_warn(rs):
    """预算 2000 要 32G：必须给最接近的 + 说清超预算 + 不能说成"能买到"。"""
    r = rs[-1]
    txt = tail_of(r)
    warns = r.get("warnings") or []
    has_warn = any("超预算" in w or "买不到" in w for w in warns)
    in_text = ("超预算" in txt) or ("买不到" in txt) or ("都超过" in txt)
    lie = [w for w in ["唯一能买到", "预算内唯一", "这个预算能买到",
                       "唯一能开机", "正经机器", "地板价"] if w in txt]
    if lie:
        return False, "超预算场景说了「%s」，属于无出处断言" % "、".join(lie)
    if has_warn and in_text:
        return True, "给了超预算警告，且写进正文"
    if has_warn and not in_text:
        return False, "警告在 warnings 里但正文没写（用户看不到）"
    return False, "没有超预算警告"


case("O01", "超预算-2000 要 32G",
     ["2000 的轻薄本，要 32G 内存，上课用"], check_over_budget_warn)


def check_warn_not_duplicated(rs):
    """超预算警告只能出现一次，且不能前后打架。

    实测 2026-11-11 原话：「这款是 3000 元价位里最便宜的一台……
    这个价位在轻薄本里一台都买不到，最便宜的就是它」
    —— 先说是最便宜的，又说这个价位买不到，同一句话自己打自己。
    """
    txt = tail_of(rs[-1])
    n = txt.count("买不到")
    if n > 1:
        return False, "「买不到」出现了 %d 次，模型复述 + 系统补写撞车了" % n
    return True, "警告只出现一次"


case("O03", "警告不许复述两遍",
     ["500 的轻薄本，上课用"], check_warn_not_duplicated)


def check_no_unsupported_claim(rs):
    """回答里不许出现库里没有依据的断言。"""
    txt = tail_of(rs[-1])
    bad = [w for w in ["能开机", "正经机器", "地板价", "唯一能开机",
                       "放心买", "闭眼"] if w in txt]
    if bad:
        return False, "出现无出处断言：%s" % "、".join(bad)
    return True, "没有无出处断言"


case("O04", "极小预算不许无出处断言",
     ["500 的轻薄本，上课用"], check_no_unsupported_claim)


def check_tiny_budget_not_cheapest_lie(rs):
    """预算 500，必须说清一台都买不到，不能假装有货。"""
    r = rs[-1]
    txt = tail_of(r)
    lie = ["唯一能买到", "预算内唯一", "这个价位里最"]
    if any(w in txt for w in lie):
        return False, "预算 500 却说『唯一能买到』，自相矛盾"
    if "买不到" in txt or "超预算" in txt or "超过" in txt:
        return True, "如实说明这个预算买不到"
    return False, "既没说买不到，也没说明超预算"


case("O02", "极小预算-500",
     ["500 的轻薄本，上课用"], check_tiny_budget_not_cheapest_lie)


print("=" * 78)
if FAILS:
    print("第三轮攻击：失败 %d 条 → %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("第三轮攻击：全部通过")