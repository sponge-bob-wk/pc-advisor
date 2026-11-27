# -*- coding: utf-8 -*-
"""eval_real_crawl.py —— 拿真实首句集跑一遍当前模型，留基线。

⚠ 只读，不改数据文件。要对比「补数据前 / 补数据后」就看这个数。

用法
    python eval_real_crawl.py                # 跑全量
    python eval_real_crawl.py --limit 50     # 先抽 50 条看看
"""
import argparse
import io
import json
import os
import sys
import time
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import intent_layer as il                                    # noqa: E402

# 🔴 默认只评**保持集**（不进训练的那 1/4）。
#    拿全量评会在重训后虚高 —— 那些句子模型已经见过了。
IN = os.path.join(HERE, "lora_data", "real-20261127.holdout.jsonl")
FOCUS = "zol-2103842"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true", help="评全量（重训后会虚高）")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    src = IN if not args.all else os.path.join(HERE, "lora_data",
                                              "real-20261127.jsonl")
    rows = [json.loads(l) for l in io.open(src, encoding="utf-8") if l.strip()]
    if args.limit:
        rows = rows[:args.limit]

    print("=" * 74)
    print("真实首句集 · %d 条" % len(rows))
    print("=" * 74)

    ok, bad, per, conf = 0, [], defaultdict(lambda: [0, 0]), Counter()
    t0 = time.time()
    for i, r in enumerate(rows, 1):
        il._CACHE.clear()
        focus = FOCUS if r.get("focus") else None
        try:
            got = il.route(r["text"], focus=focus).get("intent")
        except Exception as e:                             # noqa: BLE001
            got = "ERR:%s" % type(e).__name__
        r["got"] = got
        r["ok"] = (got == r["expect"])
        per[r["expect"]][1] += 1
        conf[(r["expect"], got)] += 1
        if r["ok"]:
            ok += 1
            per[r["expect"]][0] += 1
        else:
            bad.append(r)
        if i % 20 == 0:
            print("  ... %d/%d  用时 %.0fs" % (i, len(rows), time.time() - t0))

    print("\n  意图准确率: %d/%d = %.1f%%" % (ok, len(rows), 100.0 * ok / len(rows)))
    print("\n  各意图:")
    for k, (c, t) in sorted(per.items(), key=lambda x: -x[1][1]):
        print("    %-16s %3d/%-3d %5.1f%%" % (k, c, t, 100.0 * c / t))

    print("\n  主要混淆（真值→实际，只列 ≥2）:")
    for (e, g), n in conf.most_common():
        if e != g and n >= 2:
            print("    %-15s → %-15s %d" % (e, g, n))

    print("\n  判错明细（前 40）:")
    for r in bad[:40]:
        print("    %-34s 期望%-14s 实际%s" % (r["text"][:32], r["expect"], r["got"]))

    if args.out:
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("\n  明细写到 %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
