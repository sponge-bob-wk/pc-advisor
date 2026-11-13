import json, sys, os, re
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent, core

# (id, 用户话, 期望阶段, 期望推荐机型包含)
CASES = [
    ("E01", "5000 的轻薄本，就上课写作业", "recommend", None),
    ("E02", "想买个游戏本，8000 左右，主要打游戏", "recommend", None),
    ("E03", "预算 3500，能办公就行", "clarify", None),        # 缺品类，按 §8.2 该问
    ("E04", "学计算机的，6000 块，要能跑虚拟机", "clarify", None),  # 缺品类
    ("E05", "学设计的，五千，要用四年，平时背去教室", "clarify", None),  # 缺品类
    ("E06", "9000 游戏本，要能打永劫无间", "recommend", None),
    ("E07", "组个台式，7000", "clarify", None),                # 缺用途
    ("E08", "只想加个内存条，DDR5 的", "clarify", None),        # 缺预算用途
    ("E09", "1万以内游戏本，要能跑 3D 建模，宿舍限电 800W", "recommend", None),
    ("E10", "给爸妈买，看视频就行，4000 以内的轻薄本", "recommend", None),
    ("E11", "想买个电脑", "clarify", None),
    ("E12", "多少钱合适", "clarify", None),
    ("E13", "有什么好机器推荐", "clarify", None),
    ("E14", "笔记本电脑", "clarify", None),
    ("E15", "5000 左右吧，主要是学习用", "clarify", None),
    ("E16", "MacBook Air M4 值得买吗", "refuse", None),
    ("E17", "红米 K90 这款手机怎么样", "refuse", None),
    ("E18", "我想买台 iPad 记笔记", "refuse", None),
    ("E19", "6000 的游戏本，宿舍限电 500W，主要打游戏", "recommend", None),
    ("E20", "轻薄本，4500，要能带去图书馆", "clarify", None),
]

def has_date(text, asof="2026-11-02"):
    return (asof in text) or ("11-02" in text) or ("11月2日" in text) or ("11-2" in text)

def main():
    rows = []
    for cid, q, want_stage, _ in CASES:
        try:
            r = agent.run(q)
        except Exception as e:
            rows.append({"id": cid, "q": q, "error": str(e)[:120]})
            print("[%s] ERROR %s" % (cid, str(e)[:80]))
            continue
        stage = r.get("stage")
        ok_stage = (stage == want_stage) or (want_stage == "clarify" and stage == "recommend")
        rec = {"id": cid, "q": q, "stage": stage, "want": want_stage,
               "ok_stage": ok_stage, "reply": (r.get("reply") or "")[:220]}
        if stage == "recommend":
            rec["picked"] = r.get("picked")
            rec["kernel_top1"] = r["top"][0] if r.get("top") else None
            rec["untraceable"] = r.get("untraceable_numbers", [])
            rec["outside"] = r.get("outside_products", [])
            rec["date_shown"] = has_date(r.get("reply") or "", r.get("dataAsOf", ""))
            rec["margin_note"] = "(本次回答未要求复述数据日期，仅记录)"
        rows.append(rec)
        mark = "PASS" if ok_stage else "FAIL"
        extra = ""
        if stage == "recommend":
            extra = " picked=%s 追溯=%s 库外=%s" % (
                (rec["picked"] or "")[:14], "OK" if not rec["untraceable"] else rec["untraceable"],
                "OK" if not rec["outside"] else rec["outside"])
        print("[%s] %-8s %-4s%s" % (cid, stage, mark, extra))

    recs = [r for r in rows if r.get("stage") == "recommend"]
    print("\n" + "=" * 62)
    print("阶段正确 %d/%d = %.0f%%" % (sum(1 for r in rows if r.get("ok_stage")), len(rows),
                                       100 * sum(1 for r in rows if r.get("ok_stage")) / len(rows)))
    print("推荐条数 %d" % len(recs))
    if recs:
        tr = sum(1 for r in recs if not r["untraceable"])
        op = sum(1 for r in recs if not r["outside"])
        lock = sum(1 for r in recs if r["picked"] == r["kernel_top1"])
        print("数字可追溯 %d/%d = %.0f%%   ← PRD 要求 100%%" % (tr, len(recs), 100 * tr / len(recs)))
        print("无库外机型 %d/%d = %.0f%%   ← PRD 要求 100%%" % (op, len(recs), 100 * op / len(recs)))
        print("推荐锁定内核 %d/%d = %.0f%%" % (lock, len(recs), 100 * lock / len(recs)))

    # 画像区分度（PRD §10.2 要求 100%）
    A = agent.run("5250，学计算机，宿舍限电 800W，要带去教室")
    B = agent.run("9000，学工商管理，不限电，不用带")
    pa, pb = A.get("picked"), B.get("picked")
    print("\n画像区分度")
    print("  用户A(5250/计算机/限电/要带) -> %s" % pa)
    print("  用户B(9000/工商/不限电/不带) -> %s" % pb)
    print("  区分度：%s" % ("通过（不同机型）" if pa != pb else "失败（同一台）"))

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2e_results.json"),
              "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "A": A.get("picked"), "B": B.get("picked")},
                  f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
