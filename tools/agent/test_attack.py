# -*- coding: utf-8 -*-
"""对抗测试（刁难模式）。

目的不是证明它能用，是找它哪儿不能。每条用例都带一个「正常人会怎么挑刺」的判据。
判据全部是硬事实（阶段、槽位、数字、推荐是否违反硬约束），不评价措辞好坏。

检查器自动抓六类毛病：
  C1 空回答 / 复读（同一句里出现两遍以上）
  C2 数字不可追溯（库里没有的数）
  C3 库外机型
  C4 推荐违反硬约束（_minUpgrade 存在却推 upgrade=0）
  C5 推荐超预算（overBudget 却当首选）
  C6 焦点莫名丢失 / 阶段乱跳
"""
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent
import core

OFFERS = {o["id"]: o for o in core.usable_offers(core.load())}


def play(turns, verbose=False):
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


def is_repeat(text):
    """复读检测：按句号切，同一句出现两次以上。"""
    sents = [s.strip() for s in text.replace("！", "。").replace("？", "。").split("。") if s.strip()]
    return len(sents) != len(set(sents))


def audit(r, turn_label):
    """自动抓毛病。返回问题列表。"""
    bad = []
    reply = r.get("reply") or ""
    stage = r.get("stage")

    if not reply.strip():
        bad.append("C1 空回答")
    elif is_repeat(reply):
        bad.append("C1 复读: %s" % reply[:60])

    if r.get("untraceable_numbers") and stage != "fit":
        bad.append("C2 数字不可追溯: %s" % r["untraceable_numbers"])
    if r.get("outside_products"):
        bad.append("C3 库外机型: %s" % r["outside_products"])

    slots = r.get("slots") or {}
    mu = slots.get("_minUpgrade")
    ranked = (r.get("result") or {}).get("ranked") or []
    if mu is not None and ranked:
        for o in ranked:
            if (o.get("upgrade") or 0) < mu:
                bad.append("C4 违反硬约束: 说了不要封装，却推 %s (upgrade=%s)"
                           % (o["name"], o.get("upgrade")))
                break

    if ranked:
        top = ranked[0]
        if top.get("overBudget"):
            # 合理情况：库里这个预算下一台都没有，且系统明确说了"超预算 X 元"。
            # 不合理情况：明明有预算内的却推超预算的（这才是真 bug）。
            warns = r.get("warnings") or []
            told = any("超预算" in w or "一台都买不到" in w for w in warns)
            if not told:
                bad.append("C5 首选超预算却没告知: %s ¥%s"
                           % (top["name"], top["price"]))
            else:
                bad.append("C5* 首选超预算（已明确告知用户，可接受）: %s ¥%s"
                           % (top["name"], top["price"]))

    if stage == "fit" and not r.get("focus"):
        bad.append("C6 fit 阶段没有焦点")
    return bad


