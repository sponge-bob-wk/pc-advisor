# -*- coding: utf-8 -*-
"""debug_zero_loss4.py —— 用最小 Trainer 复现 loss=0

## 已排除（实测）

  ✅ labels 构造正确（21 个有效 token）
  ✅ DataCollatorForSeq2Seq 正常（有效 label 168/8条）
  ✅ 手写 collator 也正常
  ✅ model(**batch) 直接算 → loss 1.5438
  ✅ 传 num_items_in_batch 结果一样

## 唯一剩下的可能

  Trainer 内部某个配置把 loss 抹掉了。

## 做法

用**完全相同的配置**建一个最小 Trainer，只训 3 步，
每步打印 loss。然后逐个关掉可疑配置再试：
  ① 原样（bf16=True + gradient_checkpointing + paged_adamw_8bit）
  ② 关掉 bf16
  ③ 关掉 gradient_checkpointing
  ④ 换成普通 adamw
"""
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

D = r"E:\电脑涨价分析\tools\agent"
sys.path.insert(0, D)
MODEL_DIR = r"E:\models\Qwen3-4B-Instruct-2507"
MAX_LEN = 512


def build_data(tok, rows):
    from datasets import Dataset
    out = []
    for r in rows:
        full = tok.apply_chat_template(r["messages"], tokenize=False,
                                       add_generation_prompt=False)
        prefix = tok.apply_chat_template(r["messages"][:-1], tokenize=False,
                                         add_generation_prompt=True)
        fid = tok(full, add_special_tokens=False)["input_ids"][:MAX_LEN]
        pid = tok(prefix, add_special_tokens=False)["input_ids"]
        np_ = min(len(pid), len(fid))
        lab = [-100] * np_ + fid[np_:]
        out.append({"input_ids": fid, "labels": lab,
                    "attention_mask": [1] * len(fid)})
    return Dataset.from_list(out)


def run_case(name, tok, ds, coll, bf16, gc, optim):
    """跑 3 步，返回 loss 列表。"""
    import torch
    from transformers import (AutoModelForCausalLM, BitsAndBytesConfig,
                              TrainingArguments, Trainer)
    from peft import (LoraConfig, get_peft_model,
                      prepare_model_for_kbit_training)

    torch.cuda.empty_cache()

    bnb = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True)
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

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_DIR, quantization_config=bnb, device_map="auto",
        trust_remote_code=True, torch_dtype=torch.bfloat16)
    model = prepare_model_for_kbit_training(model)
    if gc:
        model.gradient_checkpointing_enable()
    lora = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        bias="none", task_type="CAUSAL_LM")
    model = get_peft_model(model, lora)

    targs = TrainingArguments(
        output_dir=os.path.join(D, "_probe_" + name),
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        max_steps=3,
        learning_rate=2e-4,
        logging_steps=1,
        save_steps=9999,
        bf16=bf16,
        gradient_checkpointing=gc,
        optim=optim,
        report_to=[],
        remove_unused_columns=False,
    )
    trainer = Trainer(model=model, args=targs, train_dataset=ds,
                      data_collator=coll)
    trainer.train()
    losses = [h["loss"] for h in trainer.state.log_history if "loss" in h]
    del model, trainer
    torch.cuda.empty_cache()
    return losses


def main():
    import shutil
    from transformers import AutoTokenizer, DataCollatorForSeq2Seq

    rows = []
    for line in io.open(D + r"\lora_data\route-train.jsonl", encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))

    tok = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    coll = DataCollatorForSeq2Seq(tok, padding=True, pad_to_multiple_of=8,
                                  label_pad_token_id=-100)
    ds = build_data(tok, rows[:16])
    print("数据 %d 条" % len(ds))
    print()

    CASES = [
        ("原样", True, True, "paged_adamw_8bit"),
        ("关bf16", False, True, "paged_adamw_8bit"),
        ("关gc", True, False, "paged_adamw_8bit"),
        ("普通adamw", True, True, "adamw_torch"),
    ]

    results = {}
    for name, bf16, gc, optim in CASES:
        print("=== %s（bf16=%s gc=%s %s）===" % (name, bf16, gc, optim))
        try:
            losses = run_case(name, tok, ds, coll, bf16, gc, optim)
            results[name] = losses
            print("  loss: %s" % [round(x, 4) for x in losses])
            allzero = all(abs(x) < 1e-9 for x in losses) if losses else None
            print("  全为 0: %s" % ("**是**" if allzero else "否 ✓"))
        except Exception as e:
            print("  失败: %s: %s" % (type(e).__name__, str(e)[:80]))
        print()
        # 清理临时目录
        p = os.path.join(D, "_probe_" + name)
        shutil.rmtree(p, ignore_errors=True)

    print("=== 结论 ===")
    for name, losses in results.items():
        allzero = all(abs(x) < 1e-9 for x in losses) if losses else None
        print("  %-12s %s" % (name, "loss=0 ✗" if allzero else "正常 ✓"))


if __name__ == "__main__":
    sys.exit(main() or 0)
