# -*- coding: utf-8 -*-
"""把「只有提问、没有回答」的存量补成样本（自蒸馏，2026-11-13 新增）。

## 它解决的问题

`export_lora_data.py` 会导出两个池子：
  · `train.jsonl`      有问答对的（现在是 58 条）
  · `pending.jsonl`    只有提问的（143 条，来自测试集里"听起来像真人"的那些）

用户原话：「测试的话记得同时可以把一些数据当作后续 lora 的样本集」。
这些提问本来就是真人语气写的，缺的只是**现在这版系统的回答** ——
拿修复后的系统重跑一遍，就得到一条干净训练对（《样本积攒提示词》§2 动作 A）。

## 三条纪律（违反则这条样本作废）

1. **答案只来自本系统**（`agent.run`），不调外部模型 —— 外部答案带着别人的机型和价格
2. **只收"有实质回答"的轮次**：recommend / followup / compare / fit 才算；
   clarify（反问）、refuse（拒答）、social（社交寒暄）都不算答案样本
3. **带 `_src` 可追溯**：`harvest:pending:<YYYY-MM-DD>`，将来整批能撤

## 用法

    python harvest_samples.py --limit 10      # 先试 10 条看效果
    python harvest_samples.py                 # 全量（143 条，约 8-10 分钟）
    python harvest_samples.py --dry           # 只统计会处理多少条
"""
import argparse
import datetime
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import agent  # noqa: E402

DIR = os.path.join(HERE, "lora_data")
TRAIN = os.path.join(DIR, "train.jsonl")
PENDING = os.path.join(DIR, "pending.jsonl")

GOOD_STAGES = ("recommend", "followup", "compare", "fit")
MIN_REPLY = 30


def _norm(t):
    return re.sub(r"\s+", "", t or "")


def load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    pending = load_jsonl(PENDING)
    train = load_jsonl(TRAIN)
    have = {_norm((r.get("messages") or [{}])[0].get("content", "")) for r in train}

    todo = [r for r in pending
            if _norm((r.get("messages") or [{}])[0].get("content", "")) not in have]
    print("待补提问 %d 条（已有问答对 %d 条）" % (len(todo), len(have)))
    if args.limit:
        todo = todo[:args.limit]
        print("本次只跑 %d 条" % len(todo))
    if args.dry:
        return

    today = datetime.date.today().isoformat()
    kept, skipped = 0, {}
    with io.open(TRAIN, "a", encoding="utf-8") as f:
        for i, row in enumerate(todo, 1):
            q = (row.get("messages") or [{}])[0].get("content", "").strip()
            if not q:
                continue
            try:
                r = agent.run(q)
            except Exception as e:
                skipped["异常"] = skipped.get("异常", 0) + 1
                print("[%d/%d] 异常 %s %s" % (i, len(todo), q[:16], e))
                continue
            stage = r.get("stage")
            reply = (r.get("reply") or "").strip()
            # 只有"真的回答了"才算样本；反问/拒答/寒暄不是答案
            if stage not in GOOD_STAGES or len(reply) < MIN_REPLY:
                skipped[stage or "空"] = skipped.get(stage or "空", 0) + 1
                continue
            # 出口审计报过问题的轮次也不收（铁律 2：知道自己错了的不进训练集）
            if r.get("fabricated_words") or r.get("outside_products"):
                skipped["审计命中"] = skipped.get("审计命中", 0) + 1
                continue
            f.write(json.dumps({
                "messages": [{"role": "user", "content": q},
                             {"role": "assistant", "content": reply[:1200]}],
                "_src": "harvest:pending:%s" % today,
            }, ensure_ascii=False) + "\n")
            kept += 1
            if i % 10 == 0:
                print("[%d/%d] 已收 %d 条" % (i, len(todo), kept))
            time.sleep(args.sleep)

    print("\n本次新增 %d 条；跳过明细：%s" % (kept, skipped or "无"))
    print("现在 train.jsonl 共 %d 条" % (len(load_jsonl(TRAIN))))


if __name__ == "__main__":
    main()
