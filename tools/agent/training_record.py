# -*- coding: utf-8 -*-
"""training_record.py —— 训练记录：为毕设/简历留可量化证据

## 为什么单独做这个

用户原话：「训练也要记录日志，这些都是后续毕设作品的一个量化标准，
包括写到简历上的项目」

训练完只知道"跑完了"是不够的。毕设答辩和简历需要的是**可核验的数字**：

  · 训练集多大、标签怎么分布、多少条带思维链
  · 用了什么硬件、训了多久、显存峰值多少
  · loss 从多少降到多少、eval 准确率提升了几个点
  · 前后对比：改了哪个判据、修了什么 bug

所以这个脚本做两件事：

  ① **采集**：从 `trainer_state.json` + 训练日志里抽结构化指标
  ② **归档**：写进 `training_runs.jsonl`（一次训练一条，可纵向对比）

## 为什么不用 TensorBoard

TensorBoard 是**过程可视化**，这个项目要的是**可引用的结论**。
而且毕设报告里贴不进去 TensorBoard 截图，只能贴表格。

## 数据来源

```
lora_out/checkpoint-*/trainer_state.json   ← HF Trainer 自动写的
lora_out/trainer_state.json
train-*.log                                ← stdout 重定向
```

## 用法

    python training_record.py                    # 采集最近一次训练
    python training_record.py --log train-3ep.log
    python training_record.py --report           # 打印汇总表（简历/毕设用）
    python training_record.py --report --md      # 输出 Markdown 表格
"""
import argparse
import glob
import io
import json
import os
import re
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "lora_data")
OUT_DIR = os.path.join(HERE, "lora_out")
RECORD = os.path.join(DIR, "training_runs.jsonl")


def _read_json(path):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return None


def _latest_adapter_dir():
    """找最新的 adapter 输出目录（lora_out / lora_v3 / lora_v4 / lora_v5 …）。

    🔴 2026-11-24 修：原来 `_find_state` 写死 `lora_out`，
        导致记录 v5 训练时读到的是 **lora_out 的旧 trainer_state**：

          记录写: loss 首 3.808 → 末 0.182，eval 首 0.580 → 末 0.183
          实际是: loss 首 2.555 → 末 0.100，eval 首 0.591 → 末 0.200

        **训练记录是给论文用的材料，数据错了比没有更糟。**

        判据：在 HERE 下找所有含 `checkpoint-*` 或
        `adapter_model.safetensors` 的子目录，按修改时间取最新。
    """
    best = None
    best_mtime = 0
    try:
        for name in os.listdir(HERE):
            d = os.path.join(HERE, name)
            if not os.path.isdir(d):
                continue
            # 有 adapter 或 checkpoint 才算训练输出目录
            has = (os.path.exists(os.path.join(d, "adapter_model.safetensors"))
                   or bool(glob.glob(os.path.join(d, "checkpoint-*"))))
            if not has:
                continue
            mt = os.path.getmtime(d)
            if mt > best_mtime:
                best, best_mtime = d, mt
    except Exception:
        pass
    return best or OUT_DIR


def _find_state(out_dir=None):
    """找最新的 trainer_state.json（优先根目录，其次最新 checkpoint）。"""
    if out_dir is None:
        out_dir = _latest_adapter_dir()
    root = os.path.join(out_dir, "trainer_state.json")
    if os.path.exists(root):
        return root
    cks = sorted(glob.glob(os.path.join(out_dir, "checkpoint-*")),
                 key=lambda p: int(re.sub(r".*-(\d+)$", r"\1", p) or 0))
    for ck in reversed(cks):
        p = os.path.join(ck, "trainer_state.json")
        if os.path.exists(p):
            return p
    return None


