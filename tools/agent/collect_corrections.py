# -*- coding: utf-8 -*-
"""collect_corrections.py —— 收集「模型判断错了、被纠正」的样本

## 为什么单独做一个脚本

现有三个收集器都不管这件事：

| 脚本 | 管什么 | 不管什么 |
|---|---|---|
| `harvest_samples.py` | 给存量提问补**回答** | 不判对错 |
| `adversarial_harvest.py` | 手写模板造**对抗样本** | 不是真实误判 |
| `harvest_from_logs.py` | 从日志抽**真实句子** | 不知道模型判对没判对 |

**真实误判是最贵的训练数据** —— 它是模型在当前版本下确实会错的地方，
比手写模板更贴近真实分布。

## 记什么

一条样本 = 用户原话 + 模型判的 + 正确答案 + 为什么错。

```json
{
  "messages": [system, user, assistant(正确答案)],
  "_meta": {
    "wrong": "{\"intent\":\"ask_current\",\"topic\":\"battery\",\"fit\":true}",
    "correct_before": false,
    "why": "「限电500W」是补充条件，不是问电池；模型把"限电"当成了"电池"相关",
    "src": "correction:2026-11-17:power-limit",
    "found": "用户实测对话 chat-2026-11-17.log 第 11 条"
  }
}
```

`_meta.wrong` 是模型当时的错误输出 —— **这一栏很重要**，
将来做 DPO/拒绝采样时它就是负样本。

## 用法

    python collect_corrections.py --list          # 看已收集多少
    python collect_corrections.py --add           # 交互式添加一条
    python collect_corrections.py --from-log DATE # 从日志批量扫（需人工确认）

## 与 route-train.jsonl 的关系

本脚本产出 `lora_data/corrections.jsonl`（独立文件，不混进 route-train）。
`build_route_lora.py` 会把它并进训练集，并优先保证 eval 里不出现重复。

**为什么不直接写 route-train.jsonl**：
那个文件是 `build_route_lora.py` 的产物，手写进去下次重建就被覆盖了。
"""
import argparse
import datetime
import io
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DIR = os.path.join(HERE, "lora_data")
OUT = os.path.join(DIR, "corrections.jsonl")

SYS = ('你是选机助手的路由器。只输出 JSON：'
       '{"intent":...,"topic":...,"fit":...}')

VALID_INTENT = {"ask_current", "refuse_switch", "state_range",
                "state_new_need", "list_others", "unknown"}
VALID_TOPIC = {"service", "screen", "keyboard", "noise", "build", "weight",
               "battery", "price", "config", "pros_cons", "none"}

