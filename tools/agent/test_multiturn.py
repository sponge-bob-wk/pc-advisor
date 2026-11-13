# -*- coding: utf-8 -*-
"""多轮语义回归。锁定 2026-11-11 用户实测踩到的四类问题。

每条用例都是一段真实对话，只断言三件事：
  ① 该换需求的时候换了（不串味）
  ② 该守住当前那台的时候守住了（不自作主张换品类）
  ③ 槽位不会莫名丢失（不死循环）

判据要能分辨：故意错的输入必须被判错。见 test_neg.py。
"""
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent


def play(turns):
    """按前端的方式跑一段对话，返回每轮结果。"""
    known, asked, seen, focus = None, None, None, None
    out = []
    for q in turns:
        r = agent.run(q, known=known, asked=asked, last_stage=None,
                      seen=seen, focus=focus)
        out.append(r)
        known = r.get("slots") or known
        asked = r.get("asked") or asked
        if r.get("stage") == "recommend" and r.get("result", {}).get("ranked"):
            seen = (seen or []) + [r["result"]["ranked"][0]["id"]]
        focus = r.get("focus")
    return out


CASES = [
    {
        "id": "M01",
        "name": "问『这台能不能打游戏』不能变成换游戏本",
        "turns": ["学设计的，五千，要用四年，平时背去教室",
                  "打游戏这个合适吗，偶尔打打游戏"],
        "want": [
            {"stage": "recommend"},
            {"stage": "fit", "same_machine": True, "verdict": "mismatch"},
        ],
    },
    {
        "id": "M02",
        "name": "『我说的就是刚才那台』要认得出指的是哪台",
        "turns": ["学设计的，五千，要用四年，平时背去教室",
                  "打游戏这个合适吗，偶尔打打游戏",
                  "我说的就是刚刚你给我推荐的那一台啊"],
        "want": [
            {"stage": "recommend"},
            {"stage": "fit"},
            {"same_machine": True, "cat": "thin"},
        ],
    },
    {
        "id": "M03",
        "name": "『预算提高到7000』要真的改预算，不能被追问吞掉",
        "turns": ["学设计的，五千，要用四年，平时背去教室",
                  "预算提高到7000，屏幕素质我有要求"],
        "want": [
            {"stage": "recommend", "budget": 5000},
            {"budget": 7000},
        ],
    },
    {
        "id": "M04",
        "name": "换一台不能丢条件、不能重问品类（死循环）",
        "turns": ["5000 的轻薄本，就上课写作业", "还有别的品牌吗"],
        "want": [
            {"stage": "recommend"},
            {"stage_in": ["recommend", "exhausted"], "budget": 5000, "cat": "thin"},
        ],
    },
    {
        "id": "M05",
        "name": "『不要封装内存』是硬约束，换台也不能推封装机型",
        "turns": ["5000 的轻薄本，就上课写作业", "我的要求是不要封装内存的",
                  "再换一台"],
        "want": [
            {"stage": "recommend"},
            {"stage": "recommend", "min_upgrade": 1},
            {"min_upgrade": 1},
        ],
    },
    {
        "id": "M06",
        "name": "『这个价位还有什么』是在说范围，不是指向当前那台",
        "turns": ["学设计的，五千，要用四年，平时背去教室",
                  "这个价位还有什么别的"],
        "want": [
            {"stage": "recommend"},
            {"stage_in": ["recommend", "followup", "exhausted"]},
        ],
    },
    {
        "id": "M07",
        "name": "补充用途是叠加，不是覆盖（学设计 + 偶尔游戏）",
        "turns": ["学设计的，五千，要用四年，平时背去教室",
                  "我偶尔也打打游戏，有什么推荐"],
        "want": [
            {"stage": "recommend"},
            {"purpose_has": ["design"]},
        ],
    },
    {
        "id": "M08",
        "name": "游戏本换个方问（能带进教室吗）不能变成换轻薄本",
        "turns": ["8000 的游戏本，主要打游戏", "能带进教室吗"],
        "want": [
            {"stage": "recommend"},
            {"cat": "gaming"},
        ],
    },
    {
        "id": "M09",        # 2026-11-12 用户当面反馈：前面已经定了游戏本、也说了限电 500W，
        # 补一句「要带去教室」→ 模型按"要带着走 ⇒ 轻薄本"把 cat 改回 thin，
        # 首位从暗影精灵 11 变成 MateBook 14（核显），打游戏这条直接没了。
        # M08 只覆盖了问句「能带进教室吗」，陈述句没覆盖 —— 这就是漂移点。
        "name": "补一句「要带去教室」不能把游戏本换成轻薄本（陈述句）",
        "turns": ["预算 7000，学设计，还想打游戏", "限电 500W", "要带去教室"],
        "want": [
            {"stage": "recommend", "cat": "gaming"},
            {"cat": "gaming"},
            {"cat": "gaming"},
        ],
    },
    {
        "id": "M10",        # 2026-11-12 用户实测：「其余推荐的给我列举出信息我看看」
        # → 系统又走了一遍推荐，把首位那台整段重讲，另外两台一个字没提。
        # 根因不是模型：代码里没有"列清单"这条路径，只有"重新推荐"。
        "name": "「其余推荐列出来」要给清单，不许把首位再讲一遍",
        "turns": ["我是物联网工程专业大一新生，预算7000买一台笔记本",
                  "其余推荐的给我列举出信息我看看"],
        "want": [
            {"stage": "recommend"},
            {"stage": "followup", "reply_has": ["无畏16", "小新 Pro 16"]},
        ],
    },
    {
        "id": "M11",        # 2026-11-12 用户实测：「我只要测试就会出问题」——
        # 最典型的一种是：随便说一句没营养的话，系统把首位的卡片再刷一遍。
        # 兜底规则：**同一台不许连着推两遍**。这条不猜用户想干嘛，只禁止这件事。
        "name": "没条件变化时不许把同一台再推一遍（要反问）",
        "turns": ["预算 7000，学设计，要带去教室", "你说呢"],
        "want": [
            {"stage": "recommend"},
            {"stage": "followup", "reply_has": ["条件没改"]},
        ],
    },
    {
        "id": "M12",
        # 2026-11-12 死循环（用户当场抓到）：兜底反问里给了「① 把它和另外两台比」，
        # 但代码里没有对比能力 —— 用户点一次、系统问一次，连续 4 轮同一句话。
        # 教训：**给了选项就必须接得住**，接不住就别给。
        "name": "反问里给的选项必须能接住（不许同一句反复问）",
        "turns": ["计算机科学与技术专业，推荐一台", "笔记本", "预算 8000 左右",
                  "要带去教室", "把它和另外两台放一起比"],
        "want": [
            {"stage": "clarify"}, {"stage": "clarify"}, {"stage": "recommend"},
            # 「要带去教室」是新增的携带条件，条件变了就该按新条件重排（不是兜底）
            {"stage_in": ["recommend", "followup"]},
            {"stage": "compare", "reply_has": ["放一起看"]},
        ],
    },
]


