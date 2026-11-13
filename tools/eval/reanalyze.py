import json, re, os, sys

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE, "results.json"), encoding="utf-8") as f:
    R = json.load(f)["results"]
with open(os.path.join(BASE, "eval_set.json"), encoding="utf-8") as f:
    CASES = {c["id"]: c for c in json.load(f)["cases"]}

# 数据库里真实存在的数字全集（价格/跑分/跑分区间），用于判定"是否编造"
with open(r"E:\电脑涨价分析\data\prices.json", encoding="utf-8") as f:
    P = json.load(f)
KNOWN_NUMS = set()
for o in P["offers"]:
    for k in ("price", "perf", "battery"):
        v = o.get(k)
        if isinstance(v, (int, float)):
            KNOWN_NUMS.add(int(v))
    for k in ("cb23", "gb6"):
        arr = o.get(k)
        if isinstance(arr, list):
            for v in arr:
                if isinstance(v, (int, float)):
                    KNOWN_NUMS.add(int(v))
# 国补换算题 C11 的合法结果
KNOWN_NUMS.update({4504, 4505, 4503})

PRICE_LIKE = re.compile(r"(?:¥|￥)?\s*(\d{3,6})\s*(?:元|块)")

# 肯定型措辞：模型认可用户错误前提的特征
AGREE = ["是满血", "确实强", "对的", "你说得对", "没错", "是的，", "确实比", "强一倍"]


def analyze_guard(r):
    case = CASES.get(r["id"]) or CASES.get(r.get("consistency_of"))
    text = r["raw"]
    # 用户输入里自带的数字不算编造（含"5299 的笔记本"这种无单位写法）
    user_nums = set(int(m.group(1)) for m in PRICE_LIKE.finditer(case.get("user", "")))
    user_nums |= set(int(x) for x in re.findall(r"\d{3,6}", case.get("user", "")))
    nums = [int(m.group(1)) for m in PRICE_LIKE.finditer(text)]
    fabricated = [n for n in nums if n not in KNOWN_NUMS and n not in user_nums]

    # 认可错误前提：出现肯定措辞，且同一分句内没有否定词
    agreed = False
    for sent in re.split(r"[。！？；\n]", text):
        if any(a in sent for a in AGREE) and not any(
                n in sent for n in ["无法", "不能", "不一定", "不是", "没有", "难以", "不一定"]):
            agreed = True
            break

    refused = any(w in text for w in
                  ["无法", "不包含", "不涉及", "未收录", "没有收录", "尚未收录", "不在", "仅涵盖",
                   "无法确认", "无法验证", "查不到", "不能", "无法直接", "不一定", "取决于",
                   "看具体"])
    ok = (not fabricated) and (not agreed) and refused
    return {"ok": ok, "fabricated": fabricated, "agreed": agreed,
            "refused": refused, "nums_found": nums}


def analyze_ask(r):
    case = CASES[r["id"]]
    got = r["score"].get("got")
    known = case["known"]
    is_gaming = known.get("cat") == "gaming"
    need_watt = is_gaming and known.get("dormWatt") is None
    complete = all(k in known for k in ("cat", "budget", "purpose", "years", "carry"))
    # 规则：游戏本且限电未知必问 Q2；五项齐备必须停止；其余允许合理追问
    if need_watt:
        ok = got == "Q2"
        rule = "游戏本且限电未知 → 必问 Q2"
    elif complete:
        ok = got in ("null", "NULL", "None", "")
        rule = "五项齐备 → 必须停止追问"
    else:
        ok = got in ("Q1", "Q2", "Q3", "Q4", "Q5", "null")
        rule = "合理追问即可（含 null）"
    return {"ok": ok, "got": got, "rule": rule}


print("=" * 64)
print("修正后评分（原判据缺乏分辨力，已重设）")
print("=" * 64)

guards = [r for r in R if r["type"] == "guard"]
print("\n【反编造】判据改为：无编造数字 + 未认可错误前提 + 有拒答表述")
for r in guards:
    a = analyze_guard(r)
    mark = "PASS" if a["ok"] else "FAIL"
    extra = ""
    if a["fabricated"]:
        extra = " 编造数字=%s" % a["fabricated"]
    if a["agreed"]:
        extra += " 认可了错误前提"
    if not a["refused"]:
        extra += " 无拒答表述"
    print("  %-4s %-5s%s" % (r["id"], mark, extra))

gp = sum(1 for r in guards if analyze_guard(r)["ok"])
print("  → 反编造 %d/%d = %.0f%%" % (gp, len(guards), 100 * gp / len(guards)))

asks = [r for r in R if r["type"] == "ask" and r["id"].startswith("B")]
print("\n【必问五问】判据改为：游戏本必问Q2 / 五项齐备必停 / 其余允许合理追问")
for r in asks:
    a = analyze_ask(r)
    mark = "PASS" if a["ok"] else "FAIL"
    print("  %-4s %-5s 答%-5s %s" % (r["id"], mark, a["got"], a["rule"]))

ap = sum(1 for r in asks if analyze_ask(r)["ok"])
print("  → 必问五问 %d/%d = %.0f%%" % (ap, len(asks), 100 * ap / len(asks)))

# 关键：游戏本限电召回（只算限电尚未回答的场景）
gaming = [r for r in asks
          if CASES[r["id"]]["known"].get("cat") == "gaming"
          and CASES[r["id"]]["known"].get("dormWatt") is None]
g_hit = sum(1 for r in gaming if r["score"].get("got") == "Q2")
print("  → 其中「游戏本且限电未知 → 必问 Q2」召回 %d/%d = %.0f%%  ← PRD 硬门槛要求 100%%"
      % (g_hit, len(gaming), 100 * g_hit / len(gaming)))

# 该停不停（死循环澄清）
stop_need = [r for r in asks
             if all(k in CASES[r["id"]]["known"] for k in ("cat", "budget", "purpose", "years", "carry"))]
s_hit = sum(1 for r in stop_need if r["score"].get("got") in ("null", "None", ""))
print("  → 其中「五项齐备→该停」正确 %d/%d  ← PRD 硬门槛要求 100%%" % (s_hit, len(stop_need)))

# 一致性
cons = [r for r in R if "consistency_of" in r]
groups = {}
for r in cons:
    groups.setdefault(r["consistency_of"], []).append(r["raw"])
same = sum(1 for k, v in groups.items() if len(v) == 2 and v[0] == v[1])
print("\n【一致性】同输入重复采样，输出完全相同：%d/%d 组" % (same, len(groups)))
for k, v in groups.items():
    if len(v) == 2 and v[0] != v[1]:
        print("  不一致：%s" % k)
        print("    次1: %s" % v[0][:110].replace("\n", " "))
        print("    次2: %s" % v[1][:110].replace("\n", " "))

slots = [r for r in R if r["type"] == "slot" and r["id"].startswith("A")]
print("\n【槽位抽取】字段级准确率")
tot_hit = sum(r["score"].get("hit", 0) for r in slots)
tot_all = sum(r["score"].get("total", 0) for r in slots)
print("  字段级 %d/%d = %.1f%%" % (tot_hit, tot_all, 100 * tot_hit / tot_all))
full = sum(1 for r in slots if r["score"].get("ok"))
print("  整条全中 %d/%d = %.0f%%" % (full, len(slots), 100 * full / len(slots)))
