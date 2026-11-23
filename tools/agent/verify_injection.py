# -*- coding: utf-8 -*-
"""verify_injection.py —— 彻底检查注入是否损坏了原代码

## 为什么需要这个

`inject_guard.py` 的注入逻辑有个缺陷：
它在 `from_pretrained` 那一行之前插入守门代码，但如果那行在
`try:` 块里（缩进较深），插入后会变成：

    try:
        <守门>
    except ImportError:
        pass
            tok = AutoTokenizer.from_pretrained(...)   ← 被吞进 except

导致 `UnboundLocalError`。

## 检查什么

对每个脚本：
  ① 语法能否解析
  ② `except ImportError:` 的下一行**缩进是否比 except 深**
     （深 = 被吞进去了 = 坏）
  ③ 关键变量（tok / model）是否有赋值
"""
import ast
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

D = r"E:\电脑涨价分析\tools\agent"

TARGETS = [
    "train_route_lora.py", "eval_lora.py", "measure_real.py",
    "measure_mem.py", "probe_maxlen.py",
    "debug_zero_loss2.py", "debug_zero_loss3.py", "debug_zero_loss4.py",
]


def check(fn):
    p = os.path.join(D, fn)
    if not os.path.exists(p):
        return "文件不存在", None

    src = io.open(p, encoding="utf-8").read()

    # ① 语法
    try:
        ast.parse(src)
    except SyntaxError as e:
        return "语法错误 L%d: %s" % (e.lineno, e.msg), None

    lines = src.split("\n")
    problems = []

    # ② 检查所有 except ImportError 的下一行
    for i, ln in enumerate(lines):
        if "except ImportError:" not in ln:
            continue
        indent = len(ln) - len(ln.lstrip())
        # 看后面几行
        for j in range(i + 1, min(i + 4, len(lines))):
            nxt = lines[j]
            if not nxt.strip():
                continue
            nindent = len(nxt) - len(nxt.lstrip())
            # pass 是正常的
            if nxt.strip().startswith("pass"):
                continue
            # 缩进比 except 深 = 被吞进去
            if nindent > indent:
                problems.append(
                    "L%d 被吞进 except ImportError（缩进 %d > %d）: %s"
                    % (j + 1, nindent, indent, nxt.strip()[:50]))
            break

    # ③ 关键变量赋值检查
    for var in ("tok", "model"):
        # 只在有守门代码的脚本里检查
        if "from safe_run import" not in src:
            continue
        has_assign = bool(re.search(r"^\s*%s\s*=" % var, src, re.M))
        if not has_assign:
            problems.append("变量 %s 从未赋值" % var)

    if problems:
        return "有问题", problems
    return "✓ 正常", None


def main():
    print("=== 注入完整性检查 ===")
    bad = 0
    for fn in TARGETS:
        status, problems = check(fn)
        print("  %-24s %s" % (fn, status))
        if problems:
            bad += 1
            for x in problems:
                print("      %s" % x)

    print()
    if bad:
        print("✗ %d 个脚本有问题" % bad)
        return 1
    print("✓ 全部正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