def slot_of(r, k):
    return (r.get("slots") or {}).get(k)


def check_one(r, w):
    """返回失败原因列表。空表示通过。"""
    fails = []
    if "stage" in w and r.get("stage") != w["stage"]:
        fails.append("阶段=%s 期望=%s" % (r.get("stage"), w["stage"]))
    if "stage_in" in w and r.get("stage") not in w["stage_in"]:
        fails.append("阶段=%s 期望在%s" % (r.get("stage"), w["stage_in"]))
    if "budget" in w and slot_of(r, "budget") != w["budget"]:
        fails.append("预算=%s 期望=%s" % (slot_of(r, "budget"), w["budget"]))
    if "cat" in w and slot_of(r, "cat") != w["cat"]:
        fails.append("品类=%s 期望=%s" % (slot_of(r, "cat"), w["cat"]))
    if "min_upgrade" in w:
        got = slot_of(r, "_minUpgrade")
        if got != w["min_upgrade"]:
            fails.append("_minUpgrade=%s 期望=%s" % (got, w["min_upgrade"]))
    if "purpose_has" in w:
        got = slot_of(r, "purpose") or []
        for p in w["purpose_has"]:
            if p not in got:
                fails.append("purpose=%s 缺%s" % (got, p))
    if w.get("reply_has"):
        for x in w["reply_has"]:
            if x not in (r.get("reply") or ""):
                fails.append("回答里缺「%s」" % x)
    if w.get("verdict"):
        # 结论要靠数据判，不靠模型措辞
        focus = r.get("focus")
        o = agent.core.get_offer(focus) if focus else None
        if o:
            b = agent.core.brief(o, agent._asked_purpose("打游戏"))
            if b["fitVerdict"] != w["verdict"]:
                fails.append("匹配结论=%s 期望=%s" % (b["fitVerdict"], w["verdict"]))
    return fails


def check_same_machine(prev, cur):
    """当前这台有没有被换掉。"""
    if prev.get("focus") != cur.get("focus"):
        return ["焦点=%s→%s 被换了" % (prev.get("focus"), cur.get("focus"))]
    return []


def main():
    total = passed = 0
    rows = []
    for c in CASES:
        rs = play(c["turns"])
        fails = []
        # 🔴 死循环的通用判据：连续两轮回复**一字不差**，就是卡住了。
        #   不看意图、不看措辞，任何对话里都不该出现（2026-11-12 用户连问 4 轮同一句）。
        for i in range(1, len(rs)):
            a = (rs[i - 1].get("reply") or "").strip()
            b = (rs[i].get("reply") or "").strip()
            if a and a == b:
                fails.append("第 %d/%d 轮回复完全一样 —— 死循环" % (i, i + 1))
        for i, w in enumerate(c["want"]):
            r = rs[i] if i < len(rs) else {}
            fails += check_one(r, w)
            if w.get("same_machine") and i > 0:
                fails += check_same_machine(rs[i - 1], r)
        total += 1
        if not fails:
            passed += 1
        rows.append({"id": c["id"], "name": c["name"], "ok": not fails,
                     "fails": fails,
                     "stages": [x.get("stage") for x in rs],
                     "picks": [x.get("focusName") for x in rs]})
        print("[%s] %-4s %s" % (c["id"], "PASS" if not fails else "FAIL", c["name"]))
        if fails:
            for f in fails:
                print("        - " + f)
            print("        阶段链: %s" % " → ".join(rows[-1]["stages"]))
            print("        焦点链: %s" % " → ".join(str(x) for x in rows[-1]["picks"]))
    print("\n" + "=" * 62)
    print("多轮语义 通过 %d/%d = %.0f%%" % (passed, total, passed * 100.0 / total))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "multiturn_result.json"), "w", encoding="utf-8") as f:
        json.dump({"passed": passed, "total": total, "rows": rows},
                  f, ensure_ascii=False, indent=1)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())