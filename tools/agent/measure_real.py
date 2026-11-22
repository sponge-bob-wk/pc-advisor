# -*- coding: utf-8 -*-
"""measure_real.py —— 实测训练真实内存需求（绕过 8GB 门槛）

`measure_mem.py` 有 8 GB 门槛（防蓝屏），但现在可用 7.4 GB，
卡在门槛上测不了。这个脚本**不设门槛**，分段测真实需求，
让人知道到底差多少。

⚠️ 风险：如果真实需求超过可用内存，可能触发交换甚至卡死。
   所以每步都打印剩余内存，并在低于 1.5 GB 时立即停止。
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
FLOOR_GB = 1.5          # 低于这个立即停


def rss():
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024 ** 3
    except Exception:
        return -1


def avail():
    try:
        import psutil
        return psutil.virtual_memory().available / 1024 ** 3
    except Exception:
        return -1


def step(name):
    a = avail()
    r = rss()
    print("  %-28s 进程 %5.2f GB | 系统可用 %5.2f GB" % (name, r, a))
    if a < FLOOR_GB:
        print("  ✗ 可用内存低于 %.1f GB，立即停止（防卡死）" % FLOOR_GB)
        sys.exit(2)
    return r, a


def main():
    print("=== 初始 ===")
    step("启动")

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    print()
    print("=== 分段测量 ===")
    tok = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)
    step("① tokenizer")

    bnb = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True)
    t0 = time.time()
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_DIR, quantization_config=bnb, device_map="auto",
        trust_remote_code=True, torch_dtype=torch.bfloat16)
    print("    加载耗时 %.1fs" % (time.time() - t0))
    step("② 4bit 模型")

    model = prepare_model_for_kbit_training(model)
    model.gradient_checkpointing_enable()
    lora = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        bias="none", task_type="CAUSAL_LM")
    model = get_peft_model(model, lora)
    step("③ LoRA + 梯度检查点")

    # 前向 + 反向
    ids = torch.randint(0, 1000, (1, MAX_LEN)).cuda()
    t0 = time.time()
    out = model(input_ids=ids, labels=ids)
    out.loss.backward()
    print("    前向+反向 %.1fs" % (time.time() - t0))
    r, a = step("④ 前向+反向（显存峰值）")

    print()
    print("=== 结论 ===")
    print("  进程内存峰值: %.2f GB" % r)
    print("  系统剩余可用: %.2f GB" % a)
    print("  GPU 显存峰值: %.2f GB / 8.00 GB" % (
        torch.cuda.max_memory_allocated() / 1024 ** 3))
    print()
    print("  → 训练时系统需要约 %.1f GB 空闲才能安全" % r)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
