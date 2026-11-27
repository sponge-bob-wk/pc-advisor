# -*- coding: utf-8 -*-
r"""eval_lora.py —— 用训练好的 LoRA adapter 跑评估

## 这个脚本解决什么问题

训练完了不能只看 loss —— **loss 低不等于判得对**。
必须拿同一批测试句跑一遍，看 `intent` 准确率有没有真的提升。

## 怎么用

```powershell
$py = 'E:\ai2026\Scripts\python.exe'
cd E:\电脑涨价分析\tools\agent

# 对抗测试 50 条（和训练前基线 82.0% 对比）
& $py eval_lora.py --suite adversarial

# 训练集自测（看有没有背下来）
& $py eval_lora.py --suite train

# 评估集（真正衡量泛化）
& $py eval_lora.py --suite eval

# 全部
& $py eval_lora.py --suite all
```

## 关键设计

### ① 不碰 `intent_layer` 的缓存

`intent_layer.route()` 有缓存和 `_MODEL_DEAD` 标记。评估时必须清掉，
否则会读到训练前的旧结果。

### ② 用和推理时完全一样的 prompt

从 `intent_layer.SYS_TMPL` 取，不另写一份 ——
否则测的不是同一个东西（"判据抄两遍一定会漂"的老毛病）。

### ③ 输出长度也要看

如果模型学成"啰嗦的分析"，准确率可能还行但实际不可用。
所以同时统计平均输出长度。
"""
import argparse
import io
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DIR = os.path.join(HERE, "lora_data")
ADAPTER = os.path.join(HERE, "lora_out")
MODEL_DIR = r"E:\models\Qwen3-4B-Instruct-2507"


def load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def extract_json(txt):
    """和 intent_layer._extract_json 同逻辑（从后往前找 JSON）。"""
    if not txt:
        return None
    s = str(txt)
    end = len(s)
    while True:
        i = s.rfind("{", 0, end)
        if i < 0:
            return None
        depth = 0
        for j in range(i, len(s)):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        o = json.loads(s[i:j + 1])
                        if isinstance(o, dict):
                            return o
                    except Exception:
                        pass
                    break
        end = i


