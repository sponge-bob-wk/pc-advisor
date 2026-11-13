# -*- coding: utf-8 -*-
"""第五轮攻击：专打刚修好的状态机。

第四轮修了 known / seen / switch 判断 / _cheapest，这些都是新代码，最脆弱。
这一轮反过来咬自己的修复：
  V 组  known 兜底会不会把"已经放弃的条件"也一起继承回来
  W 组  seen 会不会累加到把用户想看的机器也排除掉
  X 组  _is_switch_request 收紧后，真的换台会不会不生效
  Y 组  改品类时硬约束该怎么丢
  Z 组  长时间对话（8 轮以上）会不会状态漂移

原则：每个修复都要能证明"修过头了没有"。
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import agent

FAILS = []


def run_chain(texts):
    known = asked = last = seen = focus = None
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
        s = r.get("slots") or {}
        print("   %d) %-30s → %-9s focus=%-19s bud=%-6s" %
              (i, t, r.get("stage"), r.get("focus"), s.get("budget")))
    print("   判定: %s" % why)
    print("-" * 78)


def reply(r):
    return r.get("reply") or ""


# ============ V 组：known 兜底有没有修过头 ============
def check_budget_not_resurrect(rs):
    """用户明确说"预算不重要了随便"，之前给的 6000 不能被 known 兜底又拉回来。

    known 兜底的规则是"空就补"，但用户主动撤销预算时，补回来就是没听懂。
    """
    s = rs[-1].get("slots") or {}
    if s.get("budget") == 6000:
        return False, "用户说了预算无所谓，6000 又被兜底拉回来了"
    return True, "撤销的预算没有被复活（budget=%s, cheapest=%s）" % (
        s.get("budget"), s.get("_cheapest"))


case("V01", "撤销预算不许被兜底复活",
     ["6000 的轻薄本，上课用", "预算无所谓了，越便宜越好"],
     check_budget_not_resurrect)


def check_cat_change_clears_hard(rs):
    """改品类时可以丢旧硬约束，但不能把新品类也搞崩。"""
    s = rs[-1].get("slots") or {}
    if s.get("cat") != "gaming":
        return False, "最终品类=%s，期望 gaming" % s.get("cat")
    return True, "品类切到 gaming，_minUpgrade=%s（允许丢）" % s.get("_minUpgrade")


case("V02", "改品类时硬约束允许丢",
     ["5000 的轻薄本不要封装内存", "算了改游戏本吧，打游戏"],
     check_cat_change_clears_hard)


# ============ W 组：seen 会不会修过头 ============
def check_seen_not_overgrow(rs):
    """用户说"我要看回上一台"，seen 不能把他挡在外面。"""
    r = rs[-1]
    st = r.get("stage")
    if st == "exhausted":
        return False, "用户只是想回看，被判成'都看过了'（exhausted）"
    if not r.get("focus"):
        return False, "回看需求没有落点，focus 为空"
    return True, "能回到某台（stage=%s, focus=%s）" % (st, r.get("focus"))


case("W01", "seen 不许把回看挡死",
     ["6000 的轻薄本，上课用", "换一台", "换一台", "换一台"],
     check_seen_not_overgrow)


def check_seen_persist_across_conditions(rs):
    """换了条件（预算提高）后，seen 保留是合理的，但不能因此无货可推。"""
    r = rs[-1]
    if r.get("stage") == "exhausted":
        return False, "预算放宽后仍判 exhausted，seen 卡太死"
    if not r.get("focus"):
        return False, "预算放宽后没推出来（focus 为空）"
    return True, "放宽预算后仍能推（focus=%s）" % r.get("focus")


case("W02", "放宽预算后 seen 不能卡死",
     ["5000 的轻薄本，上课用", "换一台", "算了预算提高到 9000"],
     check_seen_persist_across_conditions)


# ============ X 组：换台判断收紧后，真换台还得生效 ============
SWITCH_CASES = [
    ("X01", "换一台", "换一台"),
    ("X02", "换个别的", "换个别的吧"),
    ("X03", "还有别的吗", "还有别的吗"),
    ("X04", "这台不想要了", "这台不想要了"),
    ("X05", "太贵了便宜点", "太贵了，便宜点的"),
    ("X06", "就这几个？没有别的？", "就这几个？没有别的？"),
]


def check_really_switches(rs):
    f1, f2 = rs[0].get("focus"), rs[-1].get("focus")
    if not f1:
        return False, "第一轮无 focus"
    if f1 == f2:
        return False, "说了换台，focus 没变（%s），收紧过头了" % f1
    return True, "确实换了：%s → %s" % (f1, f2)


for cid, label, said in SWITCH_CASES:
    case(cid, "真换台-" + label,
         ["6000 的轻薄本，上课用", said], check_really_switches)


# ============ X 组对照：比价说法不能被误判成换台 ============
COMPARE_CASES = [
    ("X07", "比别的便宜多少", "它比别的便宜多少"),
    ("X08", "跟其他比怎么样", "它跟其他机型比怎么样"),
    ("X09", "别的品牌是不是都更贵", "别的品牌是不是都更贵"),
]


def check_compare_keeps_focus(rs):
    f1, f2 = rs[0].get("focus"), rs[-1].get("focus")
    if f1 and f2 and f1 != f2:
        return False, "比价说法被当成换台了（%s → %s）" % (f1, f2)
    return True, "比价时守住当前这台"


for cid, label, said in COMPARE_CASES:
    case(cid, "对照-比价不算换台-" + label,
         ["6000 的轻薄本，上课用", said], check_compare_keeps_focus)


# ============ Y 组：长对话状态漂移 ============
def check_long_chain_stable(rs):
    """8 轮之后，最初的硬约束和最终预算都还得对。"""
    s = rs[-1].get("slots") or {}
    if s.get("budget") != 7000:
        return False, "第 8 轮预算=%s，期望 7000（最后说的）" % s.get("budget")
    if s.get("cat") != "thin":
        return False, "第 8 轮品类=%s，期望 thin" % s.get("cat")
    if not s.get("_minUpgrade"):
        return False, "第 8 轮 _minUpgrade 丢了"
    return True, "8 轮后条件仍一致（budget=7000, thin, 不封装）"


case("Y01", "8 轮长对话不漂移",
     ["5000 的轻薄本不要封装内存",
      "上课用",
      "预算提高到 6000",
      "换一台",
      "换一台",
      "算了 7000 吧",
      "再换一台",
      "就这个预算，别的条件不变"],
     check_long_chain_stable)


def check_no_repeat_in_chain(rs):
    """连续换 4 台，不能出现重复机型。"""
    focuses = [r.get("focus") for r in rs if r.get("focus")]
    dup = [f for f in set(focuses) if focuses.count(f) > 1]
    if dup:
        return False, "同一台被推了多次：%s" % dup
    return True, "连续换台无重复（共 %d 台）" % len(set(focuses))


case("Y02", "连续换台不许重复",
     ["6000 的轻薄本，上课用", "换一台", "换一台", "换一台", "换一台"],
     check_no_repeat_in_chain)


print("=" * 78)
if FAILS:
    print("第五轮攻击：失败 %d 条 → %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("第五轮攻击：全部通过")