def _read_log_text(path):
    """读日志文本，自动处理编码。

    🔴 2026-11-18 踩的坑：PowerShell 的 `Tee-Object` 默认写 **UTF-16 LE**（带 BOM
    0xff 0xfe），而 Python 的 `io.open(encoding="utf-8")` 读会抛
    `UnicodeDecodeError: invalid start byte 0xff`。

    更坑的是：`Get-Content` 能正常读（它自动识别 BOM），
    所以 `Select-String` 能看到内容，但 Python 读不到 ——
    导致正则全部匹配失败，记录里"可训练参数"永远是空。

    做法：先看 BOM 判断编码，读不出来再逐个试。
    """
    if not path or not os.path.exists(path):
        return ""
    raw = open(path, "rb").read()
    # UTF-16 LE BOM
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except Exception:
            pass
    # UTF-8 BOM
    if raw[:3] == b"\xef\xbb\xbf":
        try:
            return raw.decode("utf-8-sig")
        except Exception:
            pass
    # 逐个试
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_log(path):
    """从训练日志里抽：进度、显存、耗时、中断标记。"""
    info = {}
    txt = _read_log_text(path)
    if not txt:
        return info
    # 总步数
    m = re.search(r"总步数约\s*(\d+)", txt)
    if m:
        info["total_steps"] = int(m.group(1))
    # 训练样本数
    m = re.search(r"训练样本\s*(\d+)\s*条", txt)
    if m:
        info["train_samples"] = int(m.group(1))
    # 模型加载耗时
    m = re.search(r"加载耗时\s*([\d.]+)s", txt)
    if m:
        info["model_load_s"] = float(m.group(1))
    # 训练完成/中断
    m = re.search(r"训练完成，耗时\s*([\d.]+)\s*分钟", txt)
    if m:
        info["train_minutes"] = float(m.group(1))
        info["completed"] = True
    m = re.search(r"中断时已训练\s*([\d.]+)\s*分钟，共\s*(\d+)\s*步", txt)
    if m:
        info["train_minutes"] = float(m.group(1))
        info["interrupted_at_step"] = int(m.group(2))
        info["completed"] = False
    # 🔴 2026-11-18：训练**进行中**采集时，日志里没有"完成/中断"标记。
    #    第一版会把 completed 留空，汇总表显示"中断@None"——那是误判。
    #    改成：既没完成也没中断标记 → 视为"进行中"。
    if "completed" not in info:
        info["completed"] = None       # None = 进行中，不是中断
    # 可训练参数（训练一开始就打出来了，进行中也能采到）
    m = re.search(r"trainable params:\s*([\d,]+).*?trainable%:\s*([\d.]+)", txt)
    if m:
        info["trainable_params"] = int(m.group(1).replace(",", ""))
        info["trainable_pct"] = float(m.group(2))
    # 可训练参数
    m = re.search(r"trainable params:\s*([\d,]+).*?trainable%:\s*([\d.]+)", txt)
    if m:
        info["trainable_params"] = int(m.group(1).replace(",", ""))
        info["trainable_pct"] = float(m.group(2))
    # 是否带思维链
    m = re.search(r"其中带思维链：(\d+)\s*条", txt)
    if m:
        info["cot_samples"] = int(m.group(1))
    return info


