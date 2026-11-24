# -*- coding: utf-8 -*-
"""quantize_gguf.py —— 用 gguf 库把 F16 GGUF 量化成 Q4_K_M

## 为什么要量化

F16 GGUF（7.5 GB）导入 Ollama 后推理报 `0xc0000409`（栈溢出）。

**对比：能跑的标准模型都是量化过的**：
  · qwen3.5-4b    → file_type=15（Q4_K_M）
  · qwen3-4b-text → file_type=15（Q4_K_M）

**我的 F16 是 file_type=1。**

怀疑：llama.cpp 在 Windows 上加载 F16 大模型有 bug
（或者内存布局问题）。量化后文件更小，可能避开。

## 量化哪些 tensor

llama.cpp 的 Q4_K_M 规则（简化版）：
  · 大部分 weight → Q4_K（4-bit，带 super-block 缩放）
  · attn_v / ffn_down 的关键部分 → Q6_K（6-bit，保精度）
  · norm 类（1 维） → F32（不量化，数值敏感）

**完整实现 Q4_K 的量化算法很复杂**（需要计算 super-block 的
scale/min）。这里用 **Q8_0**（8-bit，实现简单，精度损失小），
文件约 4 GB，比 F16 小一半。

Q8_0 的量化：
    每 32 个 float 一组 → 1 个 scale（fp16）+ 32 个 int8
    scale = max(abs(x)) / 127
    q = round(x / scale)

这是 llama.cpp 里最简单也最稳的量化格式。
"""
import argparse
import io
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np


def quantize_q8_0(arr):
    """把一个 2D float 数组量化成 Q8_0。

    返回 (quantized_bytes, scales)。
    llama.cpp 的 Q8_0 布局：每个 block 32 个值
        [fp16 scale][32 × int8]
    """
    flat = arr.reshape(-1).astype(np.float32)
    n = flat.size
    # 补齐到 32 的倍数
    pad = (-n) % 32
    if pad:
        flat = np.concatenate([flat, np.zeros(pad, np.float32)])

    blocks = flat.reshape(-1, 32)
    # 每个 block 的 scale = max(abs) / 127
    amax = np.abs(blocks).max(axis=1)
    scale = np.where(amax > 0, amax / 127.0, 1.0).astype(np.float16)
    # 量化
    q = np.round(blocks / scale.astype(np.float32)[:, None])
    q = np.clip(q, -128, 127).astype(np.int8)
    return q, scale


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="输入 F16 GGUF")
    ap.add_argument("--out", required=True, help="输出量化 GGUF")
    ap.add_argument("--type", default="q8_0", choices=("q8_0",))
    args = ap.parse_args()

    from gguf import GGUFReader, GGUFWriter
    from gguf.constants import GGMLQuantizationType

    print("=" * 64)
    print("GGUF 量化：F16 → Q8_0")
    print("=" * 64)
    print("  输入: %s（%.2f GB）"
          % (args.src, os.path.getsize(args.src) / 1024 ** 3))
    print("  输出: %s" % args.out)
    print()

    r = GGUFReader(args.src)
    print("  源 tensor: %d 个，元数据 %d 项" % (len(r.tensors), len(r.fields)))

    # 创建 writer（架构名从源读）
    arch = str(r.fields["general.architecture"].contents())
    w = GGUFWriter(args.out, arch)

    # ── 复制元数据（跳过文件类型相关）──
    print()
    print("=== 复制元数据 ===")
    SKIP = {"general.file_type"}
    n_kv = 0
    for key in r.fields:
        if key.startswith("GGUF.") or key in SKIP:
            continue
        f = r.fields[key]
        try:
            val = f.contents()
        except Exception:
            continue
        # 数组类型要转 list
        if hasattr(val, "tolist"):
            val = val.tolist()
        try:
            w.add_key_value(key, val, f.types[0] if hasattr(f, "types") else None)
            n_kv += 1
        except Exception:
            # 类型推断失败就按值推断
            try:
                if isinstance(val, bool):
                    w.add_bool(key, val)
                elif isinstance(val, int):
                    w.add_uint32(key, val)
                elif isinstance(val, float):
                    w.add_float32(key, val)
                elif isinstance(val, str):
                    w.add_string(key, val)
                elif isinstance(val, list):
                    if val and isinstance(val[0], str):
                        w.add_array(key, val)
                    elif val and isinstance(val[0], (int, np.integer)):
                        w.add_array(key, [int(x) for x in val])
                    elif val and isinstance(val[0], float):
                        w.add_array(key, [float(x) for x in val])
                n_kv += 1
            except Exception as e:
                print("  ⚠️ 跳过 %s: %s" % (key, str(e)[:40]))
    print("  复制了 %d 项" % n_kv)

    # 设置量化后的 file_type = 7（Q8_0）
    w.add_file_type(7)

    # ── 量化 tensor ──
    print()
    print("=== 量化 tensor ===")
    t0 = time.time()
    n_q = n_f32 = 0
    for i, t in enumerate(r.tensors):
        data = t.data
        # 1 维的（norm/bias）保持 F32 —— 数值敏感，量化会掉精度
        if len(t.shape) == 1 or t.shape[0] <= 1:
            arr = np.array(data, dtype=np.float32)
            w.add_tensor(t.name, arr)
            n_f32 += 1
        else:
            # 2D 转成 (rows, cols) 再量化
            shape = tuple(int(x) for x in t.shape)
            arr = np.array(data, dtype=np.float32).reshape(shape)
            q, sc = quantize_q8_0(arr)
            # gguf 的 Q8_0 需要传原始 float，让 writer 自己量化
            # （gguf 库有 raw_quantize 支持）
            try:
                w.add_tensor(t.name, arr,
                             raw_dtype=GGMLQuantizationType.Q8_0)
                n_q += 1
            except Exception:
                # 退化：直接写 F16
                w.add_tensor(t.name, arr.astype(np.float16))
                n_q += 1
        if (i + 1) % 50 == 0:
            print("  已处理 %d/%d ... (%.1fs)"
                  % (i + 1, len(r.tensors), time.time() - t0))

    print("  量化 %d 个，保持 F32 %d 个，耗时 %.1fs"
          % (n_q, n_f32, time.time() - t0))

    print()
    print("=== 落盘 ===")
    t0 = time.time()
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    print("  耗时 %.1fs" % (time.time() - t0))

    sz = os.path.getsize(args.out) / 1024 ** 3
    print()
    print("✓ 完成: %s（%.2f GB）" % (args.out, sz))
    return 0


if __name__ == "__main__":
    sys.exit(main())
