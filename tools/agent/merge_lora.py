# -*- coding: utf-8 -*-
"""merge_lora.py —— 把 LoRA adapter 合并进基座模型，输出完整权重

## 为什么要合并

Ollama 的 `create` 能直接吃 safetensors 目录，但**不认 PEFT 的 adapter 格式**。
所以要先把 adapter 的增量权重合并进基座，得到一个完整的模型目录。

## 合并原理

LoRA 的数学：
    W' = W + (alpha / r) * B @ A

其中：
    W  = 基座权重（frozen）
    A  = lora_A（降维矩阵，r × d）
    B  = lora_B（升维矩阵，d × r）
    alpha / r = scaling

`peft` 的 `merge_and_unload()` 做的就是这个。

## 输出

一个完整的 HF 模型目录，包含：
    config.json
    model.safetensors（或分片）
    tokenizer.json
    tokenizer_config.json
    ...

可以直接给 Ollama 的 `create` 用。

## 显存注意

合并需要把整个模型加载到内存（fp16 约 8 GB）。
**必须在 CPU 上做**（`device_map="cpu"`），否则 8GB 显存不够。

用 `safe_run` 守门会拦住 —— 这里故意跳过，因为走 CPU。
但**内存要够**：约 12 GB。
"""
import io
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    from paths import MODEL_DIR, adapter_path
except ImportError:
    MODEL_DIR = r"E:\models\Qwen3-4B-Instruct-2507"

    def adapter_path(v="lora_v5"):
        return os.path.join(HERE, v)


def check_mem():
    """合并需要约 12 GB 内存（fp16 加载）。"""
    import ctypes

    class MS(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    st = MS()
    st.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    phys = st.ullAvailPhys / 1024 ** 3
    commit = st.ullAvailPageFile / 1024 ** 3
    print("  物理可用: %.1f GB" % phys)
    print("  提交可用: %.1f GB" % commit)
    if phys < 6.0:
        print()
        print("  ⚠️  物理内存不足 6 GB。合并需要约 12 GB（fp16 权重 8 GB + 临时）。")
        print("      建议关掉浏览器/微信后再试。")
        return False
    return True


def main():
    ap_adapter = "lora_v5"
    out_dir = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--adapter" and i + 1 < len(args):
            ap_adapter = args[i + 1]
        if a == "--out" and i + 1 < len(args):
            out_dir = args[i + 1]

    adapter = adapter_path(ap_adapter)
    if out_dir is None:
        out_dir = os.path.join(r"E:\models", "Qwen3-4B-Instruct-2507-" + ap_adapter)

    print("=" * 64)
    print("合并 LoRA 到基座")
    print("=" * 64)
    print("  基座:   %s" % MODEL_DIR)
    print("  adapter: %s" % adapter)
    print("  输出:   %s" % out_dir)
    print()

    if not os.path.isdir(adapter):
        print("✗ adapter 目录不存在")
        return 1
    if not os.path.isdir(MODEL_DIR):
        print("✗ 基座目录不存在")
        return 1

    if not check_mem():
        return 1

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print()
    print("=== 加载基座（fp16，CPU）===")
    print("  ⚠️ 走 CPU 不用显存，但要 12 GB 内存")
    t0 = time.time()
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_DIR, torch_dtype=torch.float16,
        device_map="cpu", trust_remote_code=True, low_cpu_mem_usage=True)
    print("  加载耗时 %.1fs" % (time.time() - t0))

    print()
    print("=== 加载 adapter 并合并 ===")
    t0 = time.time()
    model = PeftModel.from_pretrained(model, adapter)
    model = model.merge_and_unload()
    print("  合并耗时 %.1fs" % (time.time() - t0))

    print()
    print("=== 保存完整模型 ===")
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    model.save_pretrained(out_dir, safe_serialization=True)
    print("  保存耗时 %.1fs" % (time.time() - t0))

    tok = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)
    tok.save_pretrained(out_dir)

    # 复制 chat template（Ollama 要用）
    for f in ("chat_template.jinja", "generation_config.json"):
        src = os.path.join(MODEL_DIR, f)
        if os.path.exists(src):
            import shutil
            shutil.copy2(src, os.path.join(out_dir, f))

    print()
    print("=== 输出 ===")
    total = 0
    for f in sorted(os.listdir(out_dir)):
        p = os.path.join(out_dir, f)
        if os.path.isfile(p):
            mb = os.path.getsize(p) / 1024 ** 2
            total += mb
            print("  %-40s %8.1f MB" % (f, mb))
    print("  %-40s %8.1f MB" % ("合计", total))
    print()
    print("✓ 完成：%s" % out_dir)
    print()
    print("下一步（导入 Ollama）：")
    print("  cd %s" % out_dir)
    print("  # 写 Modelfile")
    print("  ollama create route-v5 -f Modelfile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
