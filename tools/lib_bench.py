# -*- coding: utf-8 -*-
"""lib_bench.py —— CPU / GPU 跑分索引与名称归一化

为什么单独成文件：
  同一个 CPU 在不同来源里有十几种写法：
    R7-H255 / Ryzen 7 H255 / 锐龙7 H 255 / r7 h 255
    Ultra5-225H / Core Ultra 5 225H / ultra5 225h
  不做归一化，bench.json 里明明有数据，机型池却一条都对不上 ——
  实际踩过：33 台里初版只能命中个位数。

跑分口径（写死，避免混用）：
  · CPU：Cinebench R23 单核 / 多核（笔记本口径，插电、性能模式）
  · GPU：3DMark TimeSpy 显卡分（Graphics score，不是总分）
  · 同型号不同功耗释放差异大，宁可用区间也不用单点假装精确
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# 注意：这里最容易写错。踩过的坑 —— 先把 "ryzen" 当噪声删掉，
# 结果 "Ryzen 7 H255" 变成 "7h255"，而 bench 里的 "R7 H255" 是 "r7h255"，
# 同一颗 CPU 两边永远对不上。所以品牌词只能"替换"不能"删除"。
_CPU_REPL = (
    ("酷睿", "core"), ("锐龙", "ryzen"), ("英特尔", "intel"),
    ("ryzen", "r"), ("coreultra", "ultra"), ("core", ""),
    ("intel", ""), ("amd", ""), ("processor", ""), ("处理器", ""),
    ("第1代", ""), ("第2代", ""), ("gen", ""), ("plus", "plus"),
)

# GPU 品牌词一律去掉，只留型号
_GPU_DROP = ("nvidia", "geforce", "amd", "radeonrx", "laptop", "笔记本电脑",
             "显卡", "gpu", "笔记型", "移动版", "maxq", "满血版", "满血")

# 显式别名：归一化后仍对不上的靠这里兜底（键 = 归一化后的值）
_ALIAS = {
    "5210h": "core5210h",
    "5320": "core5320",
    "core5": "core5",
    "ultra5第1代": "ultra5125h",
    "ultra5": "ultra5125h",
}


def norm_cpu(s):
    s = (s or "").lower().replace("®", "").replace("™", "")
    for a, b in _CPU_REPL:
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]", "", s)
    return _ALIAS.get(s, s)


def norm_gpu(s):
    s = (s or "").lower().replace("®", "").replace("™", "")
    s = re.sub(r"[^a-z0-9]", "", s)
    for w in _GPU_DROP:
        s = s.replace(w, "")
    return s


def load_bench():
    return json.loads((DATA / "bench.json").read_text(encoding="utf-8"))


def cpu_index(bench=None):
    bench = bench or load_bench()
    idx = {}
    for c in bench.get("cpus") or []:
        idx[norm_cpu(c.get("name"))] = c
        for a in c.get("aliases") or []:
            idx[norm_cpu(a)] = c
    return idx


def gpu_index(bench=None):
    bench = bench or load_bench()
    idx = {}
    for g in bench.get("gpus") or []:
        idx[norm_gpu(g.get("name"))] = g
        for a in g.get("aliases") or []:
            idx[norm_gpu(a)] = g
    return idx


def cpu_bench(model, idx=None):
    idx = idx if idx is not None else cpu_index()
    return idx.get(norm_cpu(model))


def gpu_bench(model, idx=None):
    idx = idx if idx is not None else gpu_index()
    return idx.get(norm_gpu(model))


def cpu_line(model, idx=None):
    b = cpu_bench(model, idx)
    if not b:
        return None
    r23 = (b.get("bench") or {}).get("r23") or {}
    s, m = r23.get("single"), r23.get("multi")
    if not s and not m:
        return None
    parts = []
    if m:
        parts.append(f"多核 {m:,}")
    if s:
        parts.append(f"单核 {s:,}")
    return "Cinebench R23 " + " · ".join(parts)


def gpu_line(model, idx=None):
    b = gpu_bench(model, idx)
    if not b:
        return None
    ts = (b.get("bench") or {}).get("timeSpyGraphics")
    if not ts:
        return None
    return f"3DMark TimeSpy 显卡分 {ts:,}"
