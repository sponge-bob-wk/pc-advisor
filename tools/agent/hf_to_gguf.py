# -*- coding: utf-8 -*-
"""hf_to_gguf.py —— 把 HF safetensors 模型转成 GGUF（纯 Python）

## 为什么自己写

标准的转换工具 `llama.cpp/convert_hf_to_gguf.py` 下载不了
（hosts 把 29 个 GitHub 域名指向 127.0.0.1）。

替代方案都失败了：
  · Ollama 0.40 的 safetensors 支持 → 只在 macOS（MLX），Windows 报错
  · `llama-cpp-python` → 没有 Windows 预编译 wheel
  · `llama-cpp-bin` → 安装时 Windows 长路径报错

**所以用 `gguf` 库（PyPI 可装，纯 Python）自己写。**

## GGUF 的结构

    magic("GGUF") + version + tensor_count + kv_count
    + 元数据 KV 对（架构、超参、tokenizer）
    + 每个 tensor 的信息（名字、维度、类型、偏移）
    + tensor 数据（按 32 字节对齐）

## 关键映射（HF → GGUF）

Qwen3 的命名规则很规整：

    model.embed_tokens.weight              → token_embd.weight
    model.layers.N.input_layernorm.weight  → blk.N.attn_norm.weight
    model.layers.N.mlp.down_proj.weight    → blk.N.ffn_down.weight
    model.layers.N.mlp.gate_proj.weight    → blk.N.ffn_gate.weight
    model.layers.N.mlp.up_proj.weight      → blk.N.ffn_up.weight
    model.layers.N.post_attention_layernorm.weight → blk.N.ffn_norm.weight
    model.layers.N.self_attn.k_norm.weight → blk.N.attn_k_norm.weight
    model.layers.N.self_attn.q_norm.weight → blk.N.attn_q_norm.weight
    model.layers.N.self_attn.{q,k,v,o}_proj.weight → blk.N.attn_{q,k,v,o}.weight
    model.norm.weight                      → output_norm.weight
    lm_head.weight                         → output.weight

## 量化

先输出 **F16**（无损，8 GB）。Ollama 导入时可以用 `--quantize q4_K_M`
自动量化，不需要我在这里做。

## 用法

    & 'E:\ai2026\Scripts\python.exe' hf_to_gguf.py \
        --model E:\models\Qwen3-4B-Instruct-2507-lora_v5 \
        --out   E:\models\route-v5-f16.gguf
"""
import argparse
import io
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

# tensor 名映射（Qwen3 / Llama 系通用）
NAME_MAP = {
    "model.embed_tokens.weight": "token_embd.weight",
    "model.norm.weight": "output_norm.weight",
    "lm_head.weight": "output.weight",
}
# 层内的后缀映射
LAYER_MAP = {
    "input_layernorm.weight": "attn_norm.weight",
    "post_attention_layernorm.weight": "ffn_norm.weight",
    "self_attn.q_proj.weight": "attn_q.weight",
    "self_attn.k_proj.weight": "attn_k.weight",
    "self_attn.v_proj.weight": "attn_v.weight",
    "self_attn.o_proj.weight": "attn_output.weight",
    "self_attn.q_norm.weight": "attn_q_norm.weight",
    "self_attn.k_norm.weight": "attn_k_norm.weight",
    "mlp.gate_proj.weight": "ffn_gate.weight",
    "mlp.up_proj.weight": "ffn_up.weight",
    "mlp.down_proj.weight": "ffn_down.weight",
}


