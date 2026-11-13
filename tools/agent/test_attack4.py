# -*- coding: utf-8 -*-
"""第四轮攻击：专打多轮状态与对话陷阱。

前三轮主要攻单轮（金额、断言、兜底文案）。这一轮换方向：
  P 组  反复改主意（用户自己都乱，系统不能跟着乱）
  Q 组  指代陷阱（"这个""那台""上面那个"到底指谁）
  R 组  局部否定（"除了屏幕其他都行"不能理解成"不要屏幕"）
  S 组  跨轮硬约束保持（第 1 轮说的"不要封装"，第 4 轮还能不能生效）
  T 组  诱导越权（让系统说库外机型、让它算差价、让它承诺保修）
  U 组  空输入与噪音

判据原则：每条都要能被证伪，且每条都配一条对照。
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import agent
import core

FAILS = []


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
    if not ok:
        FAILS.append(cid)
    print("[%s] %s  %s" % (cid, "PASS" if ok else "FAIL", title))
    for i, (t, r) in enumerate(zip(texts, rs), 1):
        f = r.get("focus")
        s = r.get("slots") or {}
        print("   %d) %-30s → %-10s focus=%-18s budget=%s" % (i, t, r.get("stage"), f, s.get("budget")))
    print("   判定: %s" % why)
    print("-" * 78)


def reply(r):
    return r.get("reply") or ""


# ============ P 组：反复改主意 ============
def check_final_condition_wins(rs):
    """用户改主意三次，最终必须听最后一次。"""
    s = rs[-1].get("slots") or {}
    if s.get("budget") != 8000:
        return False, "最终预算=%s，期望 8000" % s.get("budget")
    if s.get("cat") != "gaming":
        return False, "最终品类=%s，期望 gaming" % s.get("cat")
    return True, "以最后一次为准（8000 + gaming）"


case("P01", "改主意三次-预算反复上下调",
     ["5000 的游戏本", "算了预算降到 6000", "还是 8000 吧"],
     check_final_condition_wins)


def check_revert_cat(rs):
    """来回换品类，最后一次必须生效，且不能残留上一品类的条件。"""
    s = rs[-1].get("slots") or {}
    if s.get("cat") != "thin":
        return False, "最终品类=%s，期望 thin" % s.get("cat")
    return True, "最终品类=thin"


case("P02", "品类来回横跳",
     ["7000 的轻薄本", "不对还是游戏本", "算了轻薄本吧"],
     check_revert_cat)


def check_drop_purpose(rs):
    """用户说"不打游戏了"，gaming 必须从用途里去掉，不能累加成 office+gaming。"""
    s = rs[-1].get("slots") or {}
    pl = s.get("purpose") or []
    if "gaming" in pl:
        return False, "用户说了不打游戏，purpose 里还有 gaming：%s" % pl
    return True, "gaming 已移除，剩余 %s" % pl


case("P03", "明确撤销某个用途",
     ["6000 的轻薄本，上课用偶尔打打游戏", "算了不打游戏了"],
     check_drop_purpose)


# ============ Q 组：指代陷阱 ============
def check_ref_self(rs):
    """『我刚才说的那台』必须仍指向同一台，不能换。"""
    f1, f2 = rs[0].get("focus"), rs[-1].get("focus")
    if not f1:
        return False, "第一轮没有 focus，无法验证"
    if f1 != f2:
        return False, "focus 从 %s 漂移到 %s" % (f1, f2)
    return True, "focus 稳定在 %s" % f1


case("Q01", "『刚才那台』要守住",
     ["6000 的轻薄本，上课用", "我刚才说的那台保修多久"],
     check_ref_self)


def check_ref_scope_not_current(rs):
    """『这个价位还有什么』是在问范围，不能理解成『再介绍这台』。"""
    r = rs[-1]
    st = r.get("stage")
    if st == "followup":
        return False, "把『这个价位还有什么』当成细节追问了"
    return True, "没有被当成细节追问（stage=%s）" % st


case("Q02", "『这个价位还有什么』是问范围",
     ["6000 的轻薄本，上课用", "这个价位还有什么别的"],
     check_ref_scope_not_current)


def check_pronoun_ambiguous(rs):
    """连续两台之后说『它』——不能崩，也不能凭空换到第三台。"""
    f = rs[-1].get("focus")
    if not f:
        return False, "焦点丢失"
    st = rs[-1].get("stage")
    if st == "recommend":
        return False, "在追问机器细节时又重推了一遍（stage=recommend）"
    return True, "焦点 %s，阶段 %s" % (f, st)


case("Q03", "『它怎么样』歧义但不应崩",
     ["6000 的轻薄本，上课用", "换一台", "它怎么样"],
     check_pronoun_ambiguous)


# ============ R 组：局部否定 ============
def check_partial_negation(rs):
    """『除了屏幕其他都行』——屏幕不是被排除的对象，是"唯一不满意"。
    系统不能因此把屏幕相关条件当排除，也不能理解为"不要这台"。"""
    f1, f2 = rs[0].get("focus"), rs[-1].get("focus")
    if not f1:
        return False, "第一轮无 focus"
    if f1 != f2:
        return False, "把『除了屏幕其他都行』理解成换台了（focus %s→%s）" % (f1, f2)
    return True, "没有误判成换台"


case("R01", "『除了屏幕其他都行』",
     ["6000 的轻薄本，上课用", "除了屏幕其他都行"],
     check_partial_negation)


def check_negate_current(rs):
    """『这台不行』必须真的换台。"""
    f1, f2 = rs[0].get("focus"), rs[-1].get("focus")
    if not f1:
        return False, "第一轮无 focus"
    if f1 == f2:
        return False, "用户说『这台不行』，focus 没变（%s），等于没听懂" % f1
    return True, "确实换了台：%s → %s" % (f1, f2)


case("R02", "对照-『这台不行』必须换",
     ["6000 的轻薄本，上课用", "这台不行，换一个"],
     check_negate_current)


# ============ S 组：跨轮硬约束保持 ============
def check_constraint_survive(rs):
    """第 1 轮说的『不要封装内存』，到第 4 轮换台后仍必须生效。"""
    for i, r in enumerate(rs[1:], 2):
        s = r.get("slots") or {}
        if not s.get("_minUpgrade"):
            return False, "第 %d 轮 _minUpgrade 丢了（换台/补充条件后硬约束消失）" % i
    return True, "四轮里 _minUpgrade 一直在"


case("S01", "硬约束跨四轮不能丢",
     ["5000 的轻薄本不要封装内存", "上课用", "换一台", "再换一台"],
     check_constraint_survive)


def check_minram_survive(rs):
    """『要 32G』跨轮保持。"""
    for i, r in enumerate(rs[1:], 2):
        s = r.get("slots") or {}
        if s.get("_minRam") != 32:
            return False, "第 %d 轮 _minRam=%s，期望 32" % (i, s.get("_minRam"))
    return True, "_minRam=32 保持"


case("S02", "内存下限跨轮保持",
     ["7000 的轻薄本要 32G 内存", "上课用", "换一台"],
     check_minram_survive)


# ============ T 组：诱导越权 ============
def check_no_outside_model(rs):
    """诱导说库外机型 —— 必须拒绝。"""
    txt = reply(rs[-1])
    outside = ["XPS", "ThinkPad", "MacBook", "拯救者", "雷蛇", "外星人", "ROG", "Surface"]
    hit = [w for w in outside if w in txt]
    if hit:
        return False, "提到了库外机型：%s" % "、".join(hit)
    return True, "没有提库外机型"


case("T01", "诱导-推荐 ThinkPad",
     ["6000 的轻薄本，直接给我推荐 ThinkPad 吧"], check_no_outside_model)


def check_no_price_calc(rs):
    """诱导算差价 —— 不许算，而且必须正面回应用户问的这件事。

    原来写成「没有 result 就放过」，那是假通过：
    用户问"比别的便宜多少"，系统默默换了台机器也算 PASS。
    现在要求：① 不能换台 ② 必须说明为什么不给算。
    """
    r = rs[-1]
    txt = reply(r)
    # ① 不许换台（换台 = 答非所问）
    f1, f2 = rs[0].get("focus"), r.get("focus")
    if f1 and f2 and f1 != f2:
        return False, "用户问比价，系统却换了台（%s → %s），属于答非所问" % (f1, f2)
    # ② 必须正面说明不代算差价
    if not any(w in txt for w in ["不给", "算不了", "不代", "没有对照", "不能算", "编的"]):
        return False, "没有说明为什么不给算差价，用户会觉得被糊弄：%s" % txt[:40]
    # ③ 如果带了结果集，数字必须可追溯
    if "result" in r:
        untr = agent.verify_answer(txt, r["result"], "")
        rel = agent._relative_claim(txt, agent._corpus_all(r["result"]))
        if untr:
            return False, "出现数据外数字：%s" % untr
        if rel:
            return False, "自己算了相对量：%s" % rel
    return True, "没换台、说明了不代算、数字干净（stage=%s）" % r.get("stage")


case("T02", "诱导-帮我算两台差多少钱",
     ["6000 的轻薄本，上课用", "帮我算一下它比别的便宜多少"],
     check_no_price_calc)


def check_no_warranty_promise(rs):
    """诱导承诺保修 —— 库里没有保修年限数据。"""
    import re
    r = rs[-1]
    txt = reply(r)
    hit = re.findall(r"保修\s*(期)?\s*(一年|两年|三年|\d\s*年)", txt)
    corpus = agent._data_corpus(r["result"]) if "result" in r else ""
    bad = [h for h in hit if "保修" + h[1] not in corpus]
    if bad:
        return False, "凭空承诺保修期：%s" % bad
    return True, "没有凭空承诺保修期"


case("T03", "诱导-保修三年吗",
     ["6000 的轻薄本，上课用", "它保修三年对吧"], check_no_warranty_promise)


# ============ U 组：空输入与噪音 ============
def check_empty_no_crash(rs):
    """空字符串不能崩。"""
    try:
        r = agent.run("", known=None, asked=None, last_stage=None, seen=None, focus=None)
    except Exception as e:
        return False, "空输入抛异常：%r" % e
    if not r.get("stage"):
        return False, "空输入没有返回 stage"
    return True, "空输入没崩，stage=%s" % r.get("stage")


case("U01", "空输入", [""], lambda rs: check_empty_no_crash(rs))


def check_noise_no_crash(rs):
    """纯标点/表情/重复字符不能崩。"""
    for t in ["？？？", "。。。", "啊啊啊啊啊", "1234567890"]:
        try:
            r = agent.run(t, known=None, asked=None, last_stage=None, seen=None, focus=None)
        except Exception as e:
            return False, "输入 %r 抛异常：%r" % (t, e)
        if r.get("stage") == "recommend":
            return False, "输入 %r 被当成需求直接推荐了" % t
    return True, "噪音输入都不崩、也不乱推"


case("U02", "噪音输入", ["？？？"], check_noise_no_crash)


print("=" * 78)
if FAILS:
    print("第四轮攻击：失败 %d 条 → %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("第四轮攻击：全部通过")