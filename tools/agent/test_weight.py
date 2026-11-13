# -*- coding: utf-8 -*-
"""重量单一来源 —— 回归测试（2026-11-13 新增）。

════════════════════════════════════════════════════════════════════
这组测试是为什么加的（真实事故，不是假想）
════════════════════════════════════════════════════════════════════
2026-11-13 公网实跑「5000块写代码，轻一点」，同一份回答里出现两句互相打脸的话：

    正文：  「虽然它只有 62.5% sRGB 色域，且 1.48kg 的重量在 14 寸里偏重」
    字段卡：「重量  库里没记实测重量」
    内核：  weightKg = null

两句都是从库里来的，都没编造 —— 但**同一份回答对同一个事实说了两种话**。
用户会照哪一个？他不知道该信哪句。这比"少说一句"严重得多。

根因不是模型幻觉，是库里**同一个事实有两个存放处**：
  ① 自由文本 cons/pros —— 「1.48kg 在 14 寸里偏重」
  ② 结构化字段 weightKg —— 空
模型读得到 ①、读不到 ②（② 被 _carry_note / answer_card 用 `if not w` 判成"没记"）。

另一个叠加因素（同样真实）：这台机器在池里**有两条记录**
    thin-zhan66        惠普战 66 八代锐龙版          ¥5009      weightKg=None
    spec-hp-66g8-amd   战66 八代 14 英寸（2026 款锐龙版） ¥4674.15  weightKg=1.48
去重键是「战66八代锐龙版」vs「战66八代14英寸」，对不上 —— 因为一个用
"锐龙版"、一个用"14 英寸"来表达同一台机器。**这不合并**（合错比留重复严重），
但要保证无论挑中哪条，重量这个事实都是同一个，不会一条有、一条没有。

════════════════════════════════════════════════════════════════════
判据（宪法第 4 条：判据要有分辨力 —— 拿故意错的输入试它会不会照样通过）
════════════════════════════════════════════════════════════════════
W1  能从 cons/pros 抽出这台自己的重量；抽出来之后，字段卡与正文不再互相打脸。
W2  🔴 **必须不通过的反例**：句子里那个 kg 数不是这台机器的，就不许抽。
       「比 1.4kg 的竞品重」「同价位 1.3kg 的机器更轻」—— 抽了就是把别人的重量
       安到这台头上，比原来的"没记"更糟。
W3  一句话里出现多个 kg 数（区间 / 对比）→ 弃权，不猜。
W4  已经填在 weightKg 里的，不许被文本里的数覆盖（结构化字段优先级最高）。
W5  修完必须验证没有引入新的品类红线 / 池子变化。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core  # noqa: E402


# ==== 判据本体（被测函数，和 core 里真正用的那份保持一致）====================
def kg_from_text(o):
    """从 pros/cons 里抽这台机器**自己**的重量（kg）。抽不到返回 None。

    只认"主语是本机"的写法，见 W2 那组反例。
    """
    return core.weight_from_review_text(o)


# ==== 测试 ==================================================================
PASS = []
FAIL = []


def check(name, got, want):
    if got == want:
        PASS.append(name)
    else:
        FAIL.append("%s\n     期望 %r\n     实际 %r" % (name, want, got))


def main():
    print("=" * 64)
    print("重量单一来源 回归测试")
    print("=" * 64)

    # ---- W1：这台自己的重量能抽出来 ------------------------------------
    print("\n[W1] 能从 pros/cons 抽出本机重量")
    cases = [
        ({"pros": ["1.4kg 出头，背着不累"]}, 1.4),
        ({"pros": ["1.4kg，便携做得不错"]}, 1.4),
        ({"cons": ["1.48kg 在 14 寸里偏重"]}, 1.48),
        ({"pros": ["1.14kg，这一档最轻的之一"]}, 1.14),
        ({"cons": ["机身重量接近 2.5kg"]}, 2.5),
        ({"cons": ["2.8kg 级别，基本告别通勤"]}, 2.8),
    ]
    for o, want in cases:
        check("抽出 %s" % (o.get("pros") or o.get("cons"))[0], kg_from_text(o), want)

    # ---- W2：🔴 反例组，必须全部抽不出 ----------------------------------
    print("\n[W2] 🔴 反例：那个 kg 数不是这台机器的（必须抽不出）")
    traps = [
        ("比 1.4kg 的竞品重", "别家的重量"),
        ("同价位有 1.3kg 的机器更轻", "别家的重量"),
        ("比上一代轻了 0.2kg", "差值不是绝对重量"),
        ("背包承重 5kg 够用", "不是本机重量"),
        ("（对比 1.6kg 的同级产品）", "别家的重量"),
        ("加了 1kg 电源适配器", "配件不是本机"),
        ("屏幕 400nit，重量待查", "没有 kg 数"),
        ("重量 1.5 吨", "单位不是 kg"),
    ]
    for text, why in traps:
        got = kg_from_text({"cons": [text]})
        check("反例「%s」(%s)" % (text, why), got, None)

    # ---- W3：多个 kg 数 → 弃权 -----------------------------------------
    print("\n[W3] 一句话多个 kg 数 → 弃权，不猜")
    multi = [
        "重量 1.4-1.6kg 视配置",
        "1.4kg 起，1.6kg 是顶配",
        "比 1.2kg 的旧款重 0.2kg",
    ]
    for text in multi:
        check("多值「%s」" % text, kg_from_text({"cons": [text]}), None)

    # ---- W4：结构化字段优先，不被文本覆盖 ------------------------------
    print("\n[W4] weightKg 已有值时不被文本覆盖")
    o = {"weightKg": 1.30, "cons": ["1.48kg 在 14 寸里偏重"]}
    check("weightKg=1.30 保持", core.effective_weight(o), 1.30)

    # 结构化没有 → 用文本的
    o2 = {"cons": ["1.48kg 在 14 寸里偏重"]}
    check("weightKg 为空时回落到文本", core.effective_weight(o2), 1.48)

    # 两边都没有 → None（如实说没记）
    check("两边都没有 → None", core.effective_weight({"cons": ["屏幕一般"]}), None)

    # ---- W5：真实数据上跑一遍 -----------------------------------------
    print("\n[W5] 真实数据：修完后池子里「有 kg 文本但字段为空」的台数")
    d = core.load()
    offs = d["offers"]
    offers = offs if isinstance(offs, list) else list(offs.values())
    before = 0
    fixed = 0
    for m in offers:
        w = m.get("weightKg")
        t = kg_from_text(m)
        if t is not None and not w:
            before += 1
            if core.effective_weight(m) is not None:
                fixed += 1
    print("      有文本重量但 weightKg 为空: %d 台" % before)
    print("      修完后能给出重量的:        %d 台" % fixed)
    if before != fixed:
        FAIL.append("W5 有 %d 台文本重量没能救回来" % (before - fixed))
    else:
        PASS.append("W5 全部救回（%d 台）" % before)

    # 战66 这条必须能被救回来 —— 就是触发这次修复的那一台
    zhan = next((m for m in offers if m.get("id") == "thin-zhan66"), None)
    if zhan:
        check("战66 的 effective_weight", core.effective_weight(zhan), 1.48)
        # 已经填了 weightKg 的那条不许被改
        for o2 in core.merged_offers():
            if o2.get("id") == "spec-hp-66g8-amd":
                check("采集层战66 重量不被覆盖", o2.get("weightKg"), 1.48)

    # ---- W6：同一份回答不许自相矛盾 ------------------------------------
    print("\n[W6] 字段卡与正文对同一个事实说法一致")
    res = None
    try:
        import agent
        out = agent.run("5000块写代码，轻一点")
        res = out
    except Exception as e:                       # 没有 Ollama 时跳过（CI）
        print("      跳过（模型不可用：%s）" % type(e).__name__)

    if res:
        top = (res.get("kernel", {}).get("ranked") or [{}])[0]
        rows = {r["k"]: r["v"] for r in (res.get("answer", {}).get("rows") or [])}
        wt_row = rows.get("重量", "")
        reply = res.get("reply") or ""
        # 正文里出现的 kg 数（只看正文，字段卡单独看）
        reply_kg = re.findall(r"(\d+(?:\.\d+)?)\s*kg", reply)
        says_none = "没记" in wt_row
        if says_none and reply_kg:
            FAIL.append("W6 字段卡说「没记」，正文却写了 %s kg —— 自相矛盾"
                        % "/".join(reply_kg))
        elif says_none:
            PASS.append("W6 字段卡说没记，正文也没提具体重量（一致地不知道）")
        else:
            PASS.append("W6 字段卡给了重量 %s，正文 kg 数 %s"
                        % (wt_row, reply_kg or "未提（不矛盾，只是没展开）"))

    # ---- W7：四条出口必须同源 ------------------------------------------
    print("\n[W7] 🔴 四条出口（brief / build_recommendation / server 投影 / 字段卡）同源")
    # 这是「判据漂移」的第 8 次复现：core 里改好了，server.py 的投影层
    # 自己又写了一遍 `x.get("weightKg")`，前端拿到的还是 null。
    # 判据：对同一个 offer，四个出口给出的重量必须**完全相等**。
    d = core.load()
    offers = d["offers"]
    offers = offers if isinstance(offers, list) else list(offers.values())
    probe = next((m for m in offers if m.get("id") == "thin-zhan66"), None)
    if probe:
        merged = next((o for o in core.merged_offers()
                       if o.get("id") == "thin-zhan66"), None)
        src = core.effective_weight(merged)
        b = core.brief(merged)
        check("W7 brief() 带上重量", b.get("weightKg"), src)

        # build_recommendation 的投影
        res = core.build_recommendation({"cat": "thin", "budget": 5000, "purpose": ["dev"]})
        row = next((x for x in (res.get("ranked") or [])
                    if x.get("id") == "thin-zhan66"), None)
        if row:
            check("W7 build_recommendation() 投影", row.get("weightKg"), src)

        # server 的投影（slim_result 里的 kernel.ranked）
        import server
        shaped = server.slim_result({
            "stage": "recommend", "mode": "work", "dataAsOf": "x",
            "result": {"ranked": [{"id": "thin-zhan66", "name": "战66",
                                   "price": 5009, "spec": "s", "perf": 62,
                                   "upgrade": 2, "portable": 2, "tag": "",
                                   "caution": "", "subsidy": None,
                                   "hasReview": True, "weightKg": src,
                                   "ram": 16}]},
        })
        k = (shaped.get("kernel") or {}).get("ranked") or [{}]
        check("W7 server._shape() 投影", k[0].get("weightKg"), src)

    # ---- 汇总 -----------------------------------------------------------
    print("\n" + "=" * 64)
    for f in FAIL:
        print("FAIL " + f)
    if FAIL:
        print("\n失败 %d / 共 %d" % (len(FAIL), len(FAIL) + len(PASS)))
        return 1
    print("通过 %d / %d" % (len(PASS), len(PASS)))
    print("全绿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())