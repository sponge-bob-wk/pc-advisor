# -*- coding: utf-8 -*-
r"""watch_training.py —— 训练进度可视化（只读，不占资源）

## 设计原则：绝不干扰训练

用户明确要求「前提是别影响训练和占用资源」。所以这个脚本：

  ✅ **只读文件** —— 打开 `train-*.log` 读文本，不碰训练进程
  ✅ **不调模型** —— 没有 Ollama、没有 transformers、没有 torch
  ✅ **不占显存** —— 纯文本解析，0 GPU 占用
  ✅ **不写文件** —— 不创建临时文件、不改日志
  ✅ **CPU 占用极低** —— 每秒读一次几千字节的文本，可以忽略

对比：如果用 TensorBoard，它要起一个 web server + 可能读 checkpoint
（checkpoint 是 100MB+ 的 safetensors），那会抢 IO。

## 它显示什么

```
训练进度  ████████████░░░░░░░░  65%   121/186 步
时间      已用 24:12 / 剩余 08:44 / 预计 08:59 完成
速度      8.07 秒/步（最近 10 步均值 8.12）
loss      当前 0.680 | 最低 0.367 | 首 3.808
eval      最优 0.580（每 50 步一次）
检查点    checkpoint-100（最近）
```

## 用法

```powershell
# 一次性看当前状态
& 'E:\ai2026\Scripts\python.exe' watch_training.py

# 持续刷新（每 5 秒，Ctrl+C 退出）
& 'E:\ai2026\Scripts\python.exe' watch_training.py --follow

# 指定日志和刷新间隔
& 'E:\ai2026\Scripts\python.exe' watch_training.py --follow --interval 3
```

## 为什么不用 rich/tqdm

它们会引入额外依赖，而且每次刷新重绘整个终端 —— 在训练占满 CPU 时
反而增加负担。这里用最朴素的 `\\r` 单行覆盖 + 标准库。
"""
import argparse
import glob
import os
import re
import sys
import time
from datetime import datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))


def read_text(path):
    """读日志，自动处理编码（PowerShell Tee-Object 写的是 UTF-16）。"""
    if not path or not os.path.exists(path):
        return ""
    try:
        raw = open(path, "rb").read()
    except OSError:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except Exception:
            pass
    if raw[:3] == b"\xef\xbb\xbf":
        try:
            return raw.decode("utf-8-sig")
        except Exception:
            pass
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def find_log():
    logs = sorted(glob.glob(os.path.join(HERE, "train-*.log")),
                  key=os.path.getmtime)
    return logs[-1] if logs else None


