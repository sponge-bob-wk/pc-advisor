# -*- coding: utf-8 -*-
r"""paths.py —— 集中管理路径，消除硬编码

## 为什么需要

体检发现 25 个文件里有 40+ 处硬编码绝对路径：

    D = r"E:\电脑涨价分析\tools\agent"
    MODEL_DIR = r"E:\models\Qwen3-4B-Instruct-2507"

**问题**：

  ① 换机器/换盘符就跑不了
  ② 论文里说「可复现」会打折扣 —— 别人拿到代码，路径全不对
  ③ 想换个模型目录要改 25 个文件

## 设计

**项目内的路径**（数据、代码）→ 从 `__file__` 推导，天然可移植：

    ROOT = <项目根>
    DATA = ROOT/data
    AGENT = ROOT/tools/agent

**项目外的路径**（模型权重、venv）→ 环境变量优先，有默认值：

    MODEL_DIR = os.environ.get("MODEL_DIR") or r"E:\models\Qwen3-4B-Instruct-2507"

这样：
  · 项目内的东西拷到哪都能跑
  · 项目外的东西可以用环境变量覆盖

## 用法

    from paths import DATA, AGENT, MODEL_DIR, pool_path

    p = pool_path()                    # data/machine_pool.json
    d = os.path.join(AGENT, "lora_v4")
"""
import sys

# 🔴 2026-11-26 加：强制 stdout 用 UTF-8。
#    实测：GBK 控制台跑 paths.py 崩在 L128 的 ✓（U+2713）。
#    不依赖 PYTHONIOENCODING 环境变量，代码里写死。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import os

# ── 项目根目录（从本文件位置推导，不写死）──
# 本文件在 <ROOT>/tools/agent/paths.py
_HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = _HERE                                    # tools/agent
TOOLS = os.path.dirname(AGENT)                   # tools
ROOT = os.path.dirname(TOOLS)                    # 项目根

# ── 项目内目录 ──
DATA = os.path.join(ROOT, "data")
DOCS = os.path.join(ROOT, "docs")
LORA_DATA = os.path.join(AGENT, "lora_data")

# ── 项目外路径（环境变量优先）──
# 模型权重：换模型时改环境变量即可，不用动代码
MODEL_DIR = (os.environ.get("MODEL_DIR")
             or r"E:\models\Qwen3-4B-Instruct-2507")

# Python 解释器（训练/评估要用带 CUDA 的那个）
# ⚠️ 不要用 sys.executable —— 如果从系统 python 启动，它会是 CPU 版
CUDA_PYTHON = (os.environ.get("CUDA_PYTHON")
               or r"E:\ai2026\Scripts\python.exe")


def data_path(name):
    """data/ 下的文件。"""
    return os.path.join(DATA, name)


def pool_path():
    """机型池。"""
    return data_path("machine_pool.json")


def bench_path():
    """跑分库。"""
    return data_path("bench_nbc.json")


def prices_path():
    """价格快照。"""
    return data_path("prices.json")


def majors_path():
    """专业表。"""
    return data_path("majors.json")


def adapter_path(version="lora_v4"):
    """LoRA adapter 目录。"""
    return os.path.join(AGENT, version)


def backup(path, tag=""):
    """给文件做带时间戳的备份，返回备份路径。

    🔴 项目铁律：**每次写数据文件都要备份**。
       这个函数统一做，避免每个脚本各写一遍（抄两遍会漂）。
    """
    import shutil
    import time
    suffix = ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    if tag:
        suffix += "-" + tag
    dst = path + suffix
    shutil.copy2(path, dst)
    return dst


if __name__ == "__main__":
    print("=== paths.py 解析结果 ===")
    print("  ROOT:        %s" % ROOT)
    print("  AGENT:       %s" % AGENT)
    print("  DATA:        %s" % DATA)
    print("  LORA_DATA:   %s" % LORA_DATA)
    print()
    print("  MODEL_DIR:   %s  %s"
          % (MODEL_DIR, "✓" if os.path.isdir(MODEL_DIR) else "✗ 不存在"))
    print("  CUDA_PYTHON: %s  %s"
          % (CUDA_PYTHON, "✓" if os.path.isfile(CUDA_PYTHON) else "✗ 不存在"))
    print()
    print("=== 关键文件 ===")
    for name, p in (("machine_pool", pool_path()),
                    ("bench_nbc", bench_path()),
                    ("prices", prices_path()),
                    ("majors", majors_path()),
                    ("lora_v4", adapter_path("lora_v4"))):
        ok = "✓" if os.path.exists(p) else "✗"
        print("  %-14s %s  %s" % (name, ok, p))
