import json, urllib.request, time, re, sys, os

sys.stdout.reconfigure(encoding='utf-8')

API = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.5-4b"
BASE = os.path.dirname(os.path.abspath(__file__))

SLOT_SYS = """你是购买电脑需求的解析器。从用户的一句话中抽取槽位，只输出 JSON，不要解释。

字段定义：
- cat: 品类，只能是 "thin"(轻薄本) / "gaming"(游戏本) / "desktop"(台式) / "upgrade"(内存固态等配件) / null
- budget: 预算上限，整数，单位元。用户说"五千"就填5000，"一万"就填10000。没提就 null
- purpose: 用途数组，可选值 "office" "gaming" "dev" "video" "design"。没提就 []
- years: 打算用几年，整数。没提就 null
- carry: 是否需要经常携带（带去教室、带回家、出差），布尔。没提就 null
- dormWatt: 宿舍限电功率，整数瓦。没提就 null

规则：不确定一律填 null，不许猜测，不许编造。"""

ASK_SYS = """你在帮准大学生选电脑，已收集到部分信息。判断下一步最该问哪一个问题。

必问五问：
- Q1 专业定了吗？要跑什么软件（用途不明时问）
- Q2 宿舍限电吗？大概多少瓦（涉及游戏本时必问）
- Q3 要带去教室或图书馆吗（便携需求不明时问）
- Q4 寒暑假带回家吗（异地生源时问）
- Q5 四年内打算换吗（长期使用要考虑）

规则：信息已足够给出推荐时 next_question 填 null。每次只问一个。
只输出 JSON：{"next_question":"Q1|Q2|Q3|Q4|Q5|null","reason":"一句话理由"}"""

GUARD_SYS = """你是一个电脑选购决策助手。所有机型、价格、跑分数据都来自一个受限数据库，你不掌握数据库之外的任何数字。
原则：查不到就说查不到，绝不编造具体价格、跑分或参数。
覆盖范围只有 Windows 笔记本、台式整机、内存固态配件，不包含 Mac，也不包含手机。

请直接回答用户，控制在三句话以内。"""


def chat(messages, fmt=None, num_predict=200, temperature=0.2):
    payload = {
        "model": MODEL, "stream": False, "think": False,
        "messages": messages,
        "options": {"num_predict": num_predict, "temperature": temperature},
    }
    if fmt:
        payload["format"] = fmt
    req = urllib.request.Request(
        API, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=240) as r:
        d = json.loads(r.read().decode("utf-8"))
    elapsed = time.time() - t0
    return d.get("message", {}).get("content", ""), elapsed


PRICE_RE = re.compile(r"(¥|￥)?\s*\d{3,6}\s*(元|块)")


def score_slot(case, out_text):
    try:
        o = json.loads(out_text)
    except Exception:
        return {"ok": False, "detail": "JSON解析失败", "hit": 0, "total": 1, "parsed": None}

    checks = []
    for k, v in case.get("expect", {}).items():
        got = o.get(k)
        if isinstance(v, bool):
            ok = got is v
        elif isinstance(v, (int, float)):
            ok = got == v
        else:
            ok = str(got) == str(v)
        checks.append((k, ok, got, v))
    for k in case.get("expect_null", []):
        checks.append((k + "_null", o.get(k) is None, o.get(k), None))

    hit = sum(1 for c in checks if c[1])
    total = len(checks) if checks else 1
    return {
        "ok": hit == total and total > 0,
        "hit": hit, "total": total,
        "detail": "; ".join("%s=%s(期望%s)%s" % (c[0], c[2], c[3], "OK" if c[1] else "X")
                            for c in checks if not c[1]) or "全中",
        "parsed": o,
    }


def score_ask(case, out_text):
    try:
        o = json.loads(out_text)
    except Exception:
        return {"ok": False, "detail": "JSON解析失败", "got": None}
    got = o.get("next_question")
    got_norm = "null" if got in (None, "null", "", "None") else str(got).strip().upper()
    ok = got_norm.lower() in [str(x).lower() for x in case["expect_q"]]
    return {"ok": ok, "got": got_norm, "expect": case["expect_q"],
            "detail": "" if ok else "答 %s，期望 %s" % (got_norm, case["expect_q"])}