def map_name(hf_name):
    """HF tensor 名 → GGUF tensor 名。"""
    if hf_name in NAME_MAP:
        return NAME_MAP[hf_name]
    if hf_name.startswith("model.layers."):
        parts = hf_name.split(".", 3)      # ['model','layers','0','rest']
        if len(parts) == 4:
            idx, rest = parts[2], parts[3]
            if rest in LAYER_MAP:
                return "blk.%s.%s" % (idx, LAYER_MAP[rest])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="HF 模型目录")
    ap.add_argument("--out", required=True, help="输出 .gguf 路径")
    args = ap.parse_args()

    from gguf import GGUFWriter
    from safetensors import safe_open

    cfg_path = os.path.join(args.model, "config.json")
    if not os.path.exists(cfg_path):
        print("✗ 找不到 config.json")
        return 1
    cfg = json.load(io.open(cfg_path, encoding="utf-8"))

    # 找 safetensors 文件（可能分片）
    st_files = sorted(
        f for f in os.listdir(args.model) if f.endswith(".safetensors"))
    if not st_files:
        print("✗ 找不到 .safetensors")
        return 1

    print("=" * 64)
    print("HF → GGUF 转换")
    print("=" * 64)
    print("  模型: %s" % args.model)
    print("  config: %s" % cfg.get("architectures"))
    print("  分片: %d 个" % len(st_files))
    print("  输出: %s" % args.out)
    print()

    # ── 读所有 tensor 名（先扫一遍，确认映射全覆盖）──
    all_names = []
    for f in st_files:
        with safe_open(os.path.join(args.model, f), framework="pt") as fh:
            all_names.extend(list(fh.keys()))

    mapped = {}
    unmapped = []
    for n in all_names:
        g = map_name(n)
        if g:
            mapped[n] = g
        else:
            unmapped.append(n)

    print("  tensor 总数: %d" % len(all_names))
    print("  已映射: %d" % len(mapped))
    if unmapped:
        print("  ⚠️ 未映射 %d 个（会被跳过）:" % len(unmapped))
        for n in unmapped[:5]:
            print("      %s" % n)
    print()

    # ── 创建 writer ──
    n_layer = cfg.get("num_hidden_layers")
    n_embd = cfg.get("hidden_size")
    n_head = cfg.get("num_attention_heads")
    n_head_kv = cfg.get("num_key_value_heads") or n_head
    n_ff = cfg.get("intermediate_size")
    vocab = cfg.get("vocab_size")
    ctx = cfg.get("max_position_embeddings", 4096)
    eps = cfg.get("rms_norm_eps", 1e-6)

    # 🔴🔴 2026-11-24：**rope_theta 藏在 rope_parameters 里**。
    #
    #    踩的坑：我原来读 `cfg.get("rope_theta")`，但 Qwen3 的
    #    config.json 里没有这个顶层键，它在：
    #
    #      "rope_parameters": {"rope_theta": 5000000, "rope_type": "default"}
    #
    #    读不到时回退到 10000，而真实值是 **5000000** —— 差 500 倍。
    #
    #    后果：位置编码完全错误，模型输出垃圾或直接崩
    #    （实测 Ollama 报 0xc0000409 栈溢出）。
    #
    #    对比标准 GGUF（qwen3-4b-text）确认：
    #      qwen3.rope.freq_base = 5000000.0
    rope_theta = cfg.get("rope_theta")
    if not rope_theta:
        rp = cfg.get("rope_parameters") or cfg.get("rope_scaling") or {}
        if isinstance(rp, dict):
            rope_theta = rp.get("rope_theta") or rp.get("base")
    if not rope_theta:
        rope_theta = 10000.0
        print("  ⚠️ 没找到 rope_theta，用默认 10000（可能不对）")
    else:
        print("  rope_theta = %s" % rope_theta)
    head_dim = cfg.get("head_dim") or (n_embd // n_head)

    print("=== 模型超参 ===")
    for k, v in (("n_layer", n_layer), ("n_embd", n_embd),
                 ("n_head", n_head), ("n_head_kv", n_head_kv),
                 ("n_ff", n_ff), ("vocab", vocab), ("ctx", ctx),
                 ("head_dim", head_dim), ("rope_theta", rope_theta)):
        print("  %-12s %s" % (k, v))
    print()

    w = GGUFWriter(args.out, "qwen3")

    # ── 元数据 ──
    #
    # 🔴 2026-11-24：补齐 llama.cpp 期望的标准字段。
    #    只写必需的几个也能导入，但推理时可能崩。
    w.add_name("route-v5")
    w.add_context_length(ctx)
    w.add_embedding_length(n_embd)
    w.add_block_count(n_layer)
    w.add_feed_forward_length(n_ff)
    w.add_head_count(n_head)
    w.add_head_count_kv(n_head_kv)
    w.add_key_length(head_dim)
    w.add_value_length(head_dim)
    w.add_layer_norm_rms_eps(eps)
    w.add_rope_freq_base(rope_theta)
    w.add_file_type(1)      # 1 = F16

    # 这些字段 llama.cpp 会读，缺失时走默认值但可能不匹配
    try:
        w.add_quantization_version(2)       # GGML_QNT_VERSION
    except Exception:
        pass
    try:
        w.add_alignment(32)                 # tensor 数据对齐
    except Exception:
        pass

    # tokenizer
    tok_json = os.path.join(args.model, "tokenizer.json")
    tokens = []
    if os.path.exists(tok_json):
        tj = json.load(io.open(tok_json, encoding="utf-8"))
        vocab = tj.get("model", {}).get("vocab") or {}
        if isinstance(vocab, dict):
            # 按 id 排序
            pairs = sorted(vocab.items(), key=lambda x: x[1])
            tokens = [t for t, _ in pairs]

        # 🔴 2026-11-24：**把 added_tokens 补进 token 列表**。
        #
        #    踩的坑：token_embd.weight 是 [2560, 151936]，但我写的
        #    token 列表只有 151643 个，Ollama 据此推断词表 151643，
        #    报错：
        #      check_tensor_dims: tensor 'token_embd.weight' has wrong
        #      shape; expected 2560, 151643, got 2560, 151936
        #
        #    实际构成：
        #      tokenizer.json 的 vocab      151643 个
        #      added_tokens（special）          26 个
        #      config.json 的 vocab_size    151936 个 ← 预留了空位
        #
        #    所以要把 added_tokens 按 id 插进列表，再补空到 vocab_size。
        added = tj.get("added_tokens") or []
        if added:
            max_id = max(x.get("id", 0) for x in added)
            # 补齐到 max_id + 1
            while len(tokens) <= max_id:
                tokens.append("")
            for x in added:
                tid = x.get("id")
                if isinstance(tid, int) and tid < len(tokens):
                    tokens[tid] = x.get("content") or ""

        # 再补到 config 的 vocab_size（多出来的位置是空 token）
        target = cfg.get("vocab_size") or len(tokens)
        if len(tokens) < target:
            print("  tokenizer: 补齐 %d → %d（config vocab_size）"
                  % (len(tokens), target))
            while len(tokens) < target:
                tokens.append("")

        merges = tj.get("model", {}).get("merges") or []

        # 🔴🔴 2026-11-24：**必须写 token_type 和 scores**，否则 llama.cpp 崩。
        #
        #    踩的坑：只写 tokens + merges，Ollama 导入成功但推理时
        #    `0xc0000409`（栈溢出/缓冲区越界）。
        #
        #    原因：llama.cpp 的 tokenizer 加载时**按 token_type 决定
        #    每个 token 怎么处理**（普通/未知/控制/用户自定义）。
        #    字段缺失时它读到垃圾值 → 数组越界 → 崩溃。
        #
        #    token_type 取值（llama.cpp 的 enum）：
        #      1 = NORMAL      普通 token
        #      2 = UNKNOWN     未知
        #      3 = CONTROL     控制符（<|im_start|> 这类）
        #      4 = USER_DEFINED 用户自定义
        #      6 = BYTE        字节 token
        #
        #    Qwen3 的实际分布：
        #      0~151642   普通词表 → NORMAL(1)
        #      151643~    特殊 token → CONTROL(3)
        n_vocab_base = len(vocab) if isinstance(vocab, dict) else len(tokens)
        ctrl_ids = set()
        for x in (added or []):
            tid = x.get("id")
            if isinstance(tid, int):
                ctrl_ids.add(tid)
        token_types = []
        for i in range(len(tokens)):
            if i in ctrl_ids:
                token_types.append(3)      # CONTROL
            else:
                token_types.append(1)      # NORMAL

        if merges:
            w.add_tokenizer_model("gpt2")
            w.add_tokenizer_pre("qwen2")
            w.add_token_list(tokens)
            w.add_token_merges(
                [" ".join(m) if isinstance(m, list) else m for m in merges])
        else:
            w.add_tokenizer_model("gpt2")
            w.add_token_list(tokens)

        # 这两个字段是必须的（缺失会让 llama.cpp 崩）
        w.add_token_types(token_types)
        w.add_token_scores([0.0] * len(tokens))

        # 🔴 2026-11-24：标准 GGUF 有 add_bos_token=False。
        #    Qwen3 不需要 BOS（chat template 里自带 <|im_start|>）。
        try:
            w.add_add_bos_token(False)
            w.add_add_eos_token(False)
        except Exception:
            pass
    print("  tokenizer: %d 个 token" % len(tokens))

    # chat template
    ct = os.path.join(args.model, "chat_template.jinja")
    if os.path.exists(ct):
        w.add_chat_template(io.open(ct, encoding="utf-8").read())

    # 特殊 token
    #
    # 🔴 2026-11-24：方法名是 `add_bos_token_id` / `add_eos_token_id`，
    #    不是 `add_token_id`（那个不存在，会 AttributeError）。
    tokcfg_path = os.path.join(args.model, "tokenizer_config.json")
    if os.path.exists(tokcfg_path):
        tc = json.load(io.open(tokcfg_path, encoding="utf-8"))
        _SETTERS = {
            "bos_token": w.add_bos_token_id,
            "eos_token": w.add_eos_token_id,
            "pad_token": w.add_pad_token_id,
            "unk_token": w.add_unk_token_id,
        }
        for key, setter in _SETTERS.items():
            v = tc.get(key)
            if isinstance(v, dict):
                v = v.get("content")
            if isinstance(v, str) and v in tokens:
                try:
                    setter(tokens.index(v))
                except Exception:
                    pass

    # ── 写 tensor ──
    print()
    print("=== 写 tensor（F16）===")
    t0 = time.time()
    n_written = 0
    for f in st_files:
        with safe_open(os.path.join(args.model, f), framework="pt") as fh:
            for hf_name in fh.keys():
                gname = mapped.get(hf_name)
                if not gname:
                    continue
                t = fh.get_tensor(hf_name)

                # 🔴 2026-11-24：**必须处理 BFloat16**。
                #
                #    原始基座是 bfloat16，`t.numpy()` 会报：
                #      TypeError: Got unsupported ScalarType BFloat16
                #
                #    numpy 没有 bf16 类型，要手动转：
                #      bf16 → float32（左移 16 位补零）
                #    再降到 float16。
                #
                #    ⚠️ 直接 bf16 → fp16 可能溢出（fp16 范围小），
                #    所以先转 fp32 再判范围。
                arr = t.detach().cpu()
                if str(arr.dtype) in ("torch.bfloat16",):
                    import torch
                    arr = arr.to(torch.float32).numpy()
                else:
                    arr = arr.numpy()
                if arr.dtype != np.float16:
                    # 检查是否超出 fp16 范围（±65504）
                    amax = float(np.abs(arr).max()) if arr.size else 0.0
                    if amax > 65504:
                        print("  ⚠️ %s 有超 fp16 范围的值(%.0f)，改用 fp32 保留"
                              % (gname, amax))
                        # 不降精度，交给 gguf 写 F32
                    else:
                        arr = arr.astype(np.float16)
                w.add_tensor(gname, arr)
                n_written += 1
                if n_written % 40 == 0:
                    print("  已写 %d/%d ... (%.1fs)"
                          % (n_written, len(mapped), time.time() - t0))

    print("  共写 %d 个 tensor，耗时 %.1fs" % (n_written, time.time() - t0))

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
