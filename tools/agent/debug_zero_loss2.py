# -*- coding: utf-8 -*-
"""debug_zero_loss2.py —— 最小复现：为什么 loss 恒等于 0

## 已排除

  ✅ labels 构造正确（diagnose_zero_loss 验证：0 条被 mask，有效 label 20-70 个）
  ✅ system prompt 一致（562 字符 = 238 token）
  ✅ 训练集 512 token 装得下（prefix 255 + 答案 107）

## 待查

  ❓ DataCollatorForSeq2Seq 会不会把 labels 弄丢
  ❓ model(input_ids, labels) 的 loss 是不是真的算不出来
  ❓ 模型输出 logits 是否异常

## 做法

用 1 条真实样本，走完整流程（_prep → collator → model → loss），
逐步打印中间值。
"""
import io
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

D = r"E:\电脑涨价分析\tools\agent"
sys.path.insert(0, D)
MODEL_DIR = r"E:\models\Qwen3-4B-Instruct-2507"
MAX_LEN = 512


def main():
    import torch
    from datasets import Dataset
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              BitsAndBytesConfig, DataCollatorForSeq2Seq)
    from peft import (LoraConfig, get_peft_model,
                      prepare_model_for_kbit_training)

    # 读一条真实样本
    rows = []
    for line in io.open(D + r"\lora_data\route-train.jsonl", encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    sample = rows[0]
    print("=== 样本 ===")
    print("  user: %r" % sample["messages"][1]["content"][:50])
    print("  assistant: %r" % sample["messages"][2]["content"][:80])
    print()

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
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    # ── 复现 _prep ──
    full = tok.apply_chat_template(sample["messages"], tokenize=False,
                                   add_generation_prompt=False)
    prefix = tok.apply_chat_template(sample["messages"][:-1], tokenize=False,
                                     add_generation_prompt=True)
    full_ids = tok(full, add_special_tokens=False)["input_ids"][:MAX_LEN]
    prefix_ids = tok(prefix, add_special_tokens=False)["input_ids"]
    n_prefix = min(len(prefix_ids), len(full_ids))
    labels = [-100] * n_prefix + full_ids[n_prefix:]

    print("=== _prep 结果 ===")
    print("  full_ids: %d token" % len(full_ids))
    print("  prefix_ids: %d token" % len(prefix_ids))
    print("  n_prefix: %d" % n_prefix)
    print("  labels 里 -100 的个数: %d" % labels.count(-100))
    print("  labels 里有效 token: %d" % (len(labels) - labels.count(-100)))
    print("  有效 token 前 10 个: %s" % labels[n_prefix:n_prefix + 10])
    print()

    if len(labels) - labels.count(-100) == 0:
        print("✗ labels 全被 mask —— 就是这里的问题")
        return 1

    # ── 过 collator ──
    ds = Dataset.from_list([{"input_ids": full_ids, "labels": labels,
                             "attention_mask": [1] * len(full_ids)}])
    collator = DataCollatorForSeq2Seq(
        tok, padding=True, pad_to_multiple_of=8, label_pad_token_id=-100)
    batch = collator([ds[0]])

    print("=== collator 之后 ===")
    for k, v in batch.items():
        if hasattr(v, "shape"):
            print("  %-14s shape=%s" % (k, tuple(v.shape)))
        else:
            print("  %-14s %r" % (k, v))
    bl = batch["labels"][0].tolist()
    print("  labels 里 -100 的个数: %d" % bl.count(-100))
    print("  labels 里有效 token: %d" % (len(bl) - bl.count(-100)))
    print()

    if len(bl) - bl.count(-100) == 0:
        print("✗ **collator 把 labels 抹掉了** —— 这就是根因")
        return 1

    # ── 过模型 ──
    print("=== 加载模型并算 loss ===")
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

    batch = {k: v.cuda() if hasattr(v, "cuda") else v
             for k, v in batch.items()}
    model.eval()
    with torch.no_grad():
        out = model(**batch)
    print("  loss: %r" % out.loss)
    print("  loss.item(): %r" % out.loss.item())
    print("  logits shape: %s" % (tuple(out.logits.shape),))

    # 手动算一遍交叉熵
    logits = out.logits
    lab = batch["labels"]
    shift_logits = logits[..., :-1, :].contiguous()
    shift_labels = lab[..., 1:].contiguous()
    print()
    print("=== 手动算交叉熵 ===")
    print("  shift_labels 里有效 token: %d"
          % (shift_labels != -100).sum().item())
    if (shift_labels != -100).sum().item() == 0:
        print("  ✗ shift 之后没有有效 token —— 答案只有 1 个 token？")
        print("    这是 label 移位导致的：labels[:-1] 对应 logits，")
        print("    如果有效 token 只有 1 个且在最后，shift 后就没了。")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