def build_suites():
    """准备三个测试集。返回 {name: [(text, gold_dict), ...]}"""
    out = {}

    # ── 对抗测试 50 条（从 harvest_cot 的模板取）──
    try:
        import harvest_cot as HC
        adv = []
        for group in (HC.SEED_PATTERNS, HC.SEED_LONGTAIL):
            for text, (intent, topic, fit), _cot in group:
                adv.append((text, {"intent": intent, "topic": topic, "fit": fit}))
        out["adversarial"] = adv
    except Exception as e:
        print("⚠ 对抗集加载失败：%s" % e)

    # ── 训练集自测 ──
    tr = []
    for r in load_jsonl(os.path.join(DIR, "route-train.jsonl")):
        gold = extract_json(r["messages"][-1].get("content") or "")
        if gold and "intent" in gold:
            tr.append((r["messages"][1]["content"], gold))
    if tr:
        out["train"] = tr

    # ── 评估集（泛化）──
    ev = []
    for r in load_jsonl(os.path.join(DIR, "route-eval.jsonl")):
        gold = extract_json(r["messages"][-1].get("content") or "")
        if gold and "intent" in gold:
            ev.append((r["messages"][1]["content"], gold))
    if ev:
        out["eval"] = ev

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="adversarial",
                    choices=["adversarial", "train", "eval", "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--adapter", default=None,
                    help="adapter 目录（默认 lora_out）")
    ap.add_argument("--no-adapter", action="store_true",
                    help="用基座模型（对照组）")
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import PeftModel

    if not torch.cuda.is_available():
        print("✗ CUDA 不可用，必须用 E:\\ai2026\\Scripts\\python.exe")
        return 1
    _adapter = args.adapter or ADAPTER
    if not os.path.isabs(_adapter):
        _adapter = os.path.join(HERE, _adapter)
    if not os.path.isdir(_adapter) and not args.no_adapter:
        print("✗ 找不到 adapter：%s" % _adapter)
        return 1

    suites = build_suites()
    if args.suite == "all":
        names = list(suites)
    else:
        names = [args.suite] if args.suite in suites else []
    if not names:
        print("✗ 没有可用的测试集")
        return 1

    print("=== 加载模型 ===")
    tag = "基座（对照组）" if args.no_adapter else "基座 + LoRA"
    print("  %s" % tag)
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
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.bfloat16,
                             bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_DIR, quantization_config=bnb, device_map="auto",
        trust_remote_code=True, torch_dtype=torch.bfloat16)
    if not args.no_adapter:
        model = PeftModel.from_pretrained(model, _adapter)
    model.eval()

    # 用和推理时一样的 system prompt
    # 🔴 2026-11-27：ctx 不能再手写。生产是 _ctx_of(焦点) 的描述式，
    #    手写 "（当前讨论：id）" 是降级分支，评出来的分不代表线上表现。
    import intent_layer as il
    sysmsg = il.SYS_TMPL.format(ctx=il._ctx_of("zol-2103842"))

    results = {}
    for name in names:
        items = suites[name]
        if args.limit:
            items = items[:args.limit]
        print()
        print("=== %s（%d 条）===" % (name, len(items)))
        ok = 0
        out_lens = []
        wrong = []
        for text, gold in items:
            msgs = [{"role": "system", "content": sysmsg},
                    {"role": "user", "content": text}]
            prompt = tok.apply_chat_template(msgs, tokenize=False,
                                             add_generation_prompt=True)
            ids = tok(prompt, return_tensors="pt").to(model.device)
            with torch.no_grad():
                # 🔴 2026-11-22：max_new_tokens 从 200 提到 400。
                #    必须和 intent_layer 的 num_predict 保持一致 ——
                #    否则评估测的不是真实推理条件。
                #    实测：v3 模型的思维链 90 分位 107 token，加 JSON 约 137，
                #    200 会截断，400 有余量。
                gen = model.generate(**ids, max_new_tokens=400,
                                     do_sample=False, temperature=0.0,
                                     pad_token_id=tok.pad_token_id)
            out = tok.decode(gen[0][ids["input_ids"].shape[1]:],
                             skip_special_tokens=True)
            out_lens.append(len(out))
            got = extract_json(out)
            gi = (got or {}).get("intent")
            good = (gi == gold.get("intent"))
            ok += 1 if good else 0
            if not good and len(wrong) < 8:
                wrong.append((text, gold.get("intent"), gi, out[:60]))
        acc = 100.0 * ok / max(1, len(items))
        results[name] = {"acc": acc, "n": len(items), "ok": ok,
                         "avg_len": sum(out_lens) / max(1, len(out_lens))}
        print("  准确率 %.1f%%（%d/%d）  平均输出 %.0f 字"
              % (acc, ok, len(items), results[name]["avg_len"]))
        if wrong:
            print("  判错的前 %d 条：" % len(wrong))
            for t, g, got_i, o in wrong:
                print("    %-24s 期望 %-14s 实际 %s" % (t[:22], g, got_i))

    print()
    print("=== 汇总 ===")
    for name, r in results.items():
        print("  %-12s %.1f%%（%d 条）平均 %.0f 字"
              % (name, r["acc"], r["n"], r["avg_len"]))

    # 和训练前基线对比
    if "adversarial" in results:
        base = 82.0
        cur = results["adversarial"]["acc"]
        print()
        print("  对抗测试基线（训练前）: %.1f%%" % base)
        print("  本次（%s）: %.1f%%  %s" % (
            tag, cur,
            "提升 %.1f 个点" % (cur - base) if cur > base
            else "下降 %.1f 个点" % (base - cur) if cur < base
            else "持平"))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
