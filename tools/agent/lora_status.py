# -*- coding: utf-8 -*-
"""lora_status.py —— 训练集盘点：够不够训、还缺什么

## 为什么要单独一个脚本

`export_lora_data.py` 只统计它自己认识的那几份来源，而 2026-11-16 之后
`lora_data/` 下已经有 8 份 jsonl、来源各不相同（真实日志 / 测试即采样 /
意图数据集 / 对抗采样）。靠人肉数容易重复计数或漏算。

这个脚本做三件事：
1. **按来源分列**每份文件有多少条**可用训练样本**（必须有三段 messages）；
2. **跨文件去重**（同一句话重复出现在两个文件里，只能算一条，
   否则训练时会过采样同一句）；
3. 对照基线给结论：够不够训、缺多少、缺的是哪一类。

## 判定标准（进训练集的最低要求）

- `messages` 三段：system / user / assistant
- assistant 内容**非空**且不是「我不知道」这类拒答模板
- user 内容去重后唯一

## 用法

    python lora_status.py
    python lora_status.py --json     # 机器可读
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
LORA_DIR = os.path.join(HERE, "lora_data")

# 拒答/空壳模板：这类 assistant 内容没有训练价值
_REFUSAL = re.compile(r"(我不知道|无法回答|抱歉|不清楚|暂无数据|no data)", re.I)

# 风格对齐 LoRA 的经验下限
TARGET_MIN, TARGET_GOOD = 500, 1000


def norm_q(s):
    """问题归一：去空白、去标点，用于跨文件去重。"""
    return re.sub(r"[\s，。？！、；：,.?!;:]+", "", str(s or "")).strip().lower()


def load_file(path):
    """读一份 jsonl，返回可用样本列表。格式不认的跳过并计数。"""
    good, skipped = [], 0
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            msgs = r.get("messages")
            if not isinstance(msgs, list) or len(msgs) < 2:
                skipped += 1
                continue
            by_role = {}
            for m in msgs:
                if isinstance(m, dict) and m.get("role"):
                    by_role[m["role"]] = (m.get("content") or "").strip()
            u, a = by_role.get("user"), by_role.get("assistant")
            if not u or not a:
                skipped += 1
                continue
            if _REFUSAL.search(a):
                skipped += 1
                continue
            good.append({"user": u, "assistant": a, "system": by_role.get("system", ""),
                         "src": os.path.basename(path), "schema": schema_of(a)})
    except Exception as e:  # noqa: BLE001
        return [], 0, str(e)
    return good, skipped, ""


# ── 标签体系（决定能不能混训）────────────────────────────────────────
# 🔴 这个项目里**不止一套意图标签**，混在一起训等于教模型输出错误结构：
#      路由层（intent_layer）：{"intent","topic","fit"}      ← 对抗采样产出的
#      预算层（budget_intent）：{"budget_action"}            ← intent_train.jsonl
#      其他（slot/…）：别的键
#    所以盘点时必须**按 schema 分开**，否则「1748 条可用」是个假数。
SCHEMA_LABEL = {
    "route": "路由层 intent/topic/fit",
    "budget": "预算层 budget_action",
    "other": "其他/未知",
}


def schema_of(assistant):
    """从 assistant 的 JSON 里认出属于哪套标签体系。"""
    try:
        d = json.loads(assistant)
    except (json.JSONDecodeError, TypeError):
        return "other"
    if not isinstance(d, dict):
        return "other"
    keys = set(d)
    if {"intent", "topic", "fit"} & keys:
        return "route"
    if "budget_action" in keys:
        return "budget"
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(LORA_DIR, "*.jsonl")))
    if not files:
        print("lora_data/ 下没有 jsonl。先跑 build_intent_dataset.py 或 adversarial_harvest.py")
        return

    per_file = {}
    all_rows = []
    for f in files:
        good, skipped, err = load_file(f)
        per_file[os.path.basename(f)] = {"good": len(good), "skipped": skipped, "err": err}
        all_rows.extend(good)

    # 跨文件去重（同一句问话只留第一条，来源合并记录）
    seen = {}
    for r in all_rows:
        k = norm_q(r["user"])
        if not k:
            continue
        if k in seen:
            seen[k]["dups"].append(r["src"])
        else:
            r["dups"] = []
            seen[k] = r
    uniq = list(seen.values())

    if args.json:
        print(json.dumps({
            "files": per_file,
            "rawRows": len(all_rows),
            "unique": len(uniq),
            "targetMin": TARGET_MIN,
            "targetGood": TARGET_GOOD,
            "gap": max(0, TARGET_MIN - len(uniq)),
        }, ensure_ascii=False, indent=2))
        return

    print("=" * 68)
    print("① 按文件（可用样本 / 跳过）")
    print("=" * 68)
    for name, st in per_file.items():
        flag = "" if not st["err"] else "  !! %s" % st["err"][:40]
        print("  %-28s %5d 可用   %4d 跳过%s" % (name, st["good"], st["skipped"], flag))

    print()
    print("=" * 68)
    print("② 跨文件去重")
    print("=" * 68)
    print("  合计行数      : %d" % len(all_rows))
    print("  去重后唯一问句 : %d" % len(uniq))
    dup_files = [r for r in uniq if r["dups"]]
    print("  有重复的问句   : %d 条（同句出现在多份文件里）" % len(dup_files))
    for r in dup_files[:5]:
        print("    「%s」← %s" % (r["user"][:20], "、".join([r["src"]] + r["dups"])))

    print()
    print("=" * 68)
    print("③ 够不够训（**按标签体系分开算**，混训会教错输出结构）")
    print("=" * 68)
    by_schema = defaultdict(list)
    for r in uniq:
        by_schema[r["schema"]].append(r)

    for schema, rows in sorted(by_schema.items(), key=lambda x: -len(x[1])):
        n = len(rows)
        label = SCHEMA_LABEL.get(schema, schema)
        if n >= TARGET_GOOD:
            verdict = "够训"
        elif n >= TARGET_MIN:
            verdict = "勉强够训，建议补到 %d" % TARGET_GOOD
        else:
            verdict = "不够，缺 %d 条到下限" % (TARGET_MIN - n)
        print("  %-28s %5d 条   %s" % (label, n, verdict))

    print()
    total = len(uniq)
    print("  全部去重合计  : %d 条" % total)
    print("  经验下限      : %d 条（低于此，模型只会背句子）" % TARGET_MIN)
    print("  稳妥区间      : %d 条以上" % TARGET_GOOD)

    # 按 assistant 的意图标签分布看覆盖（只看路由层）
    route_rows = by_schema.get("route", [])
    if route_rows:
        labs = Counter()
        for r in route_rows:
            try:
                d = json.loads(r["assistant"])
                labs[d.get("intent")] += 1
            except (json.JSONDecodeError, TypeError):
                labs["（解析失败）"] += 1
        print()
        print("④ 路由层样本的意图覆盖（%d 条）" % len(route_rows))
        for k, v in labs.most_common():
            print("  %-20s %d" % (k, v))
        missing = [x for x in ("ask_current", "refuse_switch", "state_range",
                               "state_new_need", "list_others", "unknown")
                   if x not in labs]
        if missing:
            print("  ⚠ 完全没有样本的标签：%s" % "、".join(missing))
        if len(route_rows) < TARGET_MIN:
            print("  → 路由层离下限还差 %d 条，这是当前最该补的方向。"
                  % (TARGET_MIN - len(route_rows)))


if __name__ == "__main__":
    main()
