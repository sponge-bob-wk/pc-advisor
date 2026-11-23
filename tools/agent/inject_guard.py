# -*- coding: utf-8 -*-
"""inject_guard.py —— 给所有加载模型的脚本注入安全守门

## 为什么批量注入

8 个脚本都会 `from_pretrained`。手工一个个加容易漏，
而**漏一个就可能再蓝屏一次**（代价：系统重启 + 丢工作）。

## 注入什么

在 `from_pretrained` 之前插入：

    from safe_run import check_before_load, mark_loaded
    check_before_load()

加载成功后（脚本末尾或紧接着）标记：

    mark_loaded()

但精确插入 `mark_loaded()` 比较难（要找到加载语句的结束）。
折中：**只在开头注入 check_before_load()**，
并用「同一进程最多一次」的计数在 safe_run 内部维护 ——
每次调用 check 就 +1（相当于"即将加载"）。

这样即使不调 mark_loaded，连续调用 check 也会被拦住。
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

D = r"E:\电脑涨价分析\tools\agent"

TARGETS = [
    "train_route_lora.py",
    "eval_lora.py",
    "measure_real.py",
    "measure_mem.py",
    "probe_maxlen.py",
    "debug_zero_loss2.py",
    "debug_zero_loss3.py",
    "debug_zero_loss4.py",
]

GUARD = '''
# ══════════════════════════════════════════════════════════════
# 🔴 安全守门（2026-11-23 加，防蓝屏）
#
# 已经蓝屏两次，都是加载模型时内存耗尽：
#   11-22 18:56  0x3B  训练跑到 31 步
#   11-23 16:17  0x1E  测试脚本连续加载 4 次模型
#
# 这个守门做两件事：
#   ① 加载前检查 commit 余量 >= 13 GB
#   ② **同一进程最多加载一次**（连续加载必蓝屏）
# ══════════════════════════════════════════════════════════════
try:
    from safe_run import check_before_load as _guard, mark_loaded as _mark
    _guard()
    _mark()
except ImportError:
    pass          # safe_run 不在时跳过（不阻塞脚本）
'''


def inject(fn):
    p = os.path.join(D, fn)
    if not os.path.exists(p):
        return "跳过（文件不存在）"
    src = io.open(p, encoding="utf-8").read()

    if "from safe_run import" in src:
        return "已有守门"

    # 找到第一个 from_pretrained（或其所在函数的开头）
    m = re.search(r"^(\s*)(\w+\s*=\s*)?(AutoModelForCausalLM|AutoTokenizer)"
                  r"\.from_pretrained", src, re.M)
    if not m:
        return "跳过（没找到 from_pretrained）"

    # 往前找这一行的行首，插在它所在的语句块之前
    idx = m.start()
    # 找这一行的缩进
    line_start = src.rfind("\n", 0, idx) + 1
    indent = re.match(r"\s*", src[line_start:]).group(0)

    # 构造带缩进的守门
    guard = "\n".join(
        (indent + ln if ln.strip() else ln)
        for ln in GUARD.strip("\n").split("\n")) + "\n" + indent

    new = src[:line_start] + guard + src[line_start:]
    io.open(p, "w", encoding="utf-8", newline="\n").write(new)
    return "已注入（缩进 %d 空格）" % len(indent)


def main():
    print("=== 注入安全守门 ===")
    for fn in TARGETS:
        print("  %-24s %s" % (fn, inject(fn)))


if __name__ == "__main__":
    main()