def score_guard(case, out_text):
    must = case.get("must_any", [])
    hit_word = next((w for w in must if w in out_text), None)
    has_price = bool(PRICE_RE.search(out_text))
    price_violation = case.get("must_not_contain_price", False) and has_price
    ok = bool(hit_word) and not price_violation
    reason = []
    if not hit_word:
        reason.append("未出现任何关键表述")
    if price_violation:
        reason.append("编造了具体价格")
    return {"ok": ok, "hit_word": hit_word, "has_price": has_price,
            "detail": "; ".join(reason) or "OK"}


def main():
    with open(os.path.join(BASE, "eval_set.json"), encoding="utf-8") as f:
        data = json.load(f)

    cases = [c for c in data["cases"]]
    extra_consistency = [
        ("D01", "A09"), ("D02", "A09"),
        ("D03", "A17"), ("D04", "A17"),
        ("D05", "A01"), ("D06", "A01"),
        ("D07", "B01"), ("D08", "B01"),
        ("D09", "C04"), ("D10", "C04"),
    ]
    by_id = {c["id"]: c for c in cases}
    runlist = [(c["id"], c) for c in cases]
    for nid, src in extra_consistency:
        src_case = dict(by_id[src])
        src_case["_consistency_of"] = src
        runlist.append((nid, src_case))

    results = []
    t_start = time.time()
    for idx, (rid, case) in enumerate(runlist, 1):
        typ = case["type"]
        if typ == "slot":
            msgs = [{"role": "system", "content": SLOT_SYS},
                    {"role": "user", "content": case["user"]}]
            out, el = chat(msgs, fmt="json", num_predict=160)
            sc = score_slot(case, out)
        elif typ == "ask":
            msgs = [{"role": "system", "content": ASK_SYS},
                    {"role": "user", "content": "已收集：" + json.dumps(case["known"], ensure_ascii=False)}]
            out, el = chat(msgs, fmt="json", num_predict=140)
            sc = score_ask(case, out)
        else:
            msgs = [{"role": "system", "content": GUARD_SYS},
                    {"role": "user", "content": case["user"]}]
            out, el = chat(msgs, num_predict=180)
            sc = score_guard(case, out)

        rec = {"id": rid, "type": typ, "group": case["group"], "elapsed": round(el, 2),
               "raw": out.strip()[:600], "score": sc}
        if "_consistency_of" in case:
            rec["consistency_of"] = case["_consistency_of"]
        results.append(rec)
        mark = "PASS" if sc.get("ok") else "FAIL"
        print("[%2d/%d] %-4s %-12s %-6s %5.2fs  %s" %
              (idx, len(runlist), rid, case["group"], mark, el, sc.get("detail", "")[:70]))

    elapsed_all = time.time() - t_start
    with open(os.path.join(BASE, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": {"model": MODEL, "date": "2026-11-11",
                            "total": len(results), "elapsed_s": round(elapsed_all, 1)},
                   "results": results}, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("总耗时 %.1fs，平均 %.2fs/条" % (elapsed_all, elapsed_all / len(runlist)))
    for g in ["明确需求", "隐含约束", "模糊开口", "必问五问", "反编造"]:
        sub = [r for r in results if r["group"] == g]
        if sub:
            p = sum(1 for r in sub if r["score"].get("ok"))
            print("  %-8s %2d/%2d = %.0f%%" % (g, p, len(sub), 100 * p / len(sub)))

    cons = [r for r in results if "consistency_of" in r]
    agree = 0
    for r in cons:
        pair = [x for x in cons if x["consistency_of"] == r["consistency_of"]]
        if len(pair) == 2:
            a, b = pair[0]["raw"], pair[1]["raw"]
            if a == b:
                agree += 1
    print("  一致性   %d/%d 次输出完全相同" % (agree, len(cons)))


if __name__ == "__main__":
    main()
