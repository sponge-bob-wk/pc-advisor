# -*- coding: utf-8 -*-
"""measure_mem.py —— 测训练各阶段的内存峰值

蓝屏根因：训练进程吃了 11 GB 内存，耗尽 15.2 GB 物理内存 + 页面文件。
这个脚本分段测内存，找出真正的峰值在哪。

用法：
    & 'E:\ai2026\Scripts\python.exe' measure_mem.py

⚠️ 这个脚本**会加载模型**，跑之前确保：
    · Ollama 已关
    · 浏览器/其他大程序已关
    · 可用内存 > 8 GB
"""
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MODEL_DIR = r"E:\models\Qwen3-4B-Instruct-2507"
MAX_LEN = 512


def mem():
    """当前进程内存（GB）。"""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024 ** 3
    except ImportError:
        return -1


def sysmem():
    """系统可用内存（GB）。"""
    try:
        import psutil
        v = psutil.virtual_memory()
        return v.available / 1024 ** 3, v.percent
    except ImportError:
        return -1, -1


def main():
    print("=== 初始状态 ===")
    a, p = sysmem()
    print("  系统可用: %.1f GB（已用 %d%%）" % (a, p))
    if a < 8:
        print()
        print("⚠️  可用内存不足 8 GB，先关掉其他程序再跑。")
        print("    蓝屏就是这么来的：训练峰值 11 GB + 其他程序 > 15.2 GB")
        return 1

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    print()
    print("=== ① 加载 tokenizer ===")
    tok = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)
    print("  进程内存: %.1f GB" % mem())

    print()
    print("=== ② 加载 4bit 模型（内存峰值通常在这）===")
    t0 = time.time()
    bnb = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_DIR, quantization_config=bnb, device_map="auto",
        trust_remote_code=True, torch_dtype=torch.bfloat16)
    print("  耗时 %.1fs" % (time.time() - t0))
    print("  进程内存: %.1f GB" % mem())
    a, p = sysmem()
    print("  系统可用: %.1f GB" % a)
    print("  GPU 显存: %.2f GB" % (torch.cuda.memory_allocated() / 1024 ** 3))

    print()
    print("=== ③ 加 LoRA + 梯度检查点 ===")
    model = prepare_model_for_kbit_training(model)
    model.gradient_checkpointing_enable()
    lora = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        bias="none", task_type="CAUSAL_LM")
    model = get_peft_model(model, lora)
    print("  进程内存: %.1f GB" % mem())
    print("  GPU 显存: %.2f GB" % (torch.cuda.memory_allocated() / 1024 ** 3))

    print()
    print("=== ④ 前向 + 反向（显存峰值在这）===")
    ids = torch.randint(0, 1000, (1, MAX_LEN)).cuda()
    t0 = time.time()
    out = model(input_ids=ids, labels=ids)
    loss = out.loss
    loss.backward()
    print("  耗时 %.1fs" % (time.time() - t0))
    print("  进程内存: %.1f GB" % mem())
    print("  GPU 显存分配: %.2f GB" % (torch.cuda.memory_allocated() / 1024 ** 3))
    print("  GPU 显存峰值: %.2f GB" % (torch.cuda.max_memory_allocated() / 1024 ** 3))
    a, p = sysmem()
    print("  系统可用: %.1f GB" % a)

    print()
    print("=== 结论 ===")
    peak_gpu = torch.cuda.max_memory_allocated() / 1024 ** 3
    print("  GPU 峰值 %.2f GB / 8.00 GB → %s" % (
        peak_gpu, "✓ 安全" if peak_gpu < 6.5 else "⚠️ 接近上限"))
    print("  进程内存峰值 %.1f GB" % mem())
    print()
    print("  蓝屏时的实际占用是 11 GB —— 如果这里只测到 3-4 GB，")
    print("  说明 11 GB 里有一部分是 **训练循环中累积的**（如日志/缓存），")
    print("  需要在训练脚本里加内存监控。")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
