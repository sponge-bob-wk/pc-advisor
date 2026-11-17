# -*- coding: utf-8 -*-
"""预算动作分类器 · 评测器（2026-11-13）

## 为什么单独一个脚本

微调最容易骗人的地方：**准确率高不等于学会了**。
实测数据分布 none 占 84% —— 一个"永远回答 none"的废物模型，
准确率就有 84%，看着很体面。所以评测不能只看总准确率。

这里同时报四个数，缺一个都会给出错误结论：

  1. 总准确率（能骗人的那个）
  2. **每类召回**（哪个类被彻底放弃了一目了然）
  3. **宏平均 F1**（少数类权重被拉平，废物模型在这里会现原形）
  4. **危险错误数**（下面详述，这个最重要）

## 🔴 什么是"危险错误"

这个任务里两个方向的错，代价完全不对称：

  把 set_budget 判成 relax   → 用户说"放到8000"，系统按 7000×1.2=8400 算
                              **用户亲口给的数字被系统改掉了**
  把 relax 判成 set_budget   → 用户说"放宽点"，系统当成没说数字，反而去问
                              多问一句，烦但不致命

所以第一个方向单独计数，叫"改写了用户数字"。这条是零容忍项 ——
2026-11-13 那个 bug 就是这个方向。

## 用法

    python eval_intent.py                          # 用当前规则跑
    python eval_intent.py --model                  # 用当前大模型(0-shot)跑
    python eval_intent.py --model --limit 40       # 只跑 40 条（快）
    python eval_intent.py --both                   # 规则与模型对着跑
    python eval_intent.py --set adversarial        # 只跑对抗集
"""
import argparse
import collections
import io
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent                # noqa: E402
import build_intent_dataset as B  # noqa: E402

DATA = os.path.join(HERE, "lora_data")
LABELS = B.LABELS


# ══════════════════════════════════════════════════════════════
# 两种预测器
# ══════════════════════════════════════════════════════════════
def predict_rule(text):
    """当前生产规则。数字优先于动作词 —— 这就是 2026-11-13 修的那条优先级。"""
    has_num = bool(agent._budget_in_text(text))
    relax = agent.is_relax_budget(text)
    if has_num:
        return "set_budget"
    if relax:
        return "relax"
    return "none"


def predict_model(text, sys_prompt=None):
    """让大模型判断。只允许在封闭枚举里选 —— 不许它自由发挥。"""
    sp = sys_prompt or B.SYS_PROMPT
    try:
        out = agent.chat(
            [{"role": "system", "content": sp}, {"role": "user", "content": text}],
            fmt="json", num_predict=24, temperature=0)
        lab = (json.loads(out).get("budget_action") or "").strip()
        return lab if lab in LABELS else "none"
    except Exception:
        return "none"


# ══════════════════════════════════════════════════════════════
# 指标
# ══════════════════════════════════════════════════════════════
def score(pairs):
    """pairs: [(期望, 实际)]。返回指标字典。"""
    n = len(pairs)
    if not n:
        return {"n": 0}

    acc = sum(1 for w, g in pairs if w == g) / float(n)

    per = {}
    for lab in LABELS:
        tp = sum(1 for w, g in pairs if w == lab and g == lab)
        fn = sum(1 for w, g in pairs if w == lab and g != lab)
        fp = sum(1 for w, g in pairs if w != lab and g == lab)
        rec = tp / float(tp + fn) if (tp + fn) else 0.0
        pre = tp / float(tp + fp) if (tp + fp) else 0.0
        f1 = 2 * pre * rec / (pre + rec) if (pre + rec) else 0.0
        per[lab] = {"recall": rec, "precision": pre, "f1": f1,
                    "support": tp + fn, "tp": tp, "fp": fp, "fn": fn}

    macro_f1 = sum(per[l]["f1"] for l in LABELS) / float(len(LABELS))

    # 危险错误：用户给了明确数字，却被当成"放宽"（数字会被系统改掉）
    dangerous = [(w, g) for w, g in pairs if w == "set_budget" and g != "set_budget"]
    # 反向代价小但不该忽略：说放宽却被当成没说 → 会反问，烦人
    annoying = [(w, g) for w, g in pairs if w == "relax" and g == "set_budget"]

    return {"n": n, "acc": acc, "macro_f1": macro_f1, "per": per,
            "dangerous": len(dangerous), "annoying": len(annoying),
            "confusion": dict(collections.Counter(pairs))}


def bar(v, width=20):
    k = int(round(v * width))
    return "#" * k + "." * (width - k)


