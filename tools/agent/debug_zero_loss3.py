# -*- coding: utf-8 -*-
"""debug_zero_loss3.py —— 对比 collator 的两条路径

## 已知

  单条样本 model(**batch) → loss = 0.5313 ✓
  Trainer 训练 → loss = 0.0 ✗

## 待查

  Trainer 用的是 DataCollatorForSeq2Seq。这个 collator 有几个特殊行为：
    · 用 tok.pad_token_id 做 padding
    · 会把 labels 里的 pad 也设成 -100

  如果 `tok.pad_token_id` 恰好等于某个特殊值，或者 collator 对
  `attention_mask` 处理有问题，loss 可能被摊薄。

## 做法

用**同一批数据**，分别过：
  A. DataCollatorForSeq2Seq（训练用的）
  B. 手写 collator（最简单：直接 stack）
然后各自算 loss，对比。
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
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              BitsAndBytesConfig, DataCollatorForSeq2Seq)
    from peft import (LoraConfig, get_peft_model,
                      prepare_model_for_kbit_training)

    rows = []
    for line in io.open(D + r"\lora_data\route-train.jsonl", encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))

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

    print("=== tokenizer 特殊 token ===")
    print("  pad_token_id: %r" % tok.pad_token_id)
    print("  eos_token_id: %r" % tok.eos_token_id)
    print("  pad_token:    %r" % tok.pad_token)
    print()

    # 造 8 条样本（模拟 batch_size=1 + grad_accum=8 的一个微批）
    samples = []
    for r in rows[:8]:
        full = tok.apply_chat_template(r["messages"], tokenize=False,
                                       add_generation_prompt=False)
        prefix = tok.apply_chat_template(r["messages"][:-1], tokenize=False,
                                         add_generation_prompt=True)
        fid = tok(full, add_special_tokens=False)["input_ids"][:MAX_LEN]
        pid = tok(prefix, add_special_tokens=False)["input_ids"]
        np_ = min(len(pid), len(fid))
        lab = [-100] * np_ + fid[np_:]
        samples.append({"input_ids": fid, "labels": lab,
                        "attention_mask": [1] * len(fid)})

    # A. DataCollatorForSeq2Seq（训练用的）
    coll = DataCollatorForSeq2Seq(tok, padding=True,
                                  pad_to_multiple_of=8,
                                  label_pad_token_id=-100)
    batchA = coll(samples)

    # B. 手写 collator（各自 padding 到相同长度，pad 用 -100 / 0）
    maxlen = max(len(s["input_ids"]) for s in samples)
    pad = tok.pad_token_id
    ids_list, lab_list, am_list = [], [], []
    for s in samples:
        n = maxlen - len(s["input_ids"])
        ids_list.append(s["input_ids"] + [pad] * n)
        lab_list.append(s["labels"] + [-100] * n)
        am_list.append(s["attention_mask"] + [0] * n)
    batchB = {
        "input_ids": torch.tensor(ids_list),
        "labels": torch.tensor(lab_list),
        "attention_mask": torch.tensor(am_list),
    }

    print("=== 两个 collator 的 batch 对比 ===")
    for name, b in (("A: Seq2Seq", batchA), ("B: 手写", batchB)):
        lab = b["labels"]
        n_valid = (lab != -100).sum().item()
        print("  %-12s shape=%s  有效label=%d"
              % (name, tuple(lab.shape), n_valid))
    print()

    # 加载模型
    print("=== 加载模型 ===")
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
    model.eval()

    print()
    print("=== 两条路径的 loss ===")
    for name, b in (("A: Seq2Seq", batchA), ("B: 手写", batchB)):
        bb = {k: (v.cuda() if hasattr(v, "cuda") else v)
              for k, v in b.items()}
        with torch.no_grad():
            out = model(**bb)
        # 也模拟 Trainer 传 num_items_in_batch 的情况
        n_items = (bb["labels"][..., 1:] != -100).sum()
        with torch.no_grad():
            out2 = model(**bb, num_items_in_batch=n_items)
        print("  %-12s 不传 num_items: %.6f" % (name, out.loss.item()))
        print("  %-12s 传 num_items(%d): %.6f"
              % (name, n_items.item(), out2.loss.item()))

    print()
    print("=== 结论 ===")
    print("  如果「传 num_items」的 loss 接近 0，而「不传」正常，")
    print("  说明 transformers 5.17 的 num_items 归一化把 loss 摊薄了。")


if __name__ == "__main__":
    sys.exit(main() or 0)
