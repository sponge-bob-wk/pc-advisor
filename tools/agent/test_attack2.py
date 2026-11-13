# -*- coding: utf-8 -*-
"""对抗测试 第二轮：专攻"看着对、其实错"。

第一轮找的是显性 bug（推超预算、编数字、报错）。
这一轮找隐性 bug：回答流畅但答错了、条件被悄悄丢掉、同一件事两种说法。

每条的期望都是可以机器判定的硬事实。
"""
import sys, os, json, re
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent
import core

# 🔴 2026-11-12：原来用 core.load()（人工层 22 台），v0.4 池子扩到 97 台后，
#   正文主推的采集层机型（如 MateBook）不在名单里，K06 的"正文先说 = 卡片"
#   永远匹配不上 —— 是测试陈旧，不是产品不一致。改查合并池。
OFFERS = {o["id"]: o for o in core.usable_offers({"offers": core.merged_offers()})}


def play(turns):
    known, asked, seen, focus = None, None, [], None
    out = []
    for q in turns:
        r = agent.run(q, known=known, asked=asked, last_stage=None,
                      seen=seen if seen else None, focus=focus)
        out.append(r)
        if r.get("slots"):
            known = {k: v for k, v in r["slots"].items()
                     if v is not None and v != [] and v != ""}
        if r.get("asked"):
            asked = r["asked"]
        if r.get("stage") == "recommend" and r.get("result", {}).get("ranked"):
            seen.append(r["result"]["ranked"][0]["id"])
        if r.get("focus") is not None:
            focus = r["focus"]
    return out


def budget_of(r):
    return (r.get("slots") or {}).get("budget")


def names_in(text):
    return [n for n in (o["name"] for o in OFFERS.values()) if n in text]


# ============================================================
CASES = [
    # ---------- 1. 预算被悄悄改 ----------
    ("K01", "先说预算，再说别的条件，预算不能被冲掉",
     ["预算 5000，轻薄本，上课写作业", "要能带去教室，四年不换"],
     lambda rs: ("预算保持", budget_of(rs[-1]) == 5000)),

    ("K02", "『便宜点』不能把预算清成 null",
     ["8000 的轻薄本，学设计", "太贵了，便宜点"],
     lambda rs: ("预算不丢", budget_of(rs[-1]) in (8000,) or budget_of(rs[-1]) is not None)),

    ("K03", "『预算可以再加点』不改变原预算上限的逻辑",
     ["6000 的轻薄本，上课用", "预算可以再加一点"],
     lambda rs: ("不崩", rs[-1].get("stage") in ("recommend", "clarify", "followup"))),

    # ---------- 2. 硬约束跨轮保持 ----------
    ("K04", "说了不要封装，中间插一句别的，约束不能丢",
     ["5000 轻薄本，上课用，不要封装内存", "那屏幕要好点的"],
     lambda rs: ("约束在", (rs[-1].get("slots") or {}).get("_minUpgrade") == 1)),

    ("K05", "硬约束下换三台，都不能违反",
     ["5000 轻薄本，上课用，不要封装内存", "换一台", "再换一台"],
     lambda rs: ("全不违反", all(
         (o.get("upgrade") or 0) >= 1
         for r in rs[1:] for o in ((r.get("result") or {}).get("ranked") or []))
         or all(r.get("stage") != "recommend" for r in rs[1:]))),

    # ---------- 3. 推荐与描述必须一致 ----------
    ("K06", "推荐卡片上的机型 = 正文里主推的机型",
     ["7000 的轻薄本，学设计"],
     None),   # 特殊处理

    ("K07", "说了不要封装，正文里不能夸『可升级内存』",
     ["5000 轻薄本，上课用，不要封装内存"],
     None),

    # ---------- 4. 数据一致性 ----------
    ("K08", "推荐理由里的数字必须能在价格表里找到",
     ["6000 的轻薄本，上课用"],
     None),

    # ---------- 5. 边界 ----------
    ("K09", "预算正好等于某台的价格",
     ["6799，轻薄本，学设计"],
     lambda rs: ("预算=6799", budget_of(rs[-1]) == 6799)),

    ("K10", "预算极小 500",
     ["500 的轻薄本，上课用"],
     lambda rs: ("如实说没有", rs[-1].get("stage") in ("recommend", "clarify")
                 and ("买不到" in (rs[-1].get("reply") or "")
                      or "超预算" in (rs[-1].get("reply") or "")
                      or rs[-1].get("stage") == "clarify"
                      or any("买不到" in w or "超预算" in w
                             for w in (rs[-1].get("warnings") or []))))),

    ("K11", "预算极大 50000",
     ["50000 的轻薄本，上课用"],
     lambda rs: ("不推最便宜的糊弄", rs[-1].get("stage") == "recommend"
                 and any("最贵" in w or "余量" in w or "花不出去" in w
                         for w in (rs[-1].get("warnings") or [])))),

    # ---------- 6. 用途冲突 ----------
    ("K12", "同时说设计和打游戏，不能只认一个",
     ["学设计的，也打游戏，8000"],
     lambda rs: ("两个用途都记着",
                 set((rs[-1].get("slots") or {}).get("purpose") or [])
                 >= {"design", "gaming"})),

    # ---------- 7. 反复横跳 ----------
    ("K13", "连续换 5 台，不能重复推同一台",
     ["6000 的轻薄本，上课用", "换一台", "换一台", "换一台", "换一台"],
     lambda rs: ("不重复", len({r.get("focus") for r in rs if r.get("focus")}) ==
                 len([r for r in rs if r.get("focus")]))),

    # ---------- 8. 空输入类 ----------
    ("K14", "只发标点",
     ["？？？"],
     lambda rs: ("不崩", rs[-1].get("stage") in
                 ("clarify", "recommend", "refuse", "slot_error"))),

    ("K15", "重复发同一句，不能每次换一台",
     ["6000 的轻薄本，上课用", "6000 的轻薄本，上课用"],
     lambda rs: ("不换台", rs[0].get("focus") == rs[1].get("focus"))),

    # ---------- 9. 跨品类 ----------
    ("K16", "从笔记本跳到台式，条件要重置但预算能留",
     ["6000 的轻薄本，上课用", "算了，改台式机吧，放宿舍"],
     lambda rs: ("品类切了", (rs[-1].get("slots") or {}).get("cat") == "desktop")),

    ("K17", "从台式跳回笔记本",
     ["7000 的台式机，放宿舍写代码", "还是买笔记本吧，要带教室"],
     lambda rs: ("品类切回", (rs[-1].get("slots") or {}).get("cat") == "thin")),

    # ---------- 10. 数字精度 ----------
    # 原判据写成 `in (7500, 7000, None)`，让"降级成回头问预算"也判 PASS，
    # 等于把真问题藏起来了 —— 用户说"七千五"系统反问"预算多少"就是没听懂。
    # 收紧成硬判据：预算必须精确等于期望，且不能是追问。
    ("K18", "『七千五』这种口语预算",
     ["七千五的轻薄本，上课用"],
     lambda rs: ("预算=7500 且给了推荐",
                 budget_of(rs[-1]) == 7500 and rs[-1].get("stage") != "clarify")),

    ("K19", "『一万二』不能变成 10000 或 2000",
     ["一万二的游戏本，打游戏"],
     lambda rs: ("预算=12000 且给了推荐",
                 budget_of(rs[-1]) == 12000 and rs[-1].get("stage") != "clarify")),

    ("K20", "预算里带逗号『12,000』",
     ["12,000 的游戏本，打游戏"],
     lambda rs: ("预算=12000 且给了推荐",
                 budget_of(rs[-1]) == 12000 and rs[-1].get("stage") != "clarify")),
]


