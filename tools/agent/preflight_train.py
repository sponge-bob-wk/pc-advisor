# -*- coding: utf-8 -*-
"""preflight_train.py —— 训练前安全检查（防蓝屏）

## 为什么必须有这个

2026-11-22 18:56 蓝屏（0x0000003B SYSTEM_SERVICE_EXCEPTION）。
查系统日志发现根因**不是显卡驱动**：

    18:42:39  虚拟内存不足：python.exe 用了 10.2 GB
    18:44:54  虚拟内存不足：python.exe 用了 10.6 GB
    18:47:16  虚拟内存不足：python.exe 用了 10.6 GB
    18:50:04  虚拟内存不足：python.exe 用了 11.0 GB
    18:50:44  SQL Server CEIP 服务意外终止
    18:56:02  蓝屏 0x3B

训练进程吃 11 GB，物理内存只有 15.2 GB，加上其他程序耗尽
→ 内核分配内存失败 → 蓝屏。

**这不是"运气不好"，是可预防的。**

## 判据

训练峰值内存实测约 11 GB（含 4bit 模型加载 + 优化器状态 + 梯度检查点）。
安全线：
    可用内存 >= 11 GB  → 可以跑
    可用内存 >= 9 GB   → 勉强，建议先关程序
    可用内存 <  9 GB   → **拒绝运行**，先腾内存

## 用法

    python preflight_train.py            # 检查
    python preflight_train.py --force    # 忽略检查（不推荐）
"""
import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 训练峰值内存（GB）。实测值，不是估的。
PEAK_NEED_GB = 11.0
SAFE_GB = 11.0
TIGHT_GB = 9.0

# 建议关闭的程序（按占用排序，只是提示，不强制）
CLOSE_HINT = [
    ("Fusion360", "三维建模，很吃内存"),
    ("sqlservr", "SQL Server，训练用不到"),
    ("msedge", "浏览器"),
    ("firefox", "浏览器"),
    ("Weixin", "微信"),
    ("lceda-pro", "立创EDA"),
    ("ollama", "Ollama 推理服务（还占显存）"),
    ("llama-server", "Ollama 后端"),
]


def mem_info():
    """返回 (总内存 GB, 可用内存 GB, 已用百分比)。"""
    try:
        import psutil
        v = psutil.virtual_memory()
        return v.total / 1024 ** 3, v.available / 1024 ** 3, v.percent
    except ImportError:
        # 退化到 Windows API
        import ctypes

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

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return (st.ullTotalPhys / 1024 ** 3,
                st.ullAvailPhys / 1024 ** 3,
                float(st.dwMemoryLoad))


def big_procs(top=10):
    """占内存最多的进程。"""
    try:
        import psutil
    except ImportError:
        return []
    rows = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            mi = p.info.get("memory_info")
            if mi:
                rows.append((mi.rss / 1024 ** 3, p.info["name"]))
        except Exception:
            continue
    rows.sort(reverse=True)
    # 合并同名进程
    merged = {}
    for gb, name in rows:
        merged[name] = merged.get(name, 0) + gb
    return sorted(((v, k) for k, v in merged.items()), reverse=True)[:top]


def vram_info():
    """GPU 显存占用（MB）。"""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="忽略内存检查（不推荐）")
    args = ap.parse_args()

    print("=" * 64)
    print("训练前安全检查")
    print("=" * 64)

    total, avail, pct = mem_info()
    print()
    print("【内存】")
    print("  物理总计: %.1f GB" % total)
    print("  当前可用: %.1f GB（已用 %d%%）" % (avail, pct))
    print("  训练需要: 约 %.0f GB（实测峰值，不是估的）" % PEAK_NEED_GB)
    print("  余量:     %+.1f GB" % (avail - PEAK_NEED_GB))

    used_v, total_v = vram_info()
    if used_v is not None:
        print()
        print("【显存】")
        print("  已用 %d / %d MiB（训练需约 6.4 GB）" % (used_v, total_v))
        print("  余量: %+.1f GB" % ((total_v - used_v) / 1024 - 6.4))

    procs = big_procs(10)
    if procs:
        print()
        print("【占内存最多的进程】")
        for gb, name in procs:
            hint = ""
            for k, why in CLOSE_HINT:
                if k.lower() in name.lower():
                    hint = "  ← %s" % why
                    break
            print("  %5.2f GB  %-22s%s" % (gb, name[:22], hint))

    print()
    print("=" * 64)

    # ── 判定 ──
    if avail >= SAFE_GB:
        print("✓ 内存充足，可以开始训练")
        return 0

    if avail >= TIGHT_GB:
        print("⚠️  内存偏紧（%.1f GB < %.0f GB 安全线）" % (avail, SAFE_GB))
        print()
        print("  建议先关掉上面标了原因的程序。")
        print("  如果坚持训练，蓝屏风险较高 —— 2026-11-22 就是这么崩的。")
        if args.force:
            print()
            print("  （--force 已指定，继续）")
            return 0
        return 1

    # 不足
    print("✗ 内存不足，**拒绝训练**")
    print()
    print("  可用 %.1f GB < 最低要求 %.0f GB" % (avail, TIGHT_GB))
    print()
    print("  2026-11-22 18:56 的蓝屏就是训练进程吃 11 GB、")
    print("  物理内存只有 15.2 GB 导致的（系统日志有 4 次虚拟内存不足记录）。")
    print()
    print("  请先关闭这些程序再试：")
    for k, why in CLOSE_HINT:
        print("    · %-16s %s" % (k, why))
    print()
    print("  或者重启电脑（蓝屏后驱动可能锁着内存，重启能释放）。")
    if args.force:
        print()
        print("  （--force 已指定，但仍然强烈建议先腾内存）")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