def parse(txt):
    """从日志文本抽所有指标。"""
    d = {"ok": False}
    if not txt:
        return d

    # ── 总步数 ──
    m = re.search(r"总步数约\s*(\d+)", txt)
    if m:
        d["total"] = int(m.group(1))
    # ── 训练样本 ──
    m = re.search(r"训练样本\s*(\d+)\s*条", txt)
    if m:
        d["samples"] = int(m.group(1))
    # ── 带思维链 ──
    m = re.search(r"其中带思维链：(\d+)\s*条", txt)
    if m:
        d["cot"] = int(m.group(1))
    # ── 可训练参数 ──
    m = re.search(r"trainable params:\s*([\d,]+).*?trainable%:\s*([\d.]+)", txt)
    if m:
        d["trainable"] = int(m.group(1).replace(",", ""))
        d["trainable_pct"] = float(m.group(2))

    # ── 进度条 ──
    #
    # 🔴 2026-11-22 修了两次，最后靠**前缀**区分。
    #
    #    日志里有三种进度条：
    #      Loading weights: 100%|██████| 398/398 [00:12<00:00, 31.34it/s]
    #      Loading weights:   0%|      |   1/398 [00:01<09:50,  1.49s/it]  ← 也是 s/it！
    #      100%|██████| 9/9 [00:08<00:00, 1.17s/it]                        ← eval
    #      26%|██▋ | 50/189 [05:52<17:34, 7.59s/it]                        ← 训练
    #
    #    第一版取 prog[-1] → eval（9/9，显示"100% 完成"）
    #    第二版取分母最大 → 加载（398/398，显示"0%"）
    #    第三版只认 s/it + 分母最大 → 还是加载（刚开始加载也是 s/it）
    #
    #    **最终判据：先剔掉带 `Loading weights:` 前缀的行**，
    #    剩下的里取分母最大 —— 那就是训练主循环。
    #    前缀匹配比单位/分母都可靠，因为它是语义标记，不是数值巧合。
    _lines = [ln for ln in txt.split("\n") if "Loading weights" not in ln]
    _body = "\n".join(_lines)
    prog = re.findall(
        r"(\d+)%\|[^|]*\|\s*(\d+)/(\d+)\s*\[([\d:]+)<([\d:]+),\s*([\d.]+)s/it\]",
        _body)
    if prog:
        # 🔴 2026-11-22 第四次修：**取最后一条**，不是分母最大那条。
        #
        #    日志是追加写的，所以同一个进度条会重复出现：
        #      1/189, 2/189, 3/189, ..., 50/189, ...
        #    取 max(分母) 会拿到**最早的**那条（1/189），
        #    看板显示"1% 剩余 23:37"，而实际已经跑了 5 分钟。
        #
        #    正确做法：先按分母分组，取**每组最后一条**，
        #    再在训练组（分母最大的那组）里取最后一条。
        _by_tot = {}
        for x in prog:
            _by_tot.setdefault(int(x[2]), []).append(x)
        # 训练组 = 分母最大的那组（eval 的分母小得多）
        _train_tot = max(_by_tot)
        pct, cur, tot, used, eta, spd = _by_tot[_train_tot][-1]
        d["pct"] = int(pct)
        d["cur"] = int(cur)
        d["total"] = int(tot)
        d["used"] = used
        d["eta"] = eta
        d["speed"] = float(spd)
        d["ok"] = True
        # eval 次数 = 非训练组的条数（按组算，不按条算）
        d["eval_passes"] = len(_by_tot) - 1

    # ── loss 记录 ──
    losses = re.findall(r"'loss':\s*'([\d.]+)'", txt)
    if losses:
        vals = [float(x) for x in losses]
        d["loss_last"] = vals[-1]
        d["loss_min"] = min(vals)
        d["loss_first"] = vals[0]
        d["loss_recent"] = vals[-10:]
    # ── grad_norm（判断有没有梯度爆炸）──
    gns = re.findall(r"'grad_norm':\s*'([\d.]+)'", txt)
    if gns:
        d["grad_norm_last"] = float(gns[-1])

    # ── eval ──
    evals = re.findall(r"'eval_loss':\s*'([\d.]+)'", txt)
    if evals:
        vs = [float(x) for x in evals]
        d["eval_last"] = vs[-1]
        d["eval_best"] = min(vs)
        d["eval_count"] = len(vs)
        d["eval_first"] = vs[0]

    # ── checkpoint ──
    cks = re.findall(r"checkpoint-(\d+)", txt)
    d["ckpt"] = "checkpoint-%s" % max(cks, key=int) if cks else None
    # 🔴 2026-11-22 修：**从输出目录看，不写死 lora_out**。
    #
    #    原来写死 `lora_out/checkpoint-*`，导致看板显示上一个训练的
    #    checkpoint-186（旧目录），而当前训练的输出在 `lora_v3`（还没到 50 步）。
    #    用户会以为"已经存了 186 步"，实际一步没存。
    #
    #    做法：从日志里抠出 --out 指定的目录，没有再退回 lora_out。
    try:
        _out = None
        _m = re.search(r"--out\s+[‘’'\"]?([^‘’'\"\s]+)", txt)
        if _m:
            _out = _m.group(1)
        if not _out:
            _out = os.path.join(HERE, "lora_out")
        elif not os.path.isabs(_out):
            _out = os.path.join(HERE, _out)
        dirs = [p for p in glob.glob(os.path.join(_out, "checkpoint-*"))
                if os.path.isdir(p)]
        if dirs:
            nums = [int(re.sub(r".*-(\d+)$", r"\1", p) or 0) for p in dirs]
            d["ckpt"] = "checkpoint-%d" % max(nums)
            d["ckpt_count"] = len(dirs)
        else:
            # 当前输出目录还没有 checkpoint —— 明确显示，不要沿用旧的
            d["ckpt"] = None
            d["ckpt_dir"] = os.path.basename(_out)
    except Exception:
        pass

    # ── 结束状态 ──
    if re.search(r"训练完成，耗时\s*([\d.]+)\s*分钟", txt):
        d["done"] = True
        d["ok"] = True
    elif re.search(r"中断时已训练", txt):
        d["interrupted"] = True
        d["ok"] = True
    return d