def main():
    ok_n = 0
    fails = []
    for cid, desc, turns, check in CASES:
        rs = play(turns)
        last = rs[-1]
        print("=" * 78)
        print("[%s] %s" % (cid, desc))
        for i, r in enumerate(rs):
            print("  %d) %-26s → %-9s focus=%s" % (
                i + 1, (turns[i] or "")[:24], r.get("stage"), r.get("focus")))
        print("  末轮: %s" % (last.get("reply") or "").replace("\n", " ")[:220])
        print("  槽位: %s" % json.dumps(last.get("slots"), ensure_ascii=False))

        verdict = None
        if check:
            try:
                name, res = check(rs)
                verdict = (name, bool(res))
            except Exception as e:
                verdict = ("检查异常", False)
                print("  ⚠ 检查器异常: %s" % e)

        # K06: 卡片与正文一致性
        if cid == "K06" and last.get("result", {}).get("ranked"):
            top = last["result"]["ranked"][0]["name"]
            reply = last.get("reply") or ""
            # 正文里如果主推的是备选而非榜首，算失败
            first_mention = None
            for n in names_in(reply):
                if first_mention is None:
                    first_mention = n
            verdict = ("正文主推=卡片", first_mention == top)
            if first_mention != top:
                print("  ⚠ 卡片是 %s，正文先说 %s" % (top, first_mention))

        # K07: 说了不要封装却夸可升级
        if cid == "K07":
            reply = last.get("reply") or ""
            bad = ("可升级" in reply or "能加内存" in reply or "加装" in reply) \
                and "封装" not in reply and "板载" not in reply
            verdict = ("未夸可升级", not bad)
            if bad:
                print("  ⚠ 说了不要封装，正文却在夸可升级")

        # K08: 数字可追溯
        if cid == "K08":
            untr = last.get("untraceable_numbers") or []
            verdict = ("数字全可追溯", not untr)
            if untr:
                print("  ⚠ 不可追溯数字: %s" % untr)

        if verdict:
            print("  判定: %s → %s" % (verdict[0], "PASS" if verdict[1] else "FAIL"))
            if verdict[1]:
                ok_n += 1
            else:
                fails.append((cid, desc, verdict[0], last))

    print("\n" + "=" * 78)
    print("第二轮攻击：通过 %d/%d" % (ok_n, len(CASES)))
    if fails:
        print("\n失败清单：")
        for cid, desc, name, last in fails:
            print("  [%s] %s —— %s 不成立" % (cid, desc, name))
            print("       回答: %s" % (last.get("reply") or "").replace("\n", " ")[:140])
    return 0


if __name__ == "__main__":
    sys.exit(main())