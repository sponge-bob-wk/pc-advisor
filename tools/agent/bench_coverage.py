# -*- coding: utf-8 -*-
"""bench_coverage.py —— 跑分覆盖诊断：到底是缺数据还是归一化失配

## 为什么需要这个脚本

交接文档 §5.4 写着「剩余 69 台无跑分，**一半是 CPU 只写了系列名**」，
下一步计划是「回 ZOL 补具体型号」。

2026-11-16 实测**推翻了这条结论**：

    无跑分的 92 台里
      系列名（"酷睿i5 5代系列"）     1 台
      具体型号（"i5 12500H"）      90 台
      空                          1 台

所以"回 ZOL 补型号"这条路的收益接近零 —— 型号本来就是具体的。
真正的卡点在**归一化**：同一颗 CPU 有七八种写法，`_cpu_key` 没覆盖全。

    i5 12500H          -> i5 12500h        ✅
    i5-12500H          -> i5 12500h        ✅
    酷睿i5 12500H       -> corei5 12500h    ❌ 匹配不上
    英特尔 酷睿i5 12500H  -> 英特尔 酷睿i5 12500h ❌

这个脚本把「无跑分」拆成三类，让下一步知道该修哪：

    A. 归一化能救 —— 去掉前缀/别名后能在 bench.json 里找到
    B. 真的没这颗芯片 —— bench.json 里没有，要补跑分
    C. 型号本身不可用 —— 系列名/空/非 x86（麒麟等）

## 用法

    python bench_coverage.py              # 诊断
    python bench_coverage.py --fix-plan   # 输出可修的归一化映射（不改代码）
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# HERE = .../tools/agent → 上两级才是项目根（data/ 在根下）
DATA = os.path.join(os.path.dirname(os.path.dirname(HERE)), "data")

import core  # noqa: E402

# 需要在归一化里剥掉的噪声前缀/后缀。
# 🔴 这些不是"猜"，都是从实际数据里数出来的（见文件头）。
NOISE_PREFIX = [
    "英特尔 ", "英特尔", "AMD ", "AMD", "超微 ", "苹果 ",
    "酷睿", "Core ", "core ", "锐龙", "Ryzen ", "ryzen ",
]
NOISE_SUFFIX = [" 处理器", "处理器"]


def strip_noise(s):
    """剥掉厂商/系列前缀，得到可比的核心型号。"""
    t = str(s or "").strip()
    changed = True
    while changed:
        changed = False
        for p in NOISE_PREFIX:
            if t.startswith(p):
                t = t[len(p):].strip()
                changed = True
        for suf in NOISE_SUFFIX:
            if t.endswith(suf):
                t = t[: -len(suf)].strip()
                changed = True
    return t


def candidates(chip):
    """给一颗 CPU 生成「归一化后应该长什么样」的候选键。"""
    base = str(chip or "").strip()
    out = {core._cpu_key(base)}
    stripped = strip_noise(base)
    if stripped:
        out.add(core._cpu_key(stripped))
    # Ultra 系列：`Ultra 9 290HX Plus` 与 `U9-290HX` 是同一颗
    m = re.search(r"(?:ultra|u)\s*([3579])[\s\-]*(\d{3,5})\s*([A-Z]{0,3})", base, re.I)
    if m:
        out.add(("ultra %s %s%s" % (m.group(1), m.group(2), m.group(3))).lower())
    return {c for c in out if c}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix-plan", action="store_true")
    args = ap.parse_args()

    bench = json.load(open(os.path.join(DATA, "bench.json"), encoding="utf-8"))
    known = {}
    no_score = set()
    for c in bench.get("cpus") or []:
        # 🔴 芯片在表里 ≠ 有分。`perfCurrent` 为空的是「收录了但没实测分」，
        #    这类不能算「归一化能救」，否则会让人以为改归一化就能提升覆盖。
        #    实测：i7-13620H / i5-14450HX 都在表里，但 perfCurrent 都是 None。
        has_score = c.get("perfCurrent") is not None
        for k in {core._cpu_key(c.get("id")), core._cpu_key(c.get("name"))}:
            if not k:
                continue
            if has_score:
                known[k] = c
            else:
                no_score.add(k)
    print("bench.json 收录 %d 颗 CPU，归一化后 %d 个**有分**键（另有 %d 个无分）"
          % (len(bench.get("cpus") or []), len(known), len(no_score - set(known))))

    offs = core.pool_offers()
    noperf = [o for o in offs if not o.get("perf")]
    print("推荐池 %d 台，无跑分 %d 台（覆盖 %.1f%%）"
          % (len(offs), len(noperf), 100.0 * (len(offs) - len(noperf)) / len(offs)))

    A, B, C = [], [], []
    for o in noperf:
        chip = (o.get("chip") or "").strip()
        if not chip or "系列" in chip or re.search(r"(麒麟|Kirin|腾锐|飞腾|骁龙|天玑)", chip):
            C.append((o, chip))
            continue
        # 归一化能救？
        hit = None
        for cand in candidates(chip):
            if cand in known:
                hit = (cand, known[cand])
                break
        if hit:
            A.append((o, chip, hit))
        else:
            B.append((o, chip))

    print()
    print("=" * 70)
    print("A. 归一化能救        %d 台（改 _cpu_key 就能有跑分）" % len(A))
    print("B. 真的没这颗芯片    %d 台（要补跑分数据）" % len(B))
    print("C. 型号不可用        %d 台（系列名/空/非 x86）" % len(C))
    print("=" * 70)

    if A:
        print()
        print("A 类明细（当前写法 → 应该匹配到的芯片）：")
        seen = set()
        for o, chip, (k, c) in A:
            if chip in seen:
                continue
            seen.add(chip)
            print("  %-28s → %-28s (%s)" % (chip[:26], c.get("name", "")[:26], k))

    if B:
        print()
        print("B 类：bench.json 里没有的芯片（按台数排）")
        cnt = Counter(c for _o, c in B)
        for chip, n in cnt.most_common(20):
            print("  %-30s %d 台" % (chip[:28], n))
        print("  共 %d 颗不同芯片需要补跑分" % len(cnt))

    if C:
        print()
        print("C 类明细：")
        for o, chip in C[:12]:
            print("  %-24s %-30s" % (chip or "(空)", o["name"][:28]))

    if args.fix_plan and A:
        print()
        print("=" * 70)
        print("可加的归一化规则（供 core._cpu_key 参考，**不自动改代码**）")
        print("=" * 70)
        print("  需要剥掉的前缀：英特尔 / AMD / 酷睿 / 锐龙 / Core / Ryzen")
        print("  需要处理的形态：Ultra N → U N，`Ultra 9 290HX Plus` ≈ `U9-290HX`")
        print()
        print("  注意：剥前缀会改变**所有**机器的 cpu_key，")
        print("        必须跑全量测试确认没有连带影响（尤其去重和跑分匹配两处）。")


if __name__ == "__main__":
    main()
