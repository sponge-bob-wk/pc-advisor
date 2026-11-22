# -*- coding: utf-8 -*-
r"""train_route_lora.py —— 路由层 QLoRA 微调

## 这个脚本训什么

只训**路由层**：把用户一句话判成 `{intent, topic, fit}`。
不训回答生成、不训预算层。

## 环境（2026-11-17 已确认可用，不要重装）

```
Python   : E:\\ai2026\\Scripts\\python.exe   ← venv，不是系统 python
torch    : 2.13.0+cu126  (CUDA 可用 ✓)
transformers 5.17.0 / peft 0.21.2 / datasets 5.0.1
accelerate 1.15.0 / bitsandbytes 0.50.2
缺 trl    : 不需要（本脚本手写训练循环，不依赖 trl）
```

模型权重（本地，不用下载）：
```
E:\\models\\Qwen3-4B-Instruct-2507   （7.6GB，3 个 safetensors）
```

显卡：RTX 4060 Laptop / 8GB 显存 → QLoRA 4bit + 梯度检查点，够用。

## 用法

```powershell
# 0. 先确认环境（用 venv 的 python，不是系统 python）
& 'E:\ai2026\Scripts\python.exe' -c "import torch; print(torch.cuda.is_available())"

# 1. 演练：只检查数据和显存，不真训
& 'E:\ai2026\Scripts\python.exe' train_route_lora.py --dry

# 2. 小步试跑（20 步，验证能训起来，约 2-3 分钟）
& 'E:\ai2026\Scripts\python.exe' train_route_lora.py --max-steps 20

# 3. 正式训（3 epoch）
& 'E:\ai2026\Scripts\python.exe' train_route_lora.py

# 4. 训完导出到 Ollama
& 'E:\ai2026\Scripts\python.exe' train_route_lora.py --export
```

## 关键设计决策（每条都有理由）

### ① 为什么用 `_extract_json` 兼容的格式

训练数据里 assistant 是「思维链 + JSON」两段。
`intent_layer._extract_json` 从后往前找 JSON，所以这个格式推理时能吃。
**如果不改解析器就训 CoT，训了也用不上** —— 见 2026-11-17 的踩坑记录。

### ② 为什么 max_length=1024 而不是更长

路由层的输出很短（思维链 3~5 句 + JSON）。
1024 够用，而且省显存。实测最长样本约 300 token。

### ③ 为什么 lora_alpha = 2 * r

标准做法。alpha/r 的比值决定 LoRA 更新的幅度，
2 倍是经验值（原论文推荐）。

### ④ 为什么 target_modules 选这七个

Qwen3 的注意力层是 `q_proj/k_proj/v_proj/o_proj`，
MLP 是 `gate_proj/up_proj/down_proj`。
**只训注意力**效果不够，**全训**显存吃紧 —— 七个是平衡点。

### ⑤ 为什么不用 trl 的 SFTTrainer

环境里没装 trl。而且手写循环更好控制（比如按 intent 分桶采样）。
如果以后装了 trl，可以换，但没必要。
"""
import argparse
import io
import json
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "lora_data")
TRAIN = os.path.join(DIR, "route-train.jsonl")
EVAL = os.path.join(DIR, "route-eval.jsonl")

MODEL_DIR = r"E:\models\Qwen3-4B-Instruct-2507"
OUT_DIR = os.path.join(HERE, "lora_out")

# 训练超参（8GB 显存下的保守配置）
CFG = {
    # 🔴 2026-11-22：max_length 从 1024 降到 512。
    #
    #    为什么：实测训练集样本极短 ——
    #      字符数 中位 122 / 90分位 219 / 最大 413
    #      粗估 token 中位 81 / 90分位 146 / 最大 275
    #    1024 是 4 倍浪费，而 8GB 显存本来就紧张。
    #
    #    触发原因：这次训练报 CUDA OOM（要分配 928 MiB，只剩 201 MiB）。
    #    上次能跑是因为 Ollama 关得更干净、系统占用更少。
    #    降到 512 后 90 分位样本（146 token）仍有 3.5 倍余量。
    "max_length": 512,
    "lora_r": 16,
    "lora_alpha": 32,          # = 2 * r
    "lora_dropout": 0.05,
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj",
                       "gate_proj", "up_proj", "down_proj"],
    "batch_size": 1,           # 8GB 显存只能 1
    "grad_accum": 8,           # 等效 batch = 8
    "lr": 2e-4,
    "epochs": 3,
    "warmup_ratio": 0.05,
    "logging_steps": 5,
    # 🔴 2026-11-18：save_steps 从 100 降到 50。
    #    原因：3 epoch 只有 183 步，save_steps=100 意味着只在第 100 步存一次 ——
    #    用户想「噪声太大就中途停」时，停在第 80 步就白跑了（没有可用检查点）。
    #    改成 50：会在 50/100/150 各存一次，任何时刻停都有最近的权重可用。
    #    save_total_limit=2 保证只留最近两个，不占太多磁盘。
    "save_steps": 50,
}