def _parse_state(state):
    """从 trainer_state.json 抽：loss 曲线、eval 历史、最优步。"""
    out = {}
    if not state:
        return out
    out["global_step"] = state.get("global_step")
    out["epoch"] = state.get("epoch")
    out["best_metric"] = state.get("best_metric")
    out["best_model_checkpoint"] = state.get("best_model_checkpoint")
    hist = state.get("log_history") or []
    train_losses, eval_losses, evals = [], [], []
    for h in hist:
        if "loss" in h:
            train_losses.append({"step": h.get("step"), "loss": h.get("loss"),
                                 "lr": h.get("learning_rate"),
                                 "grad_norm": h.get("grad_norm")})
        if "eval_loss" in h:
            evals.append({"step": h.get("step"), "eval_loss": h.get("eval_loss"),
                          "eval_runtime": h.get("eval_runtime")})
    out["train_loss_curve"] = train_losses
    out["eval_history"] = evals
    if train_losses:
        out["loss_first"] = train_losses[0]["loss"]
        out["loss_last"] = train_losses[-1]["loss"]
        # 最低 loss（噪声大时最后不一定最低）
        out["loss_min"] = min(x["loss"] for x in train_losses)
    if evals:
        out["eval_loss_first"] = evals[0]["eval_loss"]
        out["eval_loss_best"] = min(x["eval_loss"] for x in evals)
        out["eval_loss_last"] = evals[-1]["eval_loss"]
    # 总耗时（HF 记的）
    for h in reversed(hist):
        if "train_runtime" in h:
            out["train_runtime_s"] = h["train_runtime"]
            out["train_samples_per_second"] = h.get("train_samples_per_second")
            out["train_steps_per_second"] = h.get("train_steps_per_second")
            break
    return out


def _dataset_stats():
    """训练集统计（从 route-train.jsonl 现算，保证与数据一致）。"""
    p = os.path.join(DIR, "route-train.jsonl")
    if not os.path.exists(p):
        return {}
    from collections import Counter
    n = n_cot = 0
    c = Counter()
    for line in io.open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        n += 1
        txt = r["messages"][-1].get("content") or ""
        if len(txt.split("\n")) > 1:
            n_cot += 1
        i = txt.rfind("{")
        if i >= 0:
            try:
                d = json.loads(txt[i:txt.rfind("}") + 1])
                c[d.get("intent")] += 1
            except Exception:
                pass
    return {"samples": n, "cot_samples": n_cot,
            "intent_dist": dict(c.most_common())}


def _gpu_info():
    """显卡信息（写简历要用）。"""
    import subprocess
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def collect(log_name=None):
    """采集一次完整记录。"""
    # 找日志
    log = None
    if log_name:
        log = log_name if os.path.isabs(log_name) else os.path.join(HERE, log_name)
    else:
        logs = sorted(glob.glob(os.path.join(HERE, "train-*.log")),
                      key=os.path.getmtime)
        log = logs[-1] if logs else None

    state_path = _find_state()
    state = _read_json(state_path) if state_path else None

    rec = {
        "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "log_file": os.path.basename(log) if log else None,
        "state_file": os.path.relpath(state_path, HERE) if state_path else None,
    }
    rec.update(_parse_log(log))
    rec.update(_parse_state(state))
    rec["dataset"] = _dataset_stats()
    rec["gpu"] = _gpu_info()

    # 环境版本
    try:
        import torch
        rec["env"] = {"torch": torch.__version__,
                      "cuda_available": torch.cuda.is_available(),
                      "cuda_version": getattr(torch.version, "cuda", None)}
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            rec["env"]["vram_gb"] = round(p.total_memory / 1024 ** 3, 1)
    except Exception:
        pass
    for m in ("transformers", "peft", "datasets", "bitsandbytes"):
        try:
            mod = __import__(m)
            rec.setdefault("env", {})[m] = getattr(mod, "__version__", "?")
        except ImportError:
            pass
    return rec