# ── 2026-11-17 实测误判（用户实测对话里抓到的）────────────────────────
# 每条都是**真跑出来的错**，不是猜的。
SEED = [
    {
        "text": "限电 500W",
        "wrong": {"intent": "ask_current", "topic": "battery", "fit": True},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "why": ("「限电500W」是在**补充一个条件**（宿舍限电功率），不是问电池。"
                "模型被「限电」里的「电」字带偏，判成了 topic=battery。"
                "正确动作：记下 dormWatt=500，重新跑推荐并说明够不够用。"),
        "found": "chat-2026-11-17.log 第 11 条（用户实测）",
    },
    {
        "text": "我想打游戏",
        "wrong": {"intent": "state_new_need", "topic": "none", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": True},
        "why": ("「我想打游戏」是**说用途**，不是泛泛提需求 —— fit 该为 true，"
                "因为下一句就该判「当前这台打游戏够不够」。"
                "模型给了 fit=false，导致系统跳过匹配判断直接问预算。"),
        "found": "chat-2026-11-17.log 第 6 条（用户实测）",
    },
    {
        "text": "可以",
        "wrong": {"intent": "state_new_need", "topic": "none", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "why": ("这条**意图判对了**，但系统动作错了：上一句是"
                "「要不放宽预算？」，「可以」是**同意放宽**，"
                "该把 budget 调高并清空 seen，而不是重跑同一份 known。"
                "收录它是因为它是「省略主语的应答」典型，"
                "模型需要学会结合上一轮问句理解 —— 但单看这句话，"
                "判 state_new_need 是合理的，所以 correct 与 wrong 相同。"
                "⚠️ 这条更像「系统动作缺失」而非「模型判断错」，"
                "留作分析样本，训练时可按需过滤（_meta.actionable=false）。"),
        "found": "chat-2026-11-17.log 第 10 条（用户实测）",
        "actionable": False,
    },
    # ── 2026-11-17 10:57 用户第二次实测（换一台流程）──────────────────
    {
        "text": "打游戏",
        "wrong": {"intent": "ask_current", "topic": "config", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "why": ("上一轮系统问「主要拿来干什么？」，用户答「打游戏」——"
                "这是**在回答用途问题**，不是问当前这台。"
                "模型判成 ask_current 的后果很严重：系统以为用户在问"
                "MateBook D16 能不能打游戏，走了 answer_followup，"
                "**根本没做槽位抽取**，purpose 丢失，"
                "于是后面「换一台」时不知道用户要游戏本，"
                "又推回同一台轻薄本。"
                "对比：同样的话说成「我想打游戏」模型就判对了（state_new_need）——"
                "说明模型过度依赖「我想/我要」这类显式标记。"),
        "found": "chat-2026-11-17.log 第 17 条（用户实测）",
    },
    {
        "text": "拿他打游戏",
        "wrong": {"intent": "ask_current", "topic": "config", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "why": ("和「打游戏」同一类错。区别是这句带了「拿他」——"
                "「拿他打游戏」是**说用途**（我要拿这台去干什么），"
                "不是问「这台能不能打游戏」。"
                "模型被「他」这个指代词带偏，判成在问当前这台。"
                "正确理解：用户在给用途，系统该判 fit（够不够）或记下 purpose。"),
        "found": "chat-2026-11-17.log 第 13 条（用户实测）",
    },
    {
        "text": "那换一台吧",
        "wrong": {"intent": "ask_current", "topic": "none", "fit": False},
        "correct": {"intent": "refuse_switch", "topic": "none", "fit": False},
        "why": ("「换一台」是明确的换台请求，模型判成 ask_current 属于离谱错误。"
                "但注意：这句里有「那」+「吧」，是承接上一轮的口气，"
                "模型可能把它当成了「那（这台）怎么样吧」。"
                "判错的后果：系统没走换台流程，反而去问「是笔记本还是台式」，"
                "把已经说过的品类又问了一遍。"),
        "found": "chat-2026-11-17.log 第 14 条（用户实测）",
    },
]


def _fmt(d):
    """把 dict 转成 assistant 那一栏要的 JSON 字符串（键序固定）。"""
    return json.dumps({"intent": d["intent"], "topic": d["topic"],
                       "fit": d["fit"]}, ensure_ascii=False)


def load():
    out = []
    if os.path.exists(OUT):
        for line in io.open(OUT, encoding="utf-8"):
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def make_sample(text, correct, wrong=None, why="", found="", actionable=True):
    meta = {
        "correct_before": False,
        "why": why,
        "src": "correction:%s" % datetime.date.today().isoformat(),
        "found": found,
        "actionable": actionable,
    }
    if wrong:
        meta["wrong"] = _fmt(wrong)
    return {
        "messages": [
            {"role": "system", "content": SYS},
            {"role": "user", "content": text},
            {"role": "assistant", "content": _fmt(correct)},
        ],
        "_meta": meta,
    }


def validate(correct, wrong=None):
    """标签合法性检查 —— 非法标签进训练集会把模型带坏。"""
    errs = []
    if correct.get("intent") not in VALID_INTENT:
        errs.append("correct.intent 非法: %s" % correct.get("intent"))
    if correct.get("topic") not in VALID_TOPIC:
        errs.append("correct.topic 非法: %s" % correct.get("topic"))
    if not isinstance(correct.get("fit"), bool):
        errs.append("correct.fit 不是布尔")
    if wrong:
        if wrong.get("intent") not in VALID_INTENT:
            errs.append("wrong.intent 非法")
        if wrong.get("topic") not in VALID_TOPIC:
            errs.append("wrong.topic 非法")
        if correct == wrong:
            errs.append("correct 与 wrong 完全相同 —— 这条不是误判样本")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seed", action="store_true",
                    help="写入 2026-11-17 实测的 3 条误判")
    ap.add_argument("--add", action="store_true", help="交互式添加")
    args = ap.parse_args()

    os.makedirs(DIR, exist_ok=True)
    existing = load()

    if args.list or (not args.seed and not args.add):
        print("纠错样本：%d 条  (%s)" % (len(existing), OUT))
        if existing:
            print()
            for s in existing:
                u = s["messages"][1]["content"]
                a = json.loads(s["messages"][2]["content"])
                m = s.get("_meta") or {}
                print("  %-16s → %s/%s/fit=%s" % (
                    u[:14], a["intent"], a["topic"], a["fit"]))
                if m.get("wrong"):
                    w = json.loads(m["wrong"])
                    print("       模型错判：%s/%s/fit=%s" % (
                        w["intent"], w["topic"], w["fit"]))
        return

    if args.seed:
        have = {s["messages"][1]["content"] for s in existing}
        added = skipped = 0
        lines = []
        for item in SEED:
            if item["text"] in have:
                skipped += 1
                continue
            errs = validate(item["correct"], item["wrong"])
            if errs:
                print("  ✗ 跳过 %s：%s" % (item["text"], "; ".join(errs)))
                continue
            s = make_sample(item["text"], item["correct"], item["wrong"],
                            item["why"], item["found"],
                            item.get("actionable", True))
            lines.append(s)
            added += 1
            print("  ✓ %-14s 错误 %s/%s → 正确 %s/%s/fit=%s" % (
                item["text"], item["wrong"]["intent"], item["wrong"]["topic"],
                item["correct"]["intent"], item["correct"]["topic"],
                item["correct"]["fit"]))
        if lines:
            with io.open(OUT, "a", encoding="utf-8", newline="\n") as f:
                for s in lines:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print()
        print("新增 %d 条，跳过 %d 条（已存在）。总计 %d 条。"
              % (added, skipped, len(existing) + added))
        return

    if args.add:
        print("交互式添加（直接回车放弃）")
        text = input("用户原话: ").strip()
        if not text:
            print("放弃")
            return
        wi = input("模型判的 intent: ").strip()
        wt = input("模型判的 topic: ").strip()
        wf = input("模型判的 fit (true/false): ").strip().lower() == "true"
        ci = input("正确 intent: ").strip()
        ct = input("正确 topic: ").strip()
        cf = input("正确 fit (true/false): ").strip().lower() == "true"
        why = input("为什么错: ").strip()
        wrong = {"intent": wi, "topic": wt, "fit": wf}
        correct = {"intent": ci, "topic": ct, "fit": cf}
        errs = validate(correct, wrong)
        if errs:
            print("✗ 校验失败：%s" % "; ".join(errs))
            return
        s = make_sample(text, correct, wrong, why, "手工录入")
        with io.open(OUT, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print("✓ 已添加")
        # 🔴 重要提醒：`lora_data/` 被 .gitignore 排除（训练数据体积大、可重建），
        #    所以写进 corrections.jsonl 的样本**只在本地**，换机器就没了。
        #    真正持久化的做法是把它加进本文件的 SEED 常量。
        print()
        print("⚠️ 这条只在本地（lora_data/ 不入库）。")
        print("   要永久保留，请把这条加进 collect_corrections.py 的 SEED 常量，")
        print("   然后 git commit。SEED 里的条目跑 --seed 可重建。")


if __name__ == "__main__":
    main()
