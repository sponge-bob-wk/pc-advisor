# -*- coding: utf-8 -*-
"""意图层对抗测试 · 评分尺 + 样本收集（2026-11-13）

## 这个脚本解决什么

用户的话是开放的，我们永远无法"测完"。所以不能指望一次性写对，
只能建一条**持续变好的回路**：

    跑对抗集 → 找出模型判错的 → 人工标对 → 进训练集 → 微调 → 再跑

这个脚本就是这条回路的入口，一次跑完给出四样东西：

  1. **谁在什么情况下错**（规则 / 模型 / 协作 三方对比）
  2. **错的方向是什么**（是有系统性偏向，还是随机噪声 —— 前者能训掉）
  3. **新缺口清单**（模型也判错的 → 待人工标注 → 训练集）
  4. **回归保护**（上一轮修好的，这轮不许退）

## 🔴 为什么对抗集要自己长

固定不变的测试集有个陷阱：**改代码去凑测试**。
人一旦知道标准答案，就会不自觉地让代码贴着它改。
所以每次跑完，新发现的错例要**追加**进去，让尺子越来越严，
而不是让代码越来越刁。

## 用法

    python adversarial_intent.py                 # 跑全套（规则/模型/协作）
    python adversarial_intent.py --only model    # 只跑模型
    python adversarial_intent.py --harvest       # 跑完把错例写进待标注池
    python adversarial_intent.py --add "句子|标签|理由"   # 追加一条对抗样本
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
sys.path.insert(0, HERE)
import agent                 # noqa: E402
import intent_cases_real as R  # noqa: E402
import intent_layer as IL    # noqa: E402

DATA = os.path.join(HERE, "lora_data")
HARVEST = os.path.join(DATA, "intent_need_label.jsonl")

# 🔴 判错代价不对称（沿用 test_intent.py 的定调，这里再钉一遍）：
#    · 把 ASK 判成别的 → 用户被打回第一句，烦但补一句能救
#    · 把别的判成 ASK → 用户提的新需求被当成追问，**需求直接丢了**
#    · 把 UNKNOWN 判成别的 → 其实无害（只是不反问）
#    · 把别的判成 UNKNOWN → 用户说得很清楚却被反问，最伤体验
#    ⇒ 所以"实质错误"按下面的等价类算，不是看标签字面
def impact_sig(intent):
    """把 6 个标签压成 3 个"行为等价类"。

    为什么可以压：`core.build_recommendation` 那条路上，
    state_range / state_new_need / refuse_switch / list_others **都往下走推荐**，
    只有 ask_current（追问这台）和 unknown（反问）是不同路。
    实测验证过：这四类意图在 run() 里给出相同的 stage 和相同的机器。
    """
    if intent == agent.INTENT_ASK_CURRENT:
        return "ask"
    if intent == agent.INTENT_UNKNOWN:
        return "unknown"
    return "go"          # 往下推荐


# ══════════════════════════════════════════════════════════════
# 三方预测器
# ══════════════════════════════════════════════════════════════
def pred_rule(t, focus):
    return agent._classify_intent_rule(t, focus)


def pred_model(t, focus):
    lab, _ = IL.model_intent(t, focus)
    return lab


def pred_hybrid(t, focus):
    lab, _ = IL.classify(t, focus)
    return lab


RUNNERS = {
    "rule": ("规则（155 条词表）", pred_rule),
    "model": ("模型 0-shot", pred_model),
    "hybrid": ("协作（规则+模型）", pred_hybrid),
}


# ══════════════════════════════════════════════════════════════
# 打分
# ══════════════════════════════════════════════════════════════
def score(rows, pred, focus):
    """rows: [(句子, 期望标签, 理由)]。返回指标。"""
    n = len(rows)
    exact = 0
    sig_ok = 0
    bad = []
    # 按标签统计召回
    per = {}
    for t, want, why in rows:
        got = pred(t, focus)
        per.setdefault(want, [0, 0])
        per[want][1] += 1
        hit_exact = (got == want)
        hit_sig = (impact_sig(got) == impact_sig(want))
        if hit_exact:
            exact += 1
            per[want][0] += 1
        if hit_sig:
            sig_ok += 1
        else:
            bad.append({"text": t, "want": want, "got": got, "why": why,
                        "want_sig": impact_sig(want), "got_sig": impact_sig(got)})
    return {
        "n": n,
        "exact": exact / float(n) if n else 0,
        "sig": sig_ok / float(n) if n else 0,
        "bad": bad,
        "per": {k: (v[0] / float(v[1]) if v[1] else 0, v[1]) for k, v in per.items()},
    }


def show(name, m, verbose=False):
    print("=" * 70)
    print("%s   （%d 条）" % (name, m["n"]))
    print("=" * 70)
    print("标签一致率   %.1f%%" % (m["exact"] * 100))
    print("实质正确率   %.1f%%   ← 按实际分支影响算，这个才是用户感知到的"
          % (m["sig"] * 100))
    print("-" * 70)
    print("%-16s %8s %8s" % ("期望意图", "召回", "样本"))
    for lab, (rec, cnt) in sorted(m["per"].items(), key=lambda x: -x[1][1]):
        print("%-16s %7.1f%% %8d" % (lab, rec * 100, cnt))
    if m["bad"]:
        print("-" * 70)
        print("实质错误 %d 条：" % len(m["bad"]))
        for b in m["bad"][:15]:
            print("  %-24r 应=%-14s 得=%-14s (%s)"
                  % (b["text"][:22], b["want"], b["got"], b["why"][:18]))
        if len(m["bad"]) > 15:
            print("  ... 另有 %d 条" % (len(m["bad"]) - 15))


# ══════════════════════════════════════════════════════════════
# 收样本
# ══════════════════════════════════════════════════════════════
def harvest(rows, results):
    """把"模型也判错"的句子收进待标注池。

    🔴 只收模型判错的，不收规则判错的。
       为什么：规则判错的如果模型判对了，那已经没有缺口了，
       拿它当训练样本只是让模型把已经会的再学一遍。
       真正要训的是**模型也不会**的那些 —— 那才是能力边界。
    """
    seen = set()
    if os.path.exists(HARVEST):
        with io.open(HARVEST, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        seen.add(json.loads(line)["text"])
                    except Exception:
                        pass

    model_bad = {b["text"] for b in results.get("model", {}).get("bad", [])}
    new_rows = []
    for t, want, why in rows:
        if t not in model_bad:
            continue
        if t in seen:
            continue
        new_rows.append({
            "text": t,
            "want": want,         # 已有标签（对抗集是人工标的，可直接用）
            "why": why,
            "src": "adversarial",
            "_need_human": False,  # 这批标签已有，可以直接进训练集
        })

    # 再从真实日志里捡模型判 uncertain 的句子
    log_new = harvest_from_logs(seen, limit=200)

    os.makedirs(DATA, exist_ok=True)
    mode = "a" if os.path.exists(HARVEST) else "w"
    with io.open(HARVEST, mode, encoding="utf-8", newline="\n") as f:
        for r in new_rows + log_new:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print("=" * 70)
    print("收样本")
    print("=" * 70)
    print("对抗集里模型判错的   %d 条（标签已有，可直接进训练集）" % len(new_rows))
    print("真实日志里新捡到的   %d 条（标签待人工确认）" % len(log_new))
    print("写入 %s" % HARVEST)
    return new_rows + log_new


def harvest_from_logs(seen, limit=200):
    """从真实日志里捡句子 —— **那才是用户真的会说的话**。

    🔴 为什么必须收真实日志：对抗集是我编的，编的时候带着我的想象；
       日志是用户真的打出来的字，里面有我没想到的说法。
       实测例子：「这点钱怕是不够」「有点贵但还挺喜欢的」——
       这类"情绪+需求混在一起"的句子，自己造很难造像。
    """
    import glob
    out = []
    for fp in sorted(glob.glob(os.path.join(HERE, "logs", "chat-*.log"))):
        try:
            fh = io.open(fp, encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            t = (rec.get("text") or "").strip()
            if not (2 <= len(t) <= 60) or t in seen:
                continue
            # 只收"有实义但意图不明"的 —— 那句才是缺口
            han = sum(1 for c in t if "\u4e00" <= c <= "\u9fa5")
            if han < 3:
                continue
            m, _ = IL.model_intent(t, rec.get("focus_in"))
            if m != agent.INTENT_UNKNOWN:
                continue      # 模型判得出，暂时不算缺口
            seen.add(t)
            out.append({"text": t, "want": None, "why": "日志里模型判不出",
                        "src": "log", "_need_human": True})
            if len(out) >= limit:
                return out
        fh.close()
    return out


def add_case(spec):
    """"句子|标签|理由" → 追加到对抗集源文件。

    ⚠️ 这里改的是 `intent_cases_real.py` 的 CASES 列表，用文本插入而不是
       import 后 append —— 因为那个文件是**数据源**，运行时的修改不持久。
    """
    parts = [p.strip() for p in spec.split("|")]
    if len(parts) < 2:
        print("格式：--add \"句子|标签|理由\"，标签取值：")
        print("  " + " / ".join(IL._LABEL_MAP.keys()))
        return 1
    text, lab = parts[0], parts[1]
    why = parts[2] if len(parts) > 2 else "手动追加"
    if lab not in IL._LABEL_MAP:
        print("标签 %r 不合法，可选：%s" % (lab, list(IL._LABEL_MAP.keys())))
        return 1
    fp = os.path.join(HERE, "intent_cases_real.py")
    src = io.open(fp, encoding="utf-8").read()
    marker = "]\n\n\ndef label_to_const"
    if marker not in src:
        print("找不到插入点，请手动编辑 intent_cases_real.py")
        return 1
    line = '    ("%s", "%s", "%s"),\n' % (text, lab, why)
    src = src.replace(marker, line + marker, 1)
    io.open(fp, "w", encoding="utf-8", newline="\n").write(src)
    print("已追加：%s → %s" % (text, lab))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(RUNNERS.keys()), default="")
    ap.add_argument("--harvest", action="store_true", help="收样本进待标注池")
    ap.add_argument("--add", default="", help="追加一条对抗样本")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.add:
        return add_case(args.add)

    rows = R.CASES[:args.limit] if args.limit else R.CASES
    focus = R.FOCUS

    which = [args.only] if args.only else ["rule", "model", "hybrid"]
    results = {}
    for key in which:
        name, fn = RUNNERS[key]
        t0 = time.time()
        m = score(rows, fn, focus)
        dt = time.time() - t0
        results[key] = m
        print()
        show(name, m)
        print("耗时 %.1fs（%.2fs/条）" % (dt, dt / max(1, len(rows))))

    # 一句话结论 —— 别让人自己去比表
    if len(results) >= 2:
        print()
        print("=" * 70)
        print("结论")
        print("=" * 70)
        best = max(results.items(), key=lambda kv: kv[1]["sig"])
        for k, v in results.items():
            mark = "  ← 最好" if k == best[0] else ""
            print("  %-22s 实质正确率 %.1f%%%s"
                  % (RUNNERS[k][0], v["sig"] * 100, mark))
        if "model" in results and "rule" in results:
            gap = results["model"]["sig"] - results["rule"]["sig"]
            if gap > 0.1:
                print("\n  ⇒ 模型明显更好（+%.0f 个百分点）：这一层该由模型主导" % (gap * 100))
            elif gap < -0.1:
                print("\n  ⇒ 规则明显更好（+.0f 个百分点）：这一层暂不该换模型" % 0)
            else:
                print("\n  ⇒ 两者接近：优先用快的那个，模型留给难例")

    if args.harvest:
        print()
        harvest(rows, results)

    return 0


if __name__ == "__main__":
    sys.exit(main())