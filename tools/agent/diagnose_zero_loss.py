# -*- coding: utf-8 -*-
"""诊断 loss=0 的原因：是不是 labels 全被 mask 了

## 假设

训练脚本的 _prep：
    full_ids = tok(full)          # 完整对话
    prefix_ids = tok(prefix)      # 到 assistant 之前
    labels = [-100]*n_prefix + full_ids[n_prefix:]

昨天把 system prompt 从 56 字符改成 2170 字符（和推理一致）。
如果 prefix（system + user + 生成提示）本身就超过 max_length=512，
那 n_prefix = len(full_ids)，labels 全是 -100 → **loss 恒等于 0**。
"""
import io
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

D = r"E:\电脑涨价分析\tools\agent"
sys.path.insert(0, D)

MODEL_DIR = r"E:\models\Qwen3-4B-Instruct-2507"
MAX_LEN = 512

from transformers import AutoTokenizer

tok = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)

TRAIN = D + r"\lora_data\route-train.jsonl"
rows = []
for line in io.open(TRAIN, encoding="utf-8"):
    line = line.strip()
    if line:
        rows.append(json.loads(line))

print("训练集 %d 条" % len(rows))
print()

# 统计 prefix / full 的 token 长度
stats = {"prefix": [], "full": [], "label": [], "masked_all": 0}

for r in rows[:30]:
    msgs = r["messages"]
    full = tok.apply_chat_template(msgs, tokenize=False,
                                   add_generation_prompt=False)
    prefix = tok.apply_chat_template(msgs[:-1], tokenize=False,
                                     add_generation_prompt=True)
    full_ids = tok(full, add_special_tokens=False)["input_ids"]
    prefix_ids = tok(prefix, add_special_tokens=False)["input_ids"]

    n_full = len(full_ids[:MAX_LEN])
    n_pref = min(len(prefix_ids), n_full)
    n_label = n_full - n_pref

    stats["prefix"].append(len(prefix_ids))
    stats["full"].append(len(full_ids))
    stats["label"].append(n_label)
    if n_label == 0:
        stats["masked_all"] += 1

print("=== 前 30 条样本的 token 长度 ===")
print("  prefix（system+user+提示）: 中位 %d, 最大 %d"
      % (sorted(stats["prefix"])[15], max(stats["prefix"])))
print("  full（完整对话）:          中位 %d, 最大 %d"
      % (sorted(stats["full"])[15], max(stats["full"])))
print("  截断后有效 label 数:       中位 %d, 最小 %d"
      % (sorted(stats["label"])[15], min(stats["label"])))
print()
print("  **label 全被 mask（=0）的条数: %d / 30**" % stats["masked_all"])
print()

if stats["masked_all"] > 0:
    print("=" * 60)
    print("诊断结论：**prefix 长度超过 max_length，labels 全被 mask**")
    print()
    print("  prefix 中位 %d token > max_length %d"
          % (sorted(stats["prefix"])[15], MAX_LEN))
    print("  → n_prefix = min(len(prefix_ids), len(full_ids)) = 全部")
    print("  → labels = [-100] * 全部 → 没有可学习的 token")
    print("  → loss 恒等于 0，grad_norm 恒等于 0")
    print()
    print("根因链：")
    print("  ① 昨天发现训练/推理 system prompt 不一致（56 vs 2170 字符）")
    print("  ② 修法：训练集改用 2170 字符的完整 SYS_TMPL")
    print("  ③ 但 max_length 还是 512 —— 光 system 就吃掉了全部预算")
    print("  ④ 结果：所有 label 被 mask，训练等于空转")
    print()
    print("修法（三选一）：")
    print("  A. max_length 提到 2048（system 约 1400 token + 答案）")
    print("  B. 训练时用极简 system，但推理也用极简（回到原状，不推荐）")
    print("  C. 压缩 SYS_TMPL 到 300 token 以内（保留关键规则）")
else:
    print("label 正常，loss=0 另有原因。")
