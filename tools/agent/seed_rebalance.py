# -*- coding: utf-8 -*-
"""seed_rebalance.py —— 全局重采样，让 7 类意图分布均衡

## 问题

现有 697 条 + expand 480 条 = 1177 条，但分布严重失衡：
    state_new_need   451  (38%)
    ask_current      312  (27%)
    state_range      157  (13%)
    refuse_switch     84  (7%)
    analyze           86  (7%)
    list_others       50  (4%)
    unknown           37  (3%)

`unknown` 只有 37 条 —— 模型见得太少，一旦遇到没意义的话
就可能强行塞进某个类别，拉低整体准确率。

## 目标分布

按实际使用频率 + 学习难度折中：

    state_new_need   30%   （最常见：用户不断加条件）
    ask_current      22%
    state_range      16%
    refuse_switch    12%
    analyze          10%
    list_others       6%
    unknown           4%

为什么 unknown 只给 4%：真实对话里确实很少，
但 4% × 2000 = 80 条够模型学会"这个词不构成需求"。

## 做法

① 把所有来源合并
② 按意图分组
③ 目标 = 总数 × 目标比例
④ 超出目标的降采样（随机丢 + 保留唯一句）
⑤ 不足的从 expand 池补（如果池里有）

⚠️ 降采样必须去重后随机 —— 否则会丢长尾说法。
"""
import io
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8")

LORA = r"E:\电脑涨价分析\tools\agent\lora_data"

# 目标比例
TARGET = {
    "state_new_need": 0.30,
    "ask_current": 0.22,
    "state_range": 0.16,
    "refuse_switch": 0.12,
    "analyze": 0.10,
    "list_others": 0.06,
    "unknown": 0.04,
}


def load(fname):
    p = os.path.join(LORA, fname)
    if not os.path.exists(p):
        return []
    out = []
    for l in io.open(p, encoding="utf-8"):
        if not l.strip():
            continue
        try:
            r = json.loads(l)
        except Exception:
            continue
        msgs = r.get("messages") or []
        user = ""
        ans = ""
        for m in msgs:
            if m.get("role") == "user" and not user:
                user = m.get("content", "")
            elif m.get("role") == "assistant":
                ans = m.get("content", "")
        if not user or not ans:
            continue
        m = re.search(r'"intent"\s*:\s*"(\w+)"', ans)
        if not m:
            continue
        out.append((user, m.group(1), ans, r.get("_meta") or {}))
    return out


def main():
    rnd = random.Random(20261131)

    print("=" * 76)
    print("全局重采样：让 7 类意图分布均衡")
    print("=" * 76)
    print()

    # ① 合并所有来源
    SOURCES = [
        "route-train.jsonl",      # 现有全部（已含 desktop-build/boundary/scenario）
        "expand-seed.jsonl",      # 大规模扩充
        "minority-seed.jsonl",    # 小类补充
        "unknown-expand.jsonl",   # unknown 专项
        "gap-fill-seed.jsonl",    # 按缺口补齐
        "final-gaps-seed.jsonl",  # 最后一批（品牌换台/还有X吗/计数/指示词）
    ]
    pool = defaultdict(list)
    seen = set()
    for f in SOURCES:
        items = load(f)
        for user, intent, ans, meta in items:
            key = (user, intent)
            if key in seen:
                continue
            seen.add(key)
            pool[intent].append((user, intent, ans, meta, f))
        print("  %-28s %4d 条" % (f, len(items)))

    total_before = sum(len(v) for v in pool.values())
    print()
    print("  合并去重后 %d 条（唯一句子）" % total_before)
    print()
    print("  合并后分布:")
    for k, v in sorted(pool.items(), key=lambda x: -len(x[1])):
        print("    %-16s %4d  %5.1f%%"
              % (k, len(v), 100.0 * len(v) / total_before))
    print()

    # ② 决定目标总数
    # 用「能拿到的最大量」算：小类有多少用多少，大类降到比例
    max_possible = min(len(pool[k]) / TARGET[k] for k in pool if TARGET.get(k))
    target_total = int(max_possible)
    print("  按「最小类不超采」算，目标总数 = %d" % target_total)
    print()

    # ③ 按目标采样
    out = []
    print("  采样结果:")
    for intent, ratio in TARGET.items():
        items = pool.get(intent) or []
        want = int(target_total * ratio)
        if not items:
            print("    %-16s ✗ 没有样本！" % intent)
            continue
        if len(items) <= want:
            picked = items
            note = "（全部保留）"
        else:
            rnd.shuffle(items)
            picked = items[:want]
            note = "（从 %d 降采样）" % len(items)
        out.extend(picked)
        print("    %-16s %4d / 目标 %4d  %s"
              % (intent, len(picked), want, note))

    print()
    print("  最终 %d 条" % len(out))

    # ④ 打散顺序后写盘
    rnd.shuffle(out)
    rows = []
    for user, intent, ans, meta, src in out:
        rows.append({
            "messages": [
                {"role": "user", "content": user},
                {"role": "assistant", "content": ans},
            ],
            "_meta": dict(meta, balancedFrom=src),
        })

    # 写成一个新文件，build_route_lora 时优先用它
    p = os.path.join(LORA, "route-balanced.jsonl")
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print()
    final = Counter(r["messages"][1]["content"].split('"intent": "')[1]
                    .split('"')[0] for r in rows)
    print("  写盘后核对:")
    for k, v in final.most_common():
        print("    %-16s %4d  %5.1f%%" % (k, v, 100.0 * v / len(rows)))
    print()
    print("  写出 %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