def save(rec):
    os.makedirs(DIR, exist_ok=True)
    with io.open(RECORD, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return RECORD


def report(as_md=False):
    """打印所有训练记录（纵向对比）。"""
    if not os.path.exists(RECORD):
        print("还没有训练记录。先跑一次训练，再执行：")
        print("  python training_record.py")
        return
    rows = []
    for line in io.open(RECORD, encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))

    if as_md:
        print("| 时间 | 样本 | CoT | 步数 | loss 首→末 | eval 最优 | 耗时 | 完成 |")
        print("|---|---:|---:|---:|---|---|---:|---|")
        for r in rows:
            ds = r.get("dataset") or {}
            lf, ll = r.get("loss_first"), r.get("loss_last")
            loss_s = ("%.3f→%.3f" % (lf, ll)) if lf is not None and ll is not None else "—"
            eb = r.get("eval_loss_best")
            eb_s = ("%.3f" % eb) if eb is not None else "—"
            mins = r.get("train_minutes")
            m_s = ("%.0f 分" % mins) if mins is not None else "—"
            _c = r.get("completed")
            if _c is True:
                done = "✓"
            elif _c is False:
                done = "中断@%s" % (r.get("interrupted_at_step") or "?")
            else:
                done = "进行中"
            print("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                (r.get("recorded_at") or "")[5:16],
                ds.get("samples", "—"), ds.get("cot_samples", "—"),
                r.get("global_step", "—"), loss_s, eb_s, m_s, done))
        return

    print("训练记录（共 %d 次）" % len(rows))
    print("=" * 74)
    for i, r in enumerate(rows, 1):
        ds = r.get("dataset") or {}
        env = r.get("env") or {}
        print()
        print("【第 %d 次】%s" % (i, r.get("recorded_at")))
        print("  数据  : %s 条训练 / %s 条带思维链" % (
            ds.get("samples", "—"), ds.get("cot_samples", "—")))
        if ds.get("intent_dist"):
            print("  分布  : " + " / ".join(
                "%s %d" % (k, v) for k, v in ds["intent_dist"].items()))
        print("  硬件  : %s" % (r.get("gpu") or "—"))
        print("  环境  : torch %s (CUDA %s) / transformers %s / peft %s" % (
            env.get("torch", "?"), env.get("cuda_version", "?"),
            env.get("transformers", "?"), env.get("peft", "?")))
        print("  显存  : %s GB" % env.get("vram_gb", "?"))
        print("  参数  : 可训练 %s（%.2f%%）" % (
            r.get("trainable_params", "—"), r.get("trainable_pct", 0)))
        print("  步数  : %s（%s epoch）" % (r.get("global_step", "—"),
                                          round(r.get("epoch") or 0, 2)))
        lf, ll, lm = r.get("loss_first"), r.get("loss_last"), r.get("loss_min")
        if lf is not None:
            print("  loss  : 首 %.3f → 末 %.3f（最低 %.3f）" % (lf, ll, lm))
        if r.get("eval_loss_best") is not None:
            print("  eval  : 首 %.3f → 最优 %.3f（末 %.3f）" % (
                r.get("eval_loss_first", 0), r["eval_loss_best"],
                r.get("eval_loss_last", 0)))
        mins = r.get("train_minutes")
        if mins is not None:
            print("  耗时  : %.1f 分钟（%.2f 步/秒）" % (
                mins, r.get("train_steps_per_second") or 0))
        # 🔴 completed 三态：True=训完 / False=中断 / None=进行中
        _c = r.get("completed")
        if _c is True:
            _st = "完成 ✓"
        elif _c is False:
            _st = "中断 @ %s 步" % r.get("interrupted_at_step", "?")
        else:
            _st = "进行中（采集时还没结束）"
        print("  状态  : %s" % _st)
    print()
    print("=" * 74)
    print("原始记录：%s" % RECORD)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=None, help="指定训练日志文件")
    ap.add_argument("--report", action="store_true", help="打印汇总")
    ap.add_argument("--md", action="store_true", help="Markdown 表格输出")
    args = ap.parse_args()

    if args.report:
        report(as_md=args.md)
        return

    rec = collect(args.log)
    if not rec.get("global_step") and not rec.get("total_steps"):
        print("✗ 没找到训练状态。确认 lora_out/ 下有 trainer_state.json，")
        print("  或训练还在进行中（HF 要等第一次保存才写 state）。")
        print()
        print("  已找到的日志：%s" % rec.get("log_file"))
        return 1
    path = save(rec)
    print("已记录一次训练 → %s" % path)
    print()
    report()


if __name__ == "__main__":
    sys.exit(main() or 0)
