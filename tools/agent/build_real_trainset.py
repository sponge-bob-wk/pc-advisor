# -*- coding: utf-8 -*-
"""build_real_trainset.py —— 把「人工标注过的真实提问」转成可训练/可评测的格式。

分工（别混）
    crawl_real_queries.py   只负责把真话捞上来，不猜标签
    本脚本                  只负责格式转换 + 自检，不造标签
    标签                    人工写在 lora_data/crawl/judgments.json

judgments.json 格式
    {"<raw文件名>#<行号>": [intent, topic, fit, focus, note], ...}
    没出现在表里的行 = 主动丢弃（不是漏标）。用行号而不是重抄一遍文本，
    是为了避免抄错字 —— 文本从 raw 里按行号取，一定是用户原话。

产物
    lora_data/real-20261127.jsonl       人工核对/评测用（同 adversarial-real 形状）
    lora_data/real-20261127.train.jsonl  messages 格式，可直接并入 route-train

🔴 为什么 focus 要分开：现有 route-train 1381 条**全部**是
   "（当前讨论：zol-2103842）"，没有一条 "（当前没有在讨论任何机器）"。
   真实用户第一句话永远是没有焦点的。这个缺口不补，重训还是会在真实集上掉。
"""
import io
import json
import os
import sys
import zlib
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import intent_layer as il                                    # noqa: E402

LD = os.path.join(HERE, "lora_data")
CRAWL = os.path.join(LD, "crawl")

INTENTS = ("ask_current", "refuse_switch", "state_range",
           "state_new_need", "list_others", "unknown", "analyze")
TOPICS = ("service", "screen", "keyboard", "noise", "build", "weight",
          "battery", "price", "config", "pros_cons", "none")

CTX_NONE = "（当前没有在讨论任何机器）"           # 推理时 _ctx_of(None) 的原话

# 🔴 2026-11-27 发现：现有 1381 条训练样本的焦点 ctx 全是
#    "（当前讨论：zol-2103842）" —— 那是 _ctx_of() 的**降级分支**
#    （只有机型不在池里才会出现）。生产环境里焦点永远是
#    "当前正在讨论这台：联想拯救者，i7…，到手 8099 元" 这种描述式。
#    也就是说训练和推理的 system prompt **又**不是同一个东西（第二次同类事故）。
#    这里直接调 _ctx_of 取真值，不手写字符串 —— 保证和生产一致。
CTX_FOCUS = il._ctx_of("zol-2103842")