def load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def to_text(sample, tokenizer):
    """把 messages 转成训练文本。

    🔴 关键：**只对 assistant 部分算 loss**。
       如果对 user/system 也算，模型会学着"复述用户的话"。

       做法：用 chat template 生成完整文本，再单独生成
       "到 assistant 之前"的文本，两者的长度差就是 assistant 区间。
    """
    msgs = sample["messages"]
    full = tokenizer.apply_chat_template(msgs, tokenize=False,
                                         add_generation_prompt=False)
    # 前缀 = 去掉最后一条 assistant
    prefix = tokenizer.apply_chat_template(msgs[:-1], tokenize=False,
                                           add_generation_prompt=True)
    return full, prefix


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只检查数据")
    ap.add_argument("--max-steps", type=int, default=0, help="限制步数（试跑）")
    ap.add_argument("--epochs", type=float, default=CFG["epochs"])
    ap.add_argument("--lr", type=float, default=CFG["lr"])
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--export", action="store_true",
                    help="只做导出（不训练）")
    ap.add_argument("--resume", action="store_true",
                    help="从最新的 checkpoint 续训（中断后用）")
    args = ap.parse_args()

    # ── 数据检查（不管训不训都做）──
    train = load_jsonl(TRAIN)
    evalset = load_jsonl(EVAL)
    print("训练集 %d 条 / 评估集 %d 条" % (len(train), len(evalset)))
    if not train:
        print("✗ 训练集为空，先跑 build_route_lora.py --write")
        return 1

    # 统计带思维链的比例
    n_cot = sum(1 for s in train
                if len((s["messages"][-1].get("content") or "").split("\n")) > 1)
    print("  其中带思维链：%d 条（%.0f%%）" % (n_cot, 100.0 * n_cot / len(train)))

    # 标签分布（按 intent）
    from collections import Counter
    import re
    c = Counter()
    for s in train:
        txt = s["messages"][-1].get("content") or ""
        # 从后往前找 JSON
        i = txt.rfind("{")
        if i >= 0:
            try:
                d = json.loads(txt[i:txt.rfind("}") + 1])
                c[d.get("intent")] += 1
            except Exception:
                c["<解析失败>"] += 1
    print("  intent 分布：")
    for k, v in c.most_common():
        print("    %-16s %3d" % (k, v))

    if args.dry:
        print()
        print("=== 环境检查 ===")
        try:
            import torch
            print("  torch      : %s" % torch.__version__)
            print("  CUDA 可用  : %s" % torch.cuda.is_available())
            if torch.cuda.is_available():
                print("  显存       : %.1f GB" % (
                    torch.cuda.get_device_properties(0).total_memory / 1024 ** 3))
        except ImportError:
            print("  ✗ torch 未安装")
            return 1
        for m in ("transformers", "peft", "datasets", "bitsandbytes"):
            try:
                mod = __import__(m)
                print("  %-11s: %s" % (m, getattr(mod, "__version__", "?")))
            except ImportError:
                print("  %-11s: ✗ 未安装" % m)
        print("  模型目录   : %s" % ("✓ 存在" if os.path.isdir(MODEL_DIR)
                                      else "✗ 不存在"))
        print()
        print("（演练模式。去掉 --dry 开始训练）")
        return 0

    # ── 训练 ──
    import torch
    from datasets import Dataset
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              BitsAndBytesConfig, TrainingArguments,
                              Trainer, DataCollatorForSeq2Seq)
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    if not torch.cuda.is_available():
        print("✗ CUDA 不可用 —— 必须用 E:\\ai2026\\Scripts\\python.exe")
        return 1

    print()
    print("=== 加载模型（4bit）===")
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_DIR, quantization_config=bnb, device_map="auto",
        trust_remote_code=True, torch_dtype=torch.bfloat16)
    model = prepare_model_for_kbit_training(model)
    model.gradient_checkpointing_enable()

    lora = LoraConfig(
        r=CFG["lora_r"], lora_alpha=CFG["lora_alpha"],
        lora_dropout=CFG["lora_dropout"],
        target_modules=CFG["target_modules"],
        bias="none", task_type="CAUSAL_LM")
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()
    print("  加载耗时 %.1fs" % (time.time() - t0))

    print()
    print("=== 构造数据集（只对 assistant 算 loss）===")

    def _prep(sample):
        full, prefix = to_text(sample, tok)
        full_ids = tok(full, add_special_tokens=False)["input_ids"]
        prefix_ids = tok(prefix, add_special_tokens=False)["input_ids"]
        # 截断
        full_ids = full_ids[:CFG["max_length"]]
        n_prefix = min(len(prefix_ids), len(full_ids))
        labels = [-100] * n_prefix + full_ids[n_prefix:]
        return {"input_ids": full_ids, "labels": labels,
                "attention_mask": [1] * len(full_ids)}

    ds_train = Dataset.from_list([_prep(s) for s in train])
    ds_eval = Dataset.from_list([_prep(s) for s in evalset]) if evalset else None

    collator = DataCollatorForSeq2Seq(
        tok, padding=True, pad_to_multiple_of=8, label_pad_token_id=-100)

    total_steps = int(len(ds_train) * args.epochs / CFG["grad_accum"])
    if args.max_steps:
        total_steps = args.max_steps
    print("  训练样本 %d 条，等效 batch %d，总步数约 %d"
          % (len(ds_train), CFG["grad_accum"], total_steps))

    targs = TrainingArguments(
        output_dir=args.out,
        per_device_train_batch_size=CFG["batch_size"],
        gradient_accumulation_steps=CFG["grad_accum"],
        num_train_epochs=args.epochs,
        max_steps=args.max_steps if args.max_steps else -1,
        learning_rate=args.lr,
        # 🔴 transformers 5.x 移除了 `warmup_ratio`，只保留 `warmup_steps`。
        #    实测报错：TypeError: TrainingArguments.__init__() got an
        #    unexpected keyword argument 'warmup_ratio'
        #    所以这里自己按比例算步数（原来是框架算的）。
        warmup_steps=max(1, int(total_steps * CFG["warmup_ratio"])),
        logging_steps=CFG["logging_steps"],
        save_steps=CFG["save_steps"],
        save_total_limit=2,
        bf16=True,
        gradient_checkpointing=True,
        optim="paged_adamw_8bit",
        report_to=[],
        remove_unused_columns=False,
        eval_strategy="steps" if ds_eval else "no",
        eval_steps=CFG["save_steps"] if ds_eval else None,
    )

    trainer = Trainer(
        model=model, args=targs, train_dataset=ds_train,
        eval_dataset=ds_eval, data_collator=collator)

    print()
    print("=== 开始训练 ===")
    print("提示：噪声大时按 Ctrl+C 可随时中断，已训练的权重会保存下来。")
    print()
    t0 = time.time()
    interrupted = False
    # 🔴 2026-11-18：`--resume` 从最新 checkpoint 续训。
    #    中断后用这个，不用从头跑 28 分钟。
    _resume = False
    if args.resume:
        import glob as _glob
        _cks = sorted(_glob.glob(os.path.join(args.out, "checkpoint-*")),
                      key=lambda p: int(p.rsplit("-", 1)[-1] or 0))
        if _cks:
            _resume = True
            print("从检查点续训：%s" % os.path.basename(_cks[-1]))
        else:
            print("⚠ 没找到 checkpoint，从头训")
    try:
        trainer.train(resume_from_checkpoint=_resume)
    except KeyboardInterrupt:
        # 🔴 2026-11-18：用户要求「噪声太大中间可以停」。
        #    Ctrl+C 会抛 KeyboardInterrupt，如果不捕获，
        #    后面的 save_pretrained 根本不会执行 —— 白跑。
        #    捕获后照常往下走，把当前权重存下来。
        interrupted = True
        print()
        print("⚠ 已中断（Ctrl+C）。当前训练到第 %d 步，正在保存权重..."
              % trainer.state.global_step)
    elapsed_min = (time.time() - t0) / 60
    if interrupted:
        print("中断时已训练 %.1f 分钟，共 %d 步"
              % (elapsed_min, trainer.state.global_step))
    else:
        print()
        print("训练完成，耗时 %.1f 分钟" % elapsed_min)

    print()
    print("=== 保存 LoRA adapter ===")
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print("  已保存到 %s" % args.out)

    # 🔴 2026-11-18：训练结束（含中断）自动写一条记录。
    #    用户要求：「训练也要记录日志，这些都是后续毕设作品的量化标准，
    #    包括写到简历上的项目」。
    #    所以不靠人工记得去跑 training_record.py，训练完自动落一条。
    try:
        import training_record as _tr
        _rec = _tr.collect()
        _tr.save(_rec)
        print()
        print("=== 训练记录已归档 ===")
        print("  训练集 %s 条 / CoT %s 条" % (
            (_rec.get("dataset") or {}).get("samples", "—"),
            (_rec.get("dataset") or {}).get("cot_samples", "—")))
        if _rec.get("loss_first") is not None:
            print("  loss %.3f → %.3f" % (_rec["loss_first"], _rec["loss_last"]))
        if _rec.get("eval_loss_best") is not None:
            print("  eval 最优 %.3f" % _rec["eval_loss_best"])
        print("  汇总：python training_record.py --report --md")
    except Exception as _e:
        print("  ⚠ 记录失败（不影响权重）：%s" % _e)
    if interrupted:
        print("  ⚠️ 这是**中断时**的权重（第 %d 步），不是训完的。"
              % trainer.state.global_step)
        print("     想对比的话，检查点在各 step 子目录：")
        print("     %s/checkpoint-*/" % args.out)
    print()
    print("下一步：")
    print("  1. 合并权重 + 导出 GGUF（需要 llama.cpp）")
    print("  2. 或直接用 transformers 加载 adapter 做推理测试")
    print("  3. 训完务必跑 test_intent.py 看有没有提升（现在 73/74）")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