def show(name, m, verbose=False):
    if not m["n"]:
        print("%s：没有样本" % name)
        return
    print("=" * 66)
    print("%s  （%d 条）" % (name, m["n"]))
    print("=" * 66)
    print("总准确率     %.1f%%   %s" % (m["acc"] * 100, bar(m["acc"])))
    print("宏平均 F1    %.3f    %s" % (m["macro_f1"], bar(m["macro_f1"])))
    print("-" * 66)
    print("%-12s %8s %8s %8s %8s" % ("类别", "召回", "精确", "F1", "样本"))
    for lab in LABELS:
        d = m["per"][lab]
        flag = "" if d["support"] == 0 else ("  ← 全错" if d["recall"] == 0 else "")
        print("%-12s %7.1f%% %7.1f%% %8.3f %8d%s"
              % (lab, d["recall"] * 100, d["precision"] * 100,
                 d["f1"], d["support"], flag))
    print("-" * 66)
    # 这两行才是重点，放在最后让人记住
    print("危险错误（改写了用户给的数字）  %d 条   %s"
          % (m["dangerous"], "OK" if m["dangerous"] == 0 else "🔴 零容忍项"))
    print("烦人错误（说放宽却去反问）      %d 条" % m["annoying"])


# ══════════════════════════════════════════════════════════════
# 数据集加载
# ══════════════════════════════════════════════════════════════
def load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            try:
                lab = json.loads(r["messages"][2]["content"])["budget_action"]
            except Exception:
                continue
            rows.append((r["messages"][1]["content"], lab))
    return rows


def build_sets(which):
    """返回 {集合名: [(句子, 标签)]}。

    如果 lora_data 里已经导好了 jsonl 就用文件（这样和训练集严格同源）；
    没有就现场从 build_intent_dataset 生成，保证任何时候都能跑。
    """
    sets = {}
    want = ["eval", "adversarial"] if which == "all" else [which]
    for name in want:
        fp = os.path.join(DATA, "intent_%s.jsonl" % name)
        rows = load_jsonl(fp)
        if rows:
            sets[name] = rows
    if sets:
        return sets

    # 现场生成
    train, evalset, adv, _stats, _c = B.build()
    def conv(rows):
        return [(r["messages"][1]["content"],
                 json.loads(r["messages"][2]["content"])["budget_action"])
                for r in rows]
    allsets = {"eval": conv(evalset), "adversarial": conv(adv),
               "train": conv(train)}
    for name in want:
        sets[name] = allsets.get(name, [])
    if which == "manual":
        sets["manual"] = [(t, l) for t, l, _w in B.MANUAL]
    return sets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="store_true", help="用大模型预测")
    ap.add_argument("--both", action="store_true", help="规则和模型都跑，对比")
    ap.add_argument("--set", default="eval",
                    choices=["eval", "adversarial", "train", "manual", "all"],
                    help="跑哪个集合")
    ap.add_argument("--limit", type=int, default=0, help="限制条数（调试用）")
    ap.add_argument("--dump", default="", help="把错例写到这个文件")
    args = ap.parse_args()

    sets = build_sets(args.set)
    if not sets:
        print("没有可用的数据集。先跑 build_intent_dataset.py --write")
        return 1

    runners = []
    if args.both:
        runners = [("规则", predict_rule), ("模型0-shot", predict_model)]
    elif args.model:
        runners = [("模型0-shot", predict_model)]
    else:
        runners = [("规则", predict_rule)]

    all_bad = []
    for set_name, rows in sets.items():
        if args.limit:
            rows = rows[:args.limit]
        for runner_name, fn in runners:
            t0 = None
            pairs, bad = [], []
            import time
            t0 = time.time()
            for text, want in rows:
                got = fn(text)
                pairs.append((want, got))
                if want != got:
                    bad.append((text, want, got))
            dt = time.time() - t0
            m = score(pairs)
            if len(runners) > 1:
                print("\n[%s] 集合=%s" % (runner_name, set_name))
            show("%s · %s" % (runner_name, set_name), m)
            if runner_name == "模型0-shot":
                print("耗时         %.1fs（%.2fs/条）" % (dt, dt / max(1, len(rows))))
            all_bad += [(set_name, runner_name, t, w, g) for t, w, g in bad]
            print()

    if args.dump and all_bad:
        with io.open(args.dump, "w", encoding="utf-8", newline="\n") as f:
            for set_name, runner_name, text, want, got in all_bad:
                f.write(json.dumps({"set": set_name, "runner": runner_name,
                                    "text": text, "want": want, "got": got},
                                   ensure_ascii=False) + "\n")
        print("错例写到 %s（%d 条）" % (args.dump, len(all_bad)))

    return 0


if __name__ == "__main__":
    sys.exit(main())