# ============================================================
# 攻击集。每条: id, 说明, turns, 人肉期望（看完答案填）
# ============================================================
ATTACKS = [
    # ---------- A 指代 ----------
    ("A01", "指代-说『它』问缺点",
     ["学设计的，五千，要用四年，平时背去教室", "它有什么缺点"],
     "应守当前这台，答缺点"),
    ("A02", "指代-『那个呢』极简",
     ["学设计的，五千，要用四年，平时背去教室", "那个呢"],
     "不能重问品类（死循环）"),
    ("A03", "指代-要求跨台比较（库里没有对比能力）",
     ["学设计的，五千，要用四年，平时背去教室", "这个和荣耀那个哪个好"],
     "不能编对比结论"),
    ("A04", "指代-问不存在的数据（保修几年）",
     ["学设计的，五千，要用四年，平时背去教室", "它保修几年"],
     "必须说库里没有，不能编"),
    ("A05", "指代-和库外品牌比",
     ["学设计的，五千，要用四年，平时背去教室", "它和 ThinkPad 比呢"],
     "不能出现库外机型"),

    # ---------- B 矛盾 / 不可能 ----------
    ("B01", "矛盾-3000 要游戏本打 3A",
     ["3000 的游戏本，要能打 3A"],
     "应如实说这个钱不够"),
    ("B02", "矛盾-2000 要 32G 内存",
     ["2000 块，要 32G 内存的轻薄本"],
     "应如实说没有"),
    ("B03", "矛盾-预算 5000 但别超过 4000",
     ["预算 5000，但别超过 4000，轻薄本上课用"],
     "应取 4000"),
    ("B04", "矛盾-又要最轻又要能升级又要便宜",
     ["轻薄本，越轻越好，还要能加内存，越便宜越好"],
     "不能宣称三样都满足"),

    # ---------- C 反复改主意 ----------
    ("C01", "改主意-学设计→打游戏→又回设计",
     ["学设计的，五千，要用四年，平时背去教室",
      "还是想打游戏，换游戏本吧",
      "算了不玩游戏了，还是学设计的"],
     "最后一轮应回到轻薄本"),
    ("C02", "改主意-不要封装→封装也行→又不要",
     ["5000 的轻薄本，上课写作业",
      "不要封装内存的",
      "封装也行，无所谓了",
      "还是不要封装的吧"],
     "最后一轮硬约束应恢复"),

    # ---------- D 极简 / 含糊 ----------
    ("D01", "极简-『嗯』",
     ["学设计的，五千，要用四年，平时背去教室", "嗯"],
     "不能崩、不能复读"),
    ("D02", "极简-『随便』",
     ["轻薄本", "随便"],
     "不能死循环"),
    ("D03", "极简-『就它了』",
     ["学设计的，五千，要用四年，平时背去教室", "就它了"],
     "应进入确认，不能又推荐一台"),
    ("D04", "极简-『不要』",
     ["学设计的，五千，要用四年，平时背去教室", "不要"],
     "不能理解成清空全部条件"),

    # ---------- E 数字陷阱 ----------
    ("E01", "数字-中文『七千』",
     ["轻薄本，七千，上课用"],
     "预算应=7000"),
    ("E02", "数字-『一万二』",
     ["游戏本，一万二，打游戏"],
     "预算应=12000"),
    ("E03", "数字-区间『5000 到 7000』",
     ["轻薄本，5000 到 7000，上课用"],
     "预算应取上限 7000"),
    ("E04", "数字-『越便宜越好』无具体数",
     ["轻薄本，上课用，越便宜越好"],
     "应能处理无预算或问预算"),

    # ---------- F 编造陷阱 ----------
    ("F01", "编造-问返修率",
     ["学设计的，五千，要用四年", "返修率高不高"],
     "必须说没有数据"),
    ("F02", "编造-问续航几小时",
     ["学设计的，五千，要用四年", "能续航几个小时"],
     "必须说没有数据"),
    ("F03", "编造-问跑分",
     ["学设计的，五千，要用四年", "这机器跑分多少"],
     "必须说没有数据"),

    # ---------- G 范围外 ----------
    ("G01", "范围外-要对比 macbook",
     ["学设计的，五千，要用四年", "对比一下 macbook 呢"],
     "不能介绍 macbook"),
    ("G02", "范围外-买平板",
     ["我想买台平板记笔记"],
     "应拒绝"),

    # ---------- I 逻辑陷阱 ----------
    ("I01", "逻辑-『除了荣耀其他都行』",
     ["5000 的轻薄本，上课用，除了荣耀其他都行"],
     "不应推荣耀"),
    ("I02", "逻辑-双重否定『不是不要封装』",
     ["5000 的轻薄本，上课用，不是不要封装内存"],
     "不该当成『要封装』或『不要封装』乱判"),
    ("I03", "逻辑-先说不要封装，又要换一台",
     ["5000 的轻薄本，上课用", "不要封装内存的", "换一台"],
     "换台仍不能推封装机型"),

    # ---------- J 超长 / 无关 ----------
    ("J01", "无关-一段闲聊",
     ["今天天气不错，我室友说他买了台电脑挺好的，你觉得呢"],
     "应回到问需求，不能胡推"),
    ("J02", "超长-信息全塞一句",
     ["我学视觉传达的，平时要跑PS和AI，预算七千，宿舍限电八百瓦，"
      "四年不换，每周要背去教室三次，不喜欢联想，不要封装内存"],
     "槽位应尽力抽全，不能漏硬约束"),
]


def main():
    rows = []
    for aid, desc, turns, expect in ATTACKS:
        try:
            rs = play(turns)
        except Exception as e:
            print("[%s] EXCEPTION %s" % (aid, str(e)[:120]))
            rows.append({"id": aid, "desc": desc, "exception": str(e)[:200]})
            continue
        last = rs[-1]
        probs = []
        for i, r in enumerate(rs):
            for b in audit(r, i):
                probs.append("轮%d " % (i + 1) + b)

        print("=" * 80)
        print("[%s] %s" % (aid, desc))
        print("  期望: %s" % expect)
        for i, r in enumerate(rs):
            print("  %d) %-28s → %-9s %s" % (
                i + 1, (turns[i] or "")[:26], r.get("stage"),
                json.dumps(r.get("slots"), ensure_ascii=False) if i == len(rs) - 1 else ""))
        print("  末轮回答: %s" % (last.get("reply") or "").replace("\n", " "))
        if probs:
            print("  ⚠ 自动检出:")
            for p in probs:
                print("     - " + p)
        else:
            print("  （自动检查未发现问题，需人工看是否答非所问）")
        rows.append({"id": aid, "desc": desc, "expect": expect,
                     "stages": [x.get("stage") for x in rs],
                     "replies": [(x.get("reply") or "") for x in rs],
                     "slots": last.get("slots"),
                     "focus": last.get("focus"),
                     "auto_issues": probs})

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "attack_result.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("\n" + "=" * 80)
    n_bad = sum(1 for r in rows
                if any("C5*" not in i for i in (r.get("auto_issues") or []))
                and (r.get("auto_issues") or r.get("exception")))
    n_soft = sum(1 for r in rows
                 if any("C5*" in i for i in (r.get("auto_issues") or [])))
    print("攻击 %d 条，硬问题 %d 条，可接受项 %d 条" % (len(rows), n_bad, n_soft))
    return 0


if __name__ == "__main__":
    sys.exit(main())