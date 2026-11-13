# -*- coding: utf-8 -*-
"""字段卡（agent.answer_card）的结构测试（2026-11-13 新增）。

背景：用户要求「每次回答的数据是规范格式输出，而不是一句一句话」，
于是把回答主视图改成「一句话结论 + 固定字段行」。这个改动最大的风险是
**字段卡和正文说的不是一回事** —— 卡片上多一个数字、少一句短板，
用户看不出来，而它没有过出口审计。

所以这里守四条不变式（不预设措辞，只查结构与来源）：
  I1 卡片里的每个数字都能在 result 里找到同源（不许多算、不许抄错）
  I2 没人写过评价的机器，卡片必须明说，且不许出现评价词
  I3 该用途下有硬伤时，硬伤必须进 gaps，且 headline 要提前说
  I4 卡片不引入 result 里没有的机型（候选只能是 ranked 的第 2、3 名）
"""
import sys, io, os, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core
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


CASES = [
    {"cat": "thin", "budget": 5100, "purpose": ["design"]},
    {"cat": "thin", "budget": 6000, "purpose": ["design"]},
    {"cat": "gaming", "budget": 7000, "purpose": ["gaming"]},
    {"cat": "thin", "budget": 7000, "purpose": ["ai"]},
]

for sl in CASES:
    r = core.build_recommendation(dict(sl))
    a = agent.answer_card(r, sl)
    tag = "%s/%s" % (sl["cat"], sl["budget"])
    ck(tag + " 卡片不为空", bool(a) and bool(a.get("rows")), str(a))
    if not a:
        continue
    top = r["ranked"][0]

    # I1 数字同源
    money_row = next((x["v"] for x in a["rows"] if x["k"] == "到手价"), "")
    ck(tag + " 到手价与内核一致",
       agent._money(top["price"]) in money_row, "%s vs %s" % (money_row, top["price"]))
    ck(tag + " headline 含机型", top["name"] in a["headline"], a["headline"])
    # 卡片里出现的 ¥ 数字，必须都是内核给的那几个（价格/原价/国补）
    allowed = {top.get("price")}
    sub = top.get("subsidy") or {}
    allowed |= {sub.get("original"), sub.get("subsidy")}
    allowed = {agent._money(v) for v in allowed if v is not None}
    nums = {n.replace(",", "") for x in a["rows"] for n in re.findall(r"¥([\d,.]+)", x["v"])}
    ck(tag + " 没有来路不明的价格", nums <= allowed, "%s ⊄ %s" % (nums, allowed))

    # I2 没评价的机器不许被评价
    review_row = next((x["v"] for x in a["rows"] if x["k"] == "评价"), "")
    if not top.get("hasReview"):
        ck(tag + " 无评价机明说没人评过", "没人评过" in review_row, review_row)
        BANNED = ["高达", "首选", "值得买", "很顶", "性价比高", "得满分"]
        hit = [w for w in BANNED if w in a["headline"]]
        ck(tag + " 无评价机 headline 无评价词", not hit, str(hit))

    # I3 硬伤必须进 gaps 且提前说
    hard = [b for b in (top.get("blockers") or []) if b.get("level") == "hard"]
    gap_texts = " ".join(g["text"] for g in a["gaps"])
    if hard:
        ck(tag + " 硬伤进了 gaps", hard[0]["why"] in gap_texts, gap_texts[:60])
        ck(tag + " headline 先说了硬伤", "先说要紧的" in a["headline"], a["headline"])
    else:
        ck(tag + " 无硬伤时不编硬伤",
           all(g["level"] != "hard" for g in a["gaps"]), str(a["gaps"]))

    # I4 候选只能来自 ranked
    names = [x["name"] for x in r["ranked"][:3]]
    ck(tag + " 候选都在 ranked 里",
       all(c["name"] in names for c in a["candidates"]), str(a["candidates"]))
    ck(tag + " 候选不超过 2 台", len(a["candidates"]) <= 2, str(len(a["candidates"])))

# 分辨力对照：同一台机器，换一个它真有硬伤的用途，卡片必须变
r_design = core.build_recommendation({"cat": "thin", "budget": 7000, "purpose": ["office"]})
r_gaming = core.build_recommendation({"cat": "thin", "budget": 7000, "purpose": ["gaming"]})
a1 = agent.answer_card(r_design, {})
a2 = agent.answer_card(r_gaming, {})
ck("分辨力：换用途后 gaps 有变化",
   [g["text"] for g in a1["gaps"]] != [g["text"] for g in a2["gaps"]],
   "两次都相同 → 判据没分辨力")

# 边界：空结果不许抛异常
ck("空 ranked 返回 None", agent.answer_card({"ranked": []}) is None)

print("\n" + "=" * 70)
print("通过 %d / %d" % (OK, OK + BAD))
if FAILS:
    print("\n失败明细：")
    for n, note in FAILS:
        print("  - %s   %s" % (n, note))
else:
    print("全绿。")
print("=" * 70)
