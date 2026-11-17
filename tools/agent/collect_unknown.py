# -*- coding: utf-8 -*-
"""模型接管日志 · 攒微调燃料（2026-11-13）

## 它解决什么

协作层的设计是"规则确定时用规则，不确定时问模型"。
那么**每一次问模型，都是一次"规则不会"的证据**。

这个脚本干两件事：

1. 从对话日志里，把每一轮用户原话跑一遍规则，标出：
     · 规则确定（rule）      → 这些规则已经够了，不用训
     · 规则不认识（model）   → **这些才是要训的**
2. 把"规则不认识"的句子单独攒成一个文件，供人工标注。

## 为什么这一步比训模型本身更重要

微调的价值不在"把规则会的再学一遍"，而在**学会规则不会的那些说法**。
如果训练集里 90% 是规则已经认得的句子，训出来的模型只是规则的慢速复制品 ——
花 0.23 秒/条换一个和正则一样准的东西，是纯亏。

所以要训的必须是**规则词表之外**的句子。这个脚本就是筛子。

## 用法

    python collect_unknown.py              # 统计，看看现在有多少
    python collect_unknown.py --write      # 写进 lora_data/unknown_*.jsonl
"""
import argparse
import glob
import io
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent          # noqa: E402
import budget_intent as BI  # noqa: E402

OUT = os.path.join(HERE, "lora_data")


def _is_meta(s):
    """判断这句话是不是"测试脚本在描述自己的意图"，而不是用户在说话。

    判据只用**元语言特征**，不靠主观判断（宪法第 4 条：判据要有分辨力）：
      · 第三人称描述测试行为：应/必须/不许/检测/断言/校验/复读
      · 出现"这条/该用例/期望"这类指代测试本身的词
      · 以冒号结尾的半句式（"复读检测：..."）
    """
    META = re.compile(
        r"应(该|能|当|如实|有)|必须|不许|不得|禁止|检测|断言|校验|"
        r"复读|期望|用例|这条|该条|判据|不能出现|要求是|测试")
    if META.search(s):
        return True
    # 「复读检测：按句号切，同一句出现两次以上。」这类带冒号的说明
    if "：" in s and re.search(r"检测|校验|判据|规则", s):
        return True
    return False


def iter_user_texts():
    """遍历所有日志里的用户原话，带上下文。"""
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
            u = (rec.get("text") or "").strip()
            if 1 <= len(u) <= 120:
                yield u, rec.get("last_stage"), os.path.basename(fp)
        fh.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=30)
    ap.add_argument("--set", default="all",
                    choices=["all", "eval", "adversarial", "manual"])
    args = ap.parse_args()

    # 除了日志，也扫测试集（测试集里的句子是人工写的，覆盖更广）
    texts = []
    seen = set()
    for u, stage, src in iter_user_texts():
        k = u.strip()
        if k in seen:
            continue
        seen.add(k)
        texts.append((k, "log", stage or "-"))

    # 🔴 不从测试脚本挖（2026-11-13 定的）。
    #    试过：test_attack*.py 里字符串既有用户原话（"六千万像素"），
    #    也有用例名和断言说明（"预算不丢"、"降级成回头问预算"、
    #    "先说预算，再说别的条件，预算不能被冲掉"）。
    #    两类都用同一个语法写（普通字符串），靠正则分不开 ——
    #    我加过元语言词表，能从 235 条滤到 201 条，但滤不干净。
    #
    #    留一点脏样本的代价：模型会学到"用户会用第三人称描述自己"。
    #    这个错误的破坏力远超"少挖 200 条样本"。
    #    ⇒ 只采信**真实日志**：那里每一条都是真人真的打出来的字。
    #    测试集里的用户原话另有用途（评测集），不当训练燃料。

    rule_cnt = model_cnt = 0
    unknown = []
    # 🔴 关键闸：只有"看起来可能跟预算有关"的句子才值得进待标注池。
    #    第一版没加这个闸，结果 1041 条"规则不认识"里绝大多数是
    #    「换一台」「保修几年」「封装内存什么意思」—— 它们**本来就不该动预算**，
    #    标成 none 只是把"规则正确"又标了一遍，对训练毫无增益。
    #
    #    教训：**"规则不认识" ≠ "规则错了"。** 前者是覆盖率问题，
    #    后者才是准确率问题。要训的是后者。
    BUDGET_HINT = re.compile(
        r"钱|价|预算|贵|便宜|花|加|多|少|够|省|万|千|百|块|元|"
        r"松|紧|付|给|出|承受|负担|手头|到账|发了|奖学金|工资|生活费")

    for u, src, extra in texts:
        lab, why = BI.rule_decide(u)
        if lab is not None:
            rule_cnt += 1
            continue
        # 规则不认识，但先看它是不是"跟钱沾边"
        if not BUDGET_HINT.search(u):
            # 明确与预算无关 —— 它不是缺口，是**规则正确地对它保持沉默**
            continue
        model_cnt += 1
        unknown.append({"text": u, "src": src, "extra": extra,
                        "_label": None,      # 待人工标注
                        "_note": ""})

    n = len(texts)
    print("=" * 62)
    print("规则不认识的比例 —— 这就是微调要补的缺口")
    print("=" * 62)
    print("总句子        %4d" % n)
    print("规则确定      %4d  (%.0f%%)  ← 规则已经够了，训这些是浪费" % (rule_cnt, 100.0 * rule_cnt / max(1, n)))
    print("规则不认识    %4d  (%.0f%%)  ← **要训的就是这些**" % (model_cnt, 100.0 * model_cnt / max(1, n)))
    print()
    if unknown:
        print("规则不认识的样本（前 %d 条）：" % min(args.show, len(unknown)))
        for r in unknown[:args.show]:
            print("  %-38r  [%s/%s]" % (r["text"][:36], r["src"], r["extra"]))
        if len(unknown) > args.show:
            print("  ... 另有 %d 条" % (len(unknown) - args.show))

    if args.write:
        os.makedirs(OUT, exist_ok=True)
        fp = os.path.join(OUT, "unknown_pool.jsonl")
        with io.open(fp, "w", encoding="utf-8", newline="\n") as f:
            for r in unknown:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("\n写出 %s（%d 条，_label 待人工填）" % (fp, len(unknown)))
        print("填标签的规矩：只许填 relax / set_budget / none 三个值之一；")
        print("             拿不准的留 None —— 宁可少一条，不许瞎标。")

    return 0


if __name__ == "__main__":
    sys.exit(main())