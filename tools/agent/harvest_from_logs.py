# -*- coding: utf-8 -*-
"""harvest_from_logs.py —— 从真实对话日志提取路由层训练样本

## 为什么走这条路

`lora_status.py` 盘点出的真实缺口（2026-11-16）：

    路由层 intent/topic/fit    149 条   ← 离 500 下限还差 351
    预算层 budget_action      1331 条   ← 够了，不用再补

所以缺的是**路由层**。而对抗模板是我手写的 152 条，再手写也很难翻三倍
且容易越写越像（泛化变差）。日志里有 4 天真实对话（367KB），
里面是**用户真的怎么说话** —— 这才是分布最真实的样本源。

## 标签从哪来（最关键的设计，不许偷懒）

🔴 **不能用模型自己的输出当标签** —— 那是自证，等于把模型的错误固化成标准答案。
`build_intent_dataset.py` 头部已经强调过这一点。

本脚本用**可判定的形态规则**打标，只保留**规则能确定**的句子：

| 形态 | 判定 | 为什么能确定 |
|---|---|---|
| 纯预算数字（"5000 到 7000"、"预算6000左右"） | state_range | 没有别的读法 |
| 纯品类词（"轻薄本"、"游戏本"） | state_range | 同上 |
| 空/标点/单字（"嗯"、"？"） | unknown | 同上 |
| 明确换台（"换一台"、"不行"、"太贵"） | refuse_switch | 带否定/换台动词 |
| 明确列清单（"都列出来"、"剩下的"） | list_others | 形态唯一 |

**其余一律丢弃**，包括"这个怎么样""够用吗"这类要靠上下文才能判的 ——
那些交给 `adversarial_harvest.py` 的模板（模板里标签是设计出来的，可靠）。
宁可少收，不许错标：错一条标签的代价 > 少十条数据。

## 与其它工具的分工

| 工具 | 样本来源 | 标签来源 | 规模 |
|---|---|---|---|
| `adversarial_harvest.py` | 手写模板 | 模板设计（可靠） | 152 |
| **本文** | **真实日志** | **形态规则（只收能确定的）** | 取决于日志 |
| `harvest_samples.py` | 真实日志 | 需要人工 | 慢 |

## 用法

    python harvest_from_logs.py                # 统计能收多少
    python harvest_from_logs.py --write        # 写 lora_data/log-route.jsonl
    python harvest_from_logs.py --show-reject  # 看被丢掉的长什么样（调规则用）
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
OUT_DIR = os.path.join(HERE, "lora_data")
OUT_FILE = os.path.join(OUT_DIR, "log-route.jsonl")

# ── 形态规则（只收能确定的）───────────────────────────────────────────
# 🔴 每条规则的 `why` 都要写清"为什么这句话只有这一种读法"，
#    写不出来的就不许加规则。

RE_EMPTY = re.compile(r"^[\s\W_]*$")                    # 纯标点/空白
RE_ACK = re.compile(r"^(嗯+|哦+|好+|在吗|在|行|可以|ok|OK|收到|谢谢)[\s!！。.~]*$")

# 纯预算表达：数字 + 预算类词，且**不含**任何配置/用途词
BUDGET_WORDS = r"(预算|价位|以内|左右|上下|以下|块|元|千|万|k|K)"
RE_BUDGET = re.compile(
    r"^[\s\d,，~\-—到至万kK块元" + BUDGET_WORDS + r"]+$")
# 🔴 纯数字**不算**预算：日志里真出现过 `"1"`（用户手滑），
#    把单字数字标成 state_range 是错标。至少要 3 位（千元级）或带预算词。
RE_BARE_NUM = re.compile(r"^\d{1,2}$")

# 「我的要求是…」「我要…」「得是…」—— 给条件，不是追问（M05 真实事故）
RE_REQUIRE = re.compile(r"^(我的要求是|我的需求是|我的条件是|我要|我得要|得是|必须是|希望能)")

# 「预算提高到/加到/控制在…」—— 更新槽位，即使带疑问形态
RE_RAISE = re.compile(r"(提高到|加到|提高到|控制在|改到|调整到)")

# 配置/用途词：出现就说明不只是"说价位段"，交给别的规则或丢弃
CONFIG_WORDS = ("内存", "硬盘", "显存", "独显", "核显", "屏", "键盘", "重",
                "续航", "接口", "CPU", "显卡", "跑", "做", "用", "装",
                "剪辑", "建模", "编程", "游戏", "设计")

# 纯品类词
CATS = ("轻薄本", "游戏本", "商务本", "台式机", "台式", "一体机", "工作站",
        "笔记本", "超极本")

# 换台（带否定或换台动词，且不含"换个X的"这种加条件形态）
RE_SWITCH = re.compile(
    r"(换一台|换一个|换别的|再换|重新推荐|不要这|不行|不喜欢|太丑|太贵|算了|不合适)")
RE_ADD_COND = re.compile(r"换个.{1,6}的")   # "换个32G的" 是加条件，必须排除

# 列清单
RE_LIST = re.compile(r"(都列|列出来|全列|剩下的|还有几个|都给我看|全部列)")


def classify_form(text):
    """用形态规则打标。返回 (intent, topic, fit, why) 或 None（判不了就丢弃）。

    topic 一律给 none：这些形态句都**不在问机器的某一面**。
    fit 一律 False：它们都不是"这台行不行"的提问形态。
    """
    t = (text or "").strip()
    if not t:
        return None

    # 1) 空/标点
    if RE_EMPTY.match(t):
        return "unknown", "none", False, "纯标点/空白，没有可执行信息"

    # 2) 应答词
    if RE_ACK.match(t):
        return "unknown", "none", False, "应答词，没有可执行信息"

    # 2.5) 纯数字 1-2 位 → 判不了（用户手滑），不许当预算
    if RE_BARE_NUM.match(t):
        return None

    # 3) 纯预算（先排除含配置/用途词的）
    if not any(w in t for w in CONFIG_WORDS) and RE_BUDGET.match(t) and re.search(r"\d{3,}", t):
        return "state_range", "none", False, "只说了价位段，没有别的读法"

    # 3.5) 「我的要求是…」= 给条件，不是追问当前这台（M05 真实事故）
    #      这类句子形态唯一：主语是"我的要求/需求/条件"，动作是"提出"。
    if RE_REQUIRE.match(t):
        return "state_new_need", "none", False, \
            "「我的要求/需求是…」是在给条件，不是追问当前这台（M05 事故）"

    # 3.6) 「预算提高到/控制在…」= 更新槽位，即使后面还带别的要求
    if RE_RAISE.search(t) and re.search(r"\d", t):
        return "state_range", "none", False, \
            "「提高到/控制在」是更新槽位，不是问价格（真实事故）"

    # 4) 纯品类（整句就是品类词或"要+品类"）
    core = t.strip("要 我要 想买 推荐 ")
    if core in CATS or (len(t) <= 8 and any(c in t for c in CATS)
                        and not any(w in t for w in CONFIG_WORDS if w not in ("游戏", "设计"))):
        return "state_range", "none", False, "只说了品类范围"

    # 5) 换台（排除"换个X的"）
    if RE_SWITCH.search(t) and not RE_ADD_COND.search(t):
        return "refuse_switch", "none", False, "带明确否定或换台动词"

    # 6) 列清单
    if RE_LIST.search(t):
        return "list_others", "none", False, "明确要求列出剩余候选"

    # 判不了 → 丢弃（宁可少收，不许错标）
    return None


def load_logs():
    """读所有 chat-*.log，产出 (text, focus_in, stage, file) 序列。"""
    out = []
    for path in sorted(glob.glob(os.path.join(LOG_DIR, "chat-*.log"))):
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = (r.get("text") or "").strip()
            if not t:
                continue
            out.append({"text": t, "focus": r.get("focus_in"),
                        "stage": r.get("stage"), "src": os.path.basename(path)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show-reject", action="store_true")
    args = ap.parse_args()

    rows = load_logs()
    print("日志里的用户句子：%d 条（%d 个文件）"
          % (len(rows), len({r["src"] for r in rows})))

    kept, rejected = [], []
    for r in rows:
        lab = classify_form(r["text"])
        if lab:
            r2 = dict(r, intent=lab[0], topic=lab[1], fit=lab[2], why=lab[3])
            kept.append(r2)
        else:
            rejected.append(r)

    # 去重（同一句话只留一条）
    seen, uniq = set(), []
    for r in kept:
        k = re.sub(r"\s+", "", r["text"]).lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    print("形态可判定：%d 条 → 去重后 %d 条" % (len(kept), len(uniq)))
    print("丢弃（形态判不了）：%d 条" % len(rejected))
    print()
    print("按标签分布：")
    for k, v in Counter(r["intent"] for r in uniq).most_common():
        print("  %-16s %d" % (k, v))

    print()
    print("样例（每类最多 3 条）：")
    by_lab = {}
    for r in uniq:
        by_lab.setdefault(r["intent"], []).append(r)
    for lab, items in by_lab.items():
        for r in items[:3]:
            print("  [%s] %-28s  ← %s" % (lab, r["text"][:26], r["why"]))

    if args.show_reject:
        print()
        print("被丢弃的样例（前 25 条，看规则要不要放宽）：")
        for r in rejected[:25]:
            print("  %s" % r["text"][:60])

    if args.write and uniq:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            for r in uniq:
                gold = json.dumps({"intent": r["intent"], "topic": r["topic"],
                                   "fit": r["fit"]}, ensure_ascii=False)
                f.write(json.dumps({
                    "messages": [
                        {"role": "system", "content": "你是选机助手的路由器。只输出 JSON："
                                                      '{"intent":...,"topic":...,"fit":...}'},
                        {"role": "user", "content": r["text"]},
                        {"role": "assistant", "content": gold},
                    ],
                    "_meta": {"src": r["src"], "stage": r["stage"],
                              "focus": r["focus"], "why": r["why"],
                              "labelBy": "形态规则（非模型自评）"},
                }, ensure_ascii=False) + "\n")
        print()
        print("写出 %d 条 → %s" % (len(uniq), OUT_FILE))
    elif not args.write:
        print()
        print("（演练模式。加 --write 写出训练集）")


if __name__ == "__main__":
    main()