def _bar(pct, width=24):
    """纯 ASCII 进度条（避免终端字体缺字）。"""
    filled = int(width * pct / 100)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _hms(s):
    """秒数转 时:分:秒。"""
    s = int(s)
    if s >= 3600:
        return "%d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)
    return "%02d:%02d" % (s // 60, s % 60)


def _parse_hms(t):
    """'24:12' 或 '1:02:33' → 秒。"""
    parts = [int(x) for x in str(t).split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] if parts else 0


def render(d, log_path):
    """渲染成多行文本（不用 \r 覆盖，便于复制）。"""
    L = []
    name = os.path.basename(log_path) if log_path else "(没找到日志)"
    if not d.get("ok"):
        L.append("训练还没开始，或日志里还没有进度信息。")
        L.append("日志：%s" % name)
        return "\n".join(L)

    if d.get("done"):
        L.append("训练完成")
    elif d.get("interrupted"):
        L.append("训练已中断")
    else:
        L.append("训练进行中")

    cur = d.get("cur", 0)
    tot = d.get("total", 1)
    pct = d.get("pct", 0)
    L.append("%s %3d%%   %d/%d 步" % (_bar(pct), pct, cur, tot))

    # 时间
    used = _parse_hms(d.get("used", "0"))
    eta = _parse_hms(d.get("eta", "0"))
    if d.get("done") or d.get("interrupted"):
        L.append("已用 %s" % _hms(used))
    else:
        finish = datetime.now() + timedelta(seconds=eta)
        # 只显示时:分 —— 秒对"心里有准"没帮助，反而显得啰嗦
        L.append("已用 %s / 剩余 %s / 预计 %s 完成"
                 % (_hms(used), _hms(eta), finish.strftime("%H:%M")))

    # 速度
    spd = d.get("speed")
    rec = d.get("loss_recent") or []
    if spd:
        L.append("速度 %.2f 秒/步" % spd)

    # loss
    if d.get("loss_last") is not None:
        L.append("loss  当前 %.3f | 最低 %.3f | 首 %.3f"
                 % (d["loss_last"], d["loss_min"], d["loss_first"]))
        if len(rec) >= 5:
            # 简单的趋势判断：最近 5 个 vs 再前 5 个
            L.append("      最近 10 步：" + " ".join("%.2f" % x for x in rec))
    # grad_norm
    if d.get("grad_norm_last") is not None:
        gn = d["grad_norm_last"]
        flag = "  <-- 偏大，留意" if gn > 10 else ""
        L.append("梯度  %.2f%s" % (gn, flag))

    # eval
    if d.get("eval_best") is not None:
        L.append("eval  最优 %.3f | 最近 %.3f | 已评 %d 次"
                 % (d["eval_best"], d["eval_last"], d["eval_count"]))

    # 数据
    if d.get("samples"):
        L.append("数据  %d 条训练（%d 条带思维链）"
                 % (d["samples"], d.get("cot", 0)))
    if d.get("trainable"):
        L.append("参数  可训练 %s（%.2f%%）"
                 % (format(d["trainable"], ","), d.get("trainable_pct", 0)))

    # checkpoint
    if d.get("ckpt"):
        L.append("检查点 %s%s" % (
            d["ckpt"],
            "（共 %d 个）" % d["ckpt_count"] if d.get("ckpt_count") else ""))
    elif d.get("ckpt_dir"):
        # 🔴 2026-11-22：当前输出目录还没有 checkpoint。
        #    必须**明确说出来**，不能静默省略 ——
        #    否则用户看到"没这行"会以为检查点功能坏了。
        #    真实含义：还没到 save_steps，中断就白跑。
        L.append("检查点 暂无（%s 里还没到 save_steps=50，中断会丢）"
                 % d["ckpt_dir"])

    L.append("")
    L.append("日志：%s" % name)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=None)
    ap.add_argument("--follow", action="store_true", help="持续刷新")
    ap.add_argument("--interval", type=float, default=5.0,
                    help="刷新间隔秒（默认 5）")
    args = ap.parse_args()

    log = args.log or find_log()
    if not log:
        print("没找到 train-*.log。训练还没开始？")
        return 1

    if not args.follow:
        print(render(parse(read_text(log)), log))
        return 0

    # 持续刷新：清屏 + 重绘。用 ANSI 而不是 os.system('cls')，
    # 后者在某些终端会闪。
    try:
        while True:
            txt = read_text(log)
            d = parse(txt)
            sys.stdout.write("\033[2J\033[H")     # 清屏 + 光标归位
            sys.stdout.write(render(d, log))
            sys.stdout.write("\n\n（每 %.0fs 刷新，Ctrl+C 退出）" % args.interval)
            sys.stdout.flush()
            if d.get("done") or d.get("interrupted"):
                sys.stdout.write("\n\n训练已结束，可以按 Ctrl+C 退出。")
                sys.stdout.flush()
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已退出（训练不受影响）")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
