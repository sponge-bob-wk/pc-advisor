# -*- coding: utf-8 -*-
"""probe_maxlen.py —— 探测 8GB 显存能承受的 max_length

已知：
  512  → 显存 6.4 GB，成功训练
  1024 → OOM（要分配 928 MiB，只剩 201 MiB）

现在需要知道 640 / 768 能不能跑。这决定用哪个压缩版 prompt：
  中等压缩（446 token）+ 答案 107 = 553 → 需要 max_length >= 640
  激进压缩（211 token）+ 答案 107 = 318 → 512 就够

**能跑 640 就用中等压缩（信息更全），跑不了才用激进压缩。**

做法：只加载模型 + 跑一次前向反向，测显存峰值。不训练。
"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

MODEL_DIR = r"E:\models\Qwen3-4B-Instruct-2507"


def probe(max_len):
    """测给定 max_length 的显存峰值。返回 (成功?, 峰值 GB, 错误)。"""
    import torch
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              BitsAndBytesConfig)
    from peft import (LoraConfig, get_peft_model,
                      prepare_model_for_kbit_training)

    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()

    try:
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
        tok = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)
        bnb = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_DIR, quantization_config=bnb, device_map="auto",
            trust_remote_code=True, torch_dtype=torch.bfloat16)
        model = prepare_model_for_kbit_training(model)
        model.gradient_checkpointing_enable()
        lora = LoraConfig(
            r=16, lora_alpha=32, lora_dropout=0.05,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"],
            bias="none", task_type="CAUSAL_LM")
        model = get_peft_model(model, lora)

        ids = torch.randint(0, 1000, (1, max_len)).cuda()
        out = model(input_ids=ids, labels=ids)
        out.loss.backward()

        peak = torch.cuda.max_memory_allocated() / 1024 ** 3
        del model, out, ids
        torch.cuda.empty_cache()
        return True, peak, None
    except torch.cuda.OutOfMemoryError as e:
        msg = str(e)[:100]
        del model
        torch.cuda.empty_cache()
        return False, 0, "OOM: " + msg
    except Exception as e:
        return False, 0, "%s: %s" % (type(e).__name__, str(e)[:80])


def main():
    print("=" * 60)
    print("探测 8GB 显存能承受的 max_length")
    print("=" * 60)

    for ml in (512, 640, 768):
        print()
        print("--- max_length = %d ---" % ml)
        t0 = time.time()
        ok, peak, err = probe(ml)
        dt = time.time() - t0
        if ok:
            print("  ✓ 成功，显存峰值 %.2f GB，耗时 %.1fs" % (peak, dt))
            if peak > 7.2:
                print("    ⚠️ 接近上限（8.0 GB），训练时可能 OOM")
        else:
            print("  ✗ 失败（%.1fs）" % dt)
            print("    %s" % err)


if __name__ == "__main__":
    main()