def load_raw(fn):
    rows = []
    path = os.path.join(CRAWL, fn)
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def write_pair(rows, out_plain, out_train, src_tag):
    """同一份 rows 出两份：人读的 + 训练用的。**只有这一处拼 messages。**

    out_plain 传 None = 不写人读版（保持集只出训练格式，人读版在 eval 文件里）。
    """
    if out_plain:
        with io.open(out_plain, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps({
                    "text": r["text"], "expect": r["intent"], "topic": r["topic"],
                    "fit": r["fit"], "focus": r["focus"], "note": r["note"],
                    "site": r.get("site", ""), "url": r.get("url", ""),
                }, ensure_ascii=False) + "\n")

    with io.open(out_train, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            sysmsg = il.SYS_TMPL.format(
                ctx=CTX_FOCUS if r["focus"] else CTX_NONE)
            ans = json.dumps({"intent": r["intent"], "topic": r["topic"],
                              "fit": r["fit"]}, ensure_ascii=False)
            f.write(json.dumps({
                "messages": [
                    {"role": "system", "content": sysmsg},
                    {"role": "user", "content": r["text"]},
                    {"role": "assistant", "content": ans},
                ],
                "_meta": {"src": src_tag, "focus": r["focus"],
                          "note": r["note"], "site": r.get("site", "")},
            }, ensure_ascii=False) + "\n")


def load_sim(path):
    """模拟补充集：本来就是标好的，只做合法性校验，不改标签。"""
    rows, bad = [], []
    for i, line in enumerate(io.open(path, encoding="utf-8"), 1):
        line = line.strip()
        if not line:
            continue
        o = json.loads(line)
        if o["expect"] not in INTENTS:
            bad.append("第 %d 行 intent 非法：%s" % (i, o["expect"]))
            continue
        if o["topic"] not in TOPICS:
            bad.append("第 %d 行 topic 非法：%s" % (i, o["topic"]))
            continue
        if o.get("fit") and o["expect"] != "ask_current":
            bad.append("第 %d 行 fit=1 但不是 ask_current" % i)
        rows.append({
            "text": o["text"], "intent": o["expect"], "topic": o["topic"],
            "fit": bool(o.get("fit")), "focus": bool(o.get("focus")),
            "note": o.get("note", ""), "site": "simulated", "url": "",
        })
    return rows, bad


def main():
    jp = os.path.join(CRAWL, "judgments.json")
    if not os.path.exists(jp):
        print("缺标注文件：%s" % jp)
        return 2
    judge = json.load(io.open(jp, encoding="utf-8"))

    raw_cache = {}
    rows, problems = [], []

    for key, val in judge.items():
        if key.startswith("_"):
            continue
        fn, _, idx = key.rpartition("#")
        if fn not in raw_cache:
            raw_cache[fn] = load_raw(fn)
        arr = raw_cache[fn]
        i = int(idx)
        if not (1 <= i <= len(arr)):
            problems.append("行号越界 %s（该文件 %d 行）" % (key, len(arr)))
            continue
        if len(val) < 5:
            problems.append("字段不足 %s" % key)
            continue
        intent, topic, fit, focus, note = val[:5]
        if intent not in INTENTS:
            problems.append("intent 非法 %s: %s" % (key, intent))
            continue
        if topic not in TOPICS:
            problems.append("topic 非法 %s: %s" % (key, topic))
            continue
        rows.append({
            "text": arr[i - 1]["text"], "intent": intent, "topic": topic,
            "fit": bool(int(fit)), "focus": bool(int(focus)),
            "note": note, "key": key,
            "site": arr[i - 1].get("site", ""),
            "url": arr[i - 1].get("url", ""),
        })

    # ---- 自检：fit 只能在 ask_current 上为真；无焦点不该出现指示代词
    for r in rows:
        if r["fit"] and r["intent"] != "ask_current":
            problems.append("fit=1 但 intent=%s: %s" % (r["intent"], r["text"]))
        if not r["focus"] and r["intent"] in ("ask_current", "refuse_switch"):
            problems.append("无焦点却标 %s: %s" % (r["intent"], r["text"]))

    # ---- 同一句重复（不同来源抓到同一标题）
    seen, uniq = {}, []
    for r in rows:
        k = "".join(r["text"].split())
        if k in seen:
            continue
        seen[k] = 1
        uniq.append(r)
    rows = uniq

    print("=" * 72)
    print("标注 %d 条（去重后），自检问题 %d 项" % (len(rows), len(problems)))
    for p in problems:
        print("  ! " + p)

    # ---- 输出：人读用 + 训练用（同一段代码，别抄两遍）
    #
    # 🔴 2026-11-27：**必须留出保持集**。
    #    这批真实句既是训练数据又是评测集的话，重训后再拿它评就是拿训练集考自己，
    #    分数会虚高，反而看不出补数据到底有没有用。
    #
    #    切分用 **文本 crc32 取模**，不用序号 ——
    #    序号切分每加一批新数据就整体洗牌，昨天的基线今天对不上（已踩过一次）。
    #    crc32 只跟文本有关，加多少条都不改变已有样本的归属。
    for r in rows:
        r["holdout"] = (zlib.crc32(r["text"].encode("utf-8")) % 3 == 0)
    train_rows = [r for r in rows if not r["holdout"]]
    eval_rows = [r for r in rows if r["holdout"]]

    out1 = os.path.join(LD, "real-20261127.jsonl")
    out2 = os.path.join(LD, "real-20261127.train.jsonl")
    out3 = os.path.join(LD, "real-20261127.eval.jsonl")
    out4 = os.path.join(LD, "real-20261127.holdout.jsonl")
    write_pair(train_rows, out1, out2, "real-crawl-20261127")
    write_pair(eval_rows, out4, out3, "real-crawl-20261127-holdout")
    print("\n保持集：%d 条（不进训练）→ %s" % (len(eval_rows), out3))

    print("\n意图分布：")
    for k, v in Counter(r["intent"] for r in rows).most_common():
        print("  %-16s %3d" % (k, v))
    print("\n焦点：有 %d / 无 %d" % (
        sum(1 for r in rows if r["focus"]), sum(1 for r in rows if not r["focus"])))
    print("topic：", dict(Counter(r["topic"] for r in rows).most_common()))
    print("来源：", dict(Counter(r["site"] for r in rows).most_common()))
    print("\n写出：\n  %s\n  %s" % (out1, out2))

    # ---- 模拟补充集（补真实网页上抓不到的意图）：只转格式，不改标签
    sim_path = os.path.join(CRAWL, "sim-20261127.jsonl")
    if os.path.exists(sim_path):
        sim, simbad = load_sim(sim_path)
        print("\n" + "=" * 72)
        print("模拟补充集 %d 条，格式问题 %d 项" % (len(sim), len(simbad)))
        for b in simbad:
            print("  ! " + b)
        s1 = os.path.join(LD, "sim-20261127.jsonl")
        s2 = os.path.join(LD, "sim-20261127.train.jsonl")
        write_pair(sim, s1, s2, "sim-20261127")
        print("意图分布：")
        for k, v in Counter(r["intent"] for r in sim).most_common():
            print("  %-16s %3d" % (k, v))
        L = [len(r["text"]) for r in sim]
        print("长度中位 %d，≥20 字 %d 条" % (
            sorted(L)[len(L) // 2], sum(1 for x in L if x >= 20)))
        print("写出：\n  %s\n  %s" % (s1, s2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
