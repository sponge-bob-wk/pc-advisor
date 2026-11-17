# -*- coding: utf-8 -*-
"""audit_matrix.py —— 四条输出路径的审计判据矩阵

## 它解决什么问题

这个项目因为「判据抄两遍一定会漂」吃过**七次**亏：

  1. compose() 有「参数当卖点」，answer_followup 漏了
  2. compose() 有「偷换推荐对象」，追问路径漏了
  3. _answer_current 完全没有审计（2026-11-17 才发现）
  4. _power_guess 只加在 _answer_current，另三条路径同样会编功耗
  5. check_fabrication 在 followup 路径完全没有
  6. _warranty_stretch 只在 compose 有
  7. _overspec 在 followup 漏了

**每次都是"改了一条路径就以为改好了"。**

根因：判据散在四个函数里，没有一个地方能一眼看出"谁有谁没有"。

## 这个脚本做什么

把四条路径各自用到的判据列成矩阵，**缺的用 `·` 标出来**。
一眼就能看出哪条路径把关最松。

## 用法

    python audit_matrix.py            # 打印矩阵
    python audit_matrix.py --gaps     # 只列缺口
    python audit_matrix.py --strict   # 有缺口就退出码 1（可接进 CI）

## ⚠️ 它是静态分析，不是运行时检查

靠读源码 + 正则找判据调用。加判据时如果写法太特殊（比如先赋值给变量
再调用），可能识别不到 —— 那时手工补进 `KNOWN` 表即可。
"""
import argparse
import inspect
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import agent  # noqa: E402

# 四条输出路径：(函数名, 审计函数名或 None)
PATHS = [
    ("compose", "_audit"),
    ("answer_followup", "_f_audit"),
    ("_answer_current", None),      # 判据内联在 _bad_answer 里
    ("_intro_model", None),
]

# 判据函数名前缀：这些才算"审计判据"，其他是工具函数
JUDGE_PREFIX = ("_", "check_", "verify_")
# 排除：工具/构造类函数，不是判据
NOT_JUDGE = {
    "_clean_caution", "_clean_brand_note", "_corpus_of", "_major_brief",
    "_price_label", "_asked_purpose", "_fit_template", "_answer_current",
    "_intro_model", "_bad_answer", "_f_audit", "_audit", "_merge_offer",
    "_screen_of", "_topic_of", "_topic_facts", "_is_fit_question",
    "_is_switch_request", "_last_route", "_money", "_slots_brief",
}


def _extract_judges(fn_name, inner=None):
    """从函数源码里抽判据调用名。"""
    f = getattr(agent, fn_name, None)
    if not f:
        return set()
    try:
        src = inspect.getsource(f)
    except OSError:
        return set()
    # 如果有独立审计函数，只看那一段（避免把业务逻辑函数也算进去）
    if inner:
        m = re.search(r"def " + re.escape(inner) + r"\(.*?\n(.*?)\n    [a-zA-Z]",
                      src, re.S)
        if m:
            src = m.group(1)
    # 抽所有 xxx( 形式的调用
    calls = set(re.findall(r"([A-Za-z_]\w*)\(", src))
    out = set()
    for c in calls:
        if c in NOT_JUDGE:
            continue
        if not c.startswith(JUDGE_PREFIX):
            continue
        # 过滤 Python 内置和明显的非判据
        if c in ("len", "str", "int", "dict", "list", "set", "any", "all",
                 "bool", "range", "print", "getattr", "isinstance"):
            continue
        # 必须真的是模块里的函数
        if not callable(getattr(agent, c, None)):
            continue
        out.add(c)
    return out


def build_matrix():
    cols = []
    for fn, inner in PATHS:
        cols.append((fn, _extract_judges(fn, inner)))
    all_judges = set()
    for _fn, js in cols:
        all_judges |= js
    return cols, sorted(all_judges)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gaps", action="store_true", help="只列缺口")
    ap.add_argument("--strict", action="store_true",
                    help="有缺口时退出码 1（接 CI 用）")
    args = ap.parse_args()

    cols, judges = build_matrix()
    names = [fn for fn, _ in cols]
    width = max(len(j) for j in judges) if judges else 10

    gaps = []
    if not args.gaps:
        print("四条输出路径的审计判据矩阵")
        print("=" * (width + 4 + len(names) * 12))
        header = " " * (width + 2) + "".join("%-12s" % n[:11] for n in names)
        print(header)
        print("-" * len(header))
        for j in judges:
            row = ["✓" if j in js else "·" for _fn, js in cols]
            mark = "" if all(r == "✓" for r in row) else "  ← 有缺口"
            print("  %-*s  %s%s" % (width, j,
                                     "".join("%-12s" % r for r in row), mark))
        print()

    for j in judges:
        missing = [n for n, js in zip(names, [js for _fn, js in cols]) if j not in js]
        if missing:
            gaps.append((j, missing))

    print("=" * 60)
    print("缺口汇总：%d 个判据没有覆盖全部四条路径" % len(gaps))
    print("=" * 60)
    for j, missing in gaps:
        print("  %-22s 缺：%s" % (j, "、".join(missing)))

    if not gaps:
        print("  ✓ 所有判据四条路径都覆盖了")

    print()
    print("说明：")
    print("  · `·` 表示这条路径没有这个判据 —— 同样的错误在那条路径上不会被拦")
    print("  · 不是所有判据都必须在所有路径上（比如 _tangent 只对追问有意义）")
    print("  · 但 `check_fabrication` / `_overspec` / `_power_guess` 这类")
    print("    「不许编造」的判据应该全路径覆盖")

    if args.strict and gaps:
        sys.exit(1)


if __name__ == "__main__":
    main()
