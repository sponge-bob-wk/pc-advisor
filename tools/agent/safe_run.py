# -*- coding: utf-8 -*-
"""safe_run.py —— 加载模型前的安全守门（防蓝屏）

## 为什么必须独立一个模块

**已经蓝屏两次，都是同一个原因：加载模型时内存耗尽。**

| 时间 | 错误码 | 触发场景 |
|---|---|---|
| 2026-11-22 18:56 | `0x3B` | 训练跑到 31 步，commit 累积到 11 GB |
| 2026-11-23 16:17 | `0x1E` | **测试脚本连续加载 4 次模型** |

`0x1E KMODE_EXCEPTION_NOT_HANDLED` 和 `0x3B SYSTEM_SERVICE_EXCEPTION`
都是 `0xc0000005` 访问违规 —— 内核在分配内存时拿不到页。

## 关键教训

**每次加载 4bit 模型，commit 会涨约 11 GB。**
连续加载 N 次不会释放，因为：

  · PyTorch 的 CUDA 缓存不会立刻归还
  · Python 的 GC 不保证及时回收
  · **Windows 的 commit 是「已承诺」不是「已使用」，进程退出才真正归还**

所以**同一个进程里连续加载多个模型 = 自杀**。

## 守门规则

  ① 单次加载前检查 commit 余量 >= 13 GB
  ② **同一进程最多加载 1 次模型**（用全局标记拦住）
  ③ 需要多组对比时，必须**每次开新进程**

## 用法

    from safe_run import check_before_load, mark_loaded
    check_before_load()          # 不足会 raise SystemExit
    model = AutoModelForCausalLM.from_pretrained(...)
    mark_loaded()

或者在脚本里直接：

    python safe_run.py            # 只看当前能不能加载
"""
import ctypes
import sys

# 🔴 2026-11-26 加：**强制 stdout/stderr 用 UTF-8**。
#    Windows 控制台默认 GBK，打印 ⚠️/✓/✗ 这类字符会 UnicodeEncodeError 直接崩。
#    实测：server.py 第 588 行的 ⚠️ 导致服务起不来。
#    不依赖外部 PYTHONIOENCODING 环境变量，在代码里写死。
#    errors="replace" 是双保险 —— 宁可显示成 ? 也不能让脚本崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass



class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


# 每次加载 4bit 模型，commit 涨约 11 GB
LOAD_COMMIT_GB = 13.0
# 物理内存至少留这么多给系统
PHYS_FLOOR_GB = 2.5

# 同一进程已加载次数（防连续加载）
_LOADED = [0]


def mem():
    """返回 (commit 可用 GB, 物理可用 GB, 已用百分比)。"""
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return (st.ullAvailPageFile / 1024 ** 3,
            st.ullAvailPhys / 1024 ** 3,
            st.dwMemoryLoad)


def vram():
    """返回 (已用 MiB, 总 MiB) 或 (None, None)。"""
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8)
        used, total = r.stdout.strip().split(",")
        return int(used), int(total)
    except Exception:
        return None, None


def check_before_load(allow_repeat=False):
    """加载模型前调用。不满足条件就退出（防蓝屏）。

    allow_repeat: 允许同一进程重复加载（**极度不推荐**，只在确认内存
                  充足时用，且两次之间必须 del + gc + empty_cache）
    """
    commit_avail, phys_avail, pct = mem()
    v_used, v_total = vram()

    print("=== 加载前检查 ===")
    print("  commit 可用: %.1f GB（单次加载需 %.0f GB）"
          % (commit_avail, LOAD_COMMIT_GB))
    print("  物理可用:   %.1f GB（至少留 %.1f GB）"
          % (phys_avail, PHYS_FLOOR_GB))
    if v_used is not None:
        print("  显存:       %d / %d MiB" % (v_used, v_total))
    print("  本进程已加载模型: %d 次" % _LOADED[0])
    print()

    # ① 同一进程重复加载
    if _LOADED[0] >= 1 and not allow_repeat:
        print("✗ **同一进程已经加载过模型，拒绝再次加载**")
        print()
        print("  2026-11-23 16:17 蓝屏（0x1E）就是这么来的：")
        print("  一个测试脚本连续加载 4 次模型，每次 commit 涨 11 GB，")
        print("  累积到 44 GB > 提交上限 30 GB → 内核分配失败 → 蓝屏。")
        print()
        print("  PyTorch 的 CUDA 缓存不会立刻归还，Python GC 也不保证及时，")
        print("  **Windows 的 commit 是「已承诺」不是「已使用」，进程退出才归还。**")
        print()
        print("  修法：**每次开新进程**。需要多组对比就写 shell 循环：")
        print("    for case in a b c d; do python probe.py --case $case; done")
        raise SystemExit(1)

    # ② commit 不足
    if commit_avail < LOAD_COMMIT_GB:
        print("✗ **commit 不足，拒绝加载**")
        print()
        print("  可用 %.1f GB < 需要 %.0f GB" % (commit_avail, LOAD_COMMIT_GB))
        print()
        print("  关掉这些再试：")
        for n, why in (("msedge", "浏览器"), ("firefox", "浏览器"),
                       ("Weixin", "微信"), ("lceda-pro", "立创EDA"),
                       ("Fusion360", "三维建模"), ("sqlservr", "SQL Server"),
                       ("ollama", "推理服务（还占显存）")):
            print("    · %-16s %s" % (n, why))
        print()
        print("  或者重启电脑（commit 只有重启才完全归还）。")
        raise SystemExit(1)

    # ③ 物理不足（警告，不拦）
    if phys_avail < PHYS_FLOOR_GB:
        print("⚠️  物理内存偏少（%.1f GB），会大量换页，可能很慢" % phys_avail)

    print("  ✓ 可以加载")
    return True


def mark_loaded():
    """加载成功后调用，记一次数。"""
    _LOADED[0] += 1


def reset():
    """重置计数（只在确实释放了模型后调用）。"""
    _LOADED[0] = 0


if __name__ == "__main__":
    commit_avail, phys_avail, pct = mem()
    v_used, v_total = vram()
    print("=" * 60)
    print("加载模型前的安全状态")
    print("=" * 60)
    print("  commit 可用: %.1f GB" % commit_avail)
    print("  物理可用:   %.1f GB" % phys_avail)
    print("  内存占用:   %d%%" % pct)
    if v_used is not None:
        print("  显存:       %d / %d MiB（空闲 %d）"
              % (v_used, v_total, v_total - v_used))
    print()
    if commit_avail >= LOAD_COMMIT_GB:
        print("✓ 可以加载一个模型（commit 余量 %+.1f GB）"
              % (commit_avail - LOAD_COMMIT_GB))
    else:
        print("✗ commit 不足（差 %.1f GB）—— 先关程序或重启"
              % (LOAD_COMMIT_GB - commit_avail))
