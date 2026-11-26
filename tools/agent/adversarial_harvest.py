# -*- coding: utf-8 -*-
"""adversarial_harvest.py —— 对抗采样 → LoRA 训练集

## 这个脚本解决什么

`export_lora_data.py` 统计出的现状（2026-11-16）：

    立即可用（有问答对）   117 条
    参考基线（风格对齐）   500~1000 条起步
    → 缺口 383 条

117 条训练出来的 LoRA 只会背句子，学不到规律。缺的不是「更多句子」，
而是**模型现在做错的那类句子** —— 把对的说对了没有训练价值，
只有「模型会错、正确答案是什么」这一对才是信息。

## 与既有工具的分工（别重复造）

| 工具 | 干什么 | 与本文的关系 |
|---|---|---|
| `adversarial_intent.py` | **测**：47 条固定对抗句，报规则/模型/协作的正确率 | 它只测不产数据 |
| `build_intent_dataset.py` | 从**测试用例**抽 intent 标签建数据集 | 标签来自人工标注的用例 |
| `harvest_samples.py` | 跑真实对话收集样本 | 需要人真的用起来 |
| **本文** | **造**：按标签体系**组合生成**对抗句 → 跑路由 → 只留模型错的 → 导出训练集 | 补缺口的主力 |

## 核心设计：只收「模型错 + 正确答案已知」的样本

一条样本要进训练集，必须同时满足：

1. **标签由代码确定，不靠模型自评** —— 句子是我们按模板造的，
   造的时候就知道正确 intent/topic/fit 是什么（模板即标签）。
   这是 `build_intent_dataset.py` 头部强调的「标签从哪来」的同一个原则。
2. **模型当前判错** —— 对的样本不进训练集（模型已经会了，教它浪费时间还伤泛化）。
3. **不是靠词表就能对** —— 规则能过的句子对模型没挑战；
   但注意：这里**不排斥**规则能过的，因为 LoRA 要学的是模型本身，
   部署时规则只是兜底，模型才是主路由。所以保留，但标记 `rule_ok`。

## 模板从哪来（这是质量的关键）

不是随机拼词，而是从 **`intent_layer.SYS_TMPL` 里那套「关键区分」** 反推 ——
那段提示词列出的每一条混淆点，都是一个真实的翻车现场：

    "换个32G的" = 加条件 → state_new_need（不是 refuse_switch）
    "换一台"   = 不要了 → refuse_switch
    "能跑PS吗" = fit=true 且 intent=ask_current

所以模板按「**同一句话的两种读法**」成对造：一个词之差，标签必须翻转。
这类最小对比对（minimal pair）对训练最有效。

## 用法

    python adversarial_harvest.py                  # 统计：能造多少、模型错多少
    python adversarial_harvest.py --write          # 写出 lora_data/adv-*.jsonl
    python adversarial_harvest.py --write --only-errors   # 只写模型判错的
    python adversarial_harvest.py --limit 20       # 先跑一小批看效果
"""
import argparse
import json
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import intent_layer as L  # noqa: E402

OUT_DIR = os.path.join(HERE, "lora_data")

# ── 标签 ────────────────────────────────────────────────────────────────
# 六个 intent / 十一个 topic / fit 三态（True/False/None）
INTENTS = ["ask_current", "refuse_switch", "state_range",
           "state_new_need", "list_others", "unknown"]
TOPICS = ["service", "screen", "keyboard", "noise", "build", "weight",
          "battery", "price", "config", "pros_cons", "none"]


# ── 模板：按「最小对比对」组织 ─────────────────────────────────────────
# 每个条目是 (句子, intent, topic, fit, 说明)
# 说明写的是「这句话为什么是这个标签」，来源是 SYS_TMPL 的「关键区分」段。
# 🔴 标签在模板里写死 —— 不许由模型判定后再当标签（那是自证）。
CASES = []


def add(text, intent, topic, fit, why):
    CASES.append({"text": text, "intent": intent, "topic": topic,
                  "fit": fit, "why": why})


# ── A. 「加条件」vs「换台」—— 最容易混的一对 ───────────────────────────
for spec in ["32G", "独显", "1T", "台式", "大屏", "轻一点"]:
    add("换个%s的" % spec, "state_new_need", "none", False,
        "「换个X的」是加条件，不是拒绝当前这台")
    add("要%s的" % spec, "state_new_need", "none", False, "提新要求")
    add("得有%s" % spec, "state_new_need", "none", False, "提新要求")
for refuse in ["换一台", "换一个", "这个不行", "不太喜欢", "太丑了", "太贵了",
               "算了", "再看看别的不", "再给我看几个", "能看看别的吗"]:
    add(refuse, "refuse_switch", "none", False, "不要当前这台，想换台")

# ── B. 「问这台能不能干某事」—— fit=true 的 ask_current ────────────────
for use in ["跑PS", "做设计", "剪视频", "编程", "跑MATLAB", "打游戏",
            "跑CAD", "做三维建模", "带得动PR", "开虚拟机"]:
    add("能%s吗" % use, "ask_current", "none", True,
        "问这台能不能满足他的用途 → fit=true，不是要新条件")
    add("这台%s够吗" % use, "ask_current", "none", True, "问匹配度")
    add("%s行不行" % use, "ask_current", "none", True, "问匹配度")

for q in ["够用吗", "合适吗", "适合我吗", "撑得住四年不", "能撑四年吗",
          "够我用到毕业吗"]:
    add(q, "ask_current", "none", True, "拿用途问这台行不行 → fit=true")

# ── C. 省略主语的追问 —— ask_current，topic 必须给对 ──────────────────
TOPIC_Q = {
    "service": ["保修多久", "售后怎么样", "品控如何", "返修率高吗", "质量靠谱吗"],
    "screen": ["屏幕怎么样", "屏幕素质如何", "色域多少", "分辨率多少", "屏幕是OLED吗"],
    "keyboard": ["键盘手感如何", "键程多长", "键盘背光吗"],
    "noise": ["风扇吵不吵", "噪音大吗", "满载会不会很响"],
    "build": ["做工怎么样", "手感如何", "什么材质", "外壳是金属吗"],
    "weight": ["多重啊", "重不重", "沉不沉", "好不好带", "多少公斤"],
    "battery": ["续航多久", "电池多少", "能用几个小时"],
    "price": ["多少钱", "现在什么价", "降了吗"],
    "config": ["什么配置", "接口有哪些", "内存多大", "硬盘多少G"],
    "pros_cons": ["有什么缺点", "短板是什么", "值不值", "有什么坑", "毛病多吗"],
}
for topic, qs in TOPIC_Q.items():
    for q in qs:
        add(q, "ask_current", topic, False,
            "省略主语的追问，topic 指向具体面；fit=false（不是在问匹配）")

# ── D. 「这个价算什么水平」vs「它保值吗」—— price 位置的两条路 ─────────
# 前者必须代码实算排名（intent=state_range），后者是机器属性（ask_current）
for q in ["这个价算什么水平", "这价格贵不贵", "同价位比怎么样",
          "这个价格合理吗", "算便宜吗"]:
    add(q, "state_range", "price", False,
        "问价格在市场上的位置 → 必须代码实算排名，不是问这台机器")
for q in ["它保值吗", "二手能卖多少", "残值怎么样", "几年后还值钱吗"]:
    add(q, "ask_current", "price", False,
        "保值/残值是机器属性，留在追问，别被价格通道抢走")

# ── E. 价位段 / 品类 / 条件范围 —— state_range ────────────────────────
for q in ["六七千的", "一万以内", "三千左右的", "预算五千", "两千到三千",
          "这个价位的"]:
    add(q, "state_range", "none", False, "说价位段，不指向某一台")
for q in ["轻薄本", "游戏本", "要台式的", "不要游戏本", "商务本"]:
    add(q, "state_range", "none", False, "说品类范围")

# ── F. 列清单 —— list_others ──────────────────────────────────────────
for q in ["剩下的都列出来", "还有几个", "都给我看看", "全部列一下",
          "把剩下的发我"]:
    add(q, "list_others", "none", False, "要把剩余候选列出来")

# ── G. 什么都没说清 —— unknown ────────────────────────────────────────
for q in ["嗯", "哦", "在吗", "。。。", "?", "好", "嗯嗯", "在"]:
    add(q, "unknown", "none", False, "没有可执行的信息")

# ── H. 「我的要求是…」—— 给条件不是追问（真实事故 M05）────────────────
for q in ["我的要求是轻薄", "我的需求是大屏", "我的条件是预算七千",
          "我希望是独显"]:
    add(q, "state_new_need", "none", False,
        "「我的要求是…」是在给条件，不是追问当前这台（M05 真实事故）")

# ── I. 「提高到/加到/控制在」—— 更新槽位，即使带疑问形态 ──────────────
for q in ["预算提高到7000", "预算加到八千", "价格控制在六千以内",
          "预算提高到7000，屏幕素质我有要求"]:
    add(q, "state_range", "price", False,
        "「提高到/控制在」是更新槽位，不是问价格（真实事故）")

# ── J. 「还有更X的吗」—— 换台不是列清单 ──────────────────────────────
for q in ["还有更便宜的吗", "还有更轻的吗", "有没有更强的", "还有更好的吗"]:
    add(q, "refuse_switch", "none", False,
        "「还有更X的吗」= 当前这台不满足 → 换台，不是 list_others（4f2 事故）")

# ── K. 整机泛问 —— pros_cons 不算具体面 ──────────────────────────────
for q in ["联想小新16如何", "这台怎么样", "天选6好用吗"]:
    add(q, "ask_current", "pros_cons", False,
        "整机泛问走 intro 引导，不倒具体面缺点")


# ── 执行与导出 ─────────────────────────────────────────────────────────
# 🔴 `focus` 必须给真实机器 id —— 这是首版最大的设计错误。
#
#    `_ctx_of(focus)` 在 focus 为空时往提示词里写「（当前没有在讨论任何机器）」。
#    在这种上下文下问「屏幕怎么样」，模型判 state_new_need **是对的**，
#    是我的期望标签错了 —— 没有"这台"的时候，问屏幕只能是提新需求。
#
#    实测代价：首版 152 条里 103 条"判错"，其中 83 条是 ask_current。
#    加上真实 focus 后才是真正在测「他是不是在问当前这台」。
#
#    这也是 `_ctx_of` 文档里写过的：「实测不给上下文时，'这个机器怎么样'这种句子
#    模型全判错 —— 它不知道'这个'指什么。给了之后从 75% 升到 90%。」
DEFAULT_FOCUS = "spec-legion-y9000p-2026"   # 拯救者 Y9000P 2026，池里第一台


def route_of(text, focus=None):
    """跑一次真实路由。返回 (intent, topic, fit, model_ok, 耗时, 错误)。"""
    t0 = time.time()
    try:
        r = L.route(text, focus)
    except Exception as e:  # noqa: BLE001
        return None, None, None, False, time.time() - t0, str(e)
    return (r.get("intent"), r.get("topic"), r.get("fit"),
            bool(r.get("model_ok")), time.time() - t0, "")


def norm_fit(v):
    return v if isinstance(v, bool) else None


def judge_correct(pred, truth):
    """逐字段比对。topic 只在 intent 是 ask_current 时才有意义。"""
    p_i, p_t, p_f = pred
    if p_i != truth["intent"]:
        return False, "intent %s≠%s" % (p_i, truth["intent"])
    if truth["intent"] == "ask_current":
        # ask_current 才校验 topic；其余 intent 下 topic 不参与判定
        if truth["topic"] != "none" and p_t != truth["topic"]:
            return False, "topic %s≠%s" % (p_t, truth["topic"])
    if norm_fit(p_f) != truth["fit"]:
        return False, "fit %s≠%s" % (p_f, truth["fit"])
    return True, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--only-errors", action="store_true",
                    help="只写模型判错的（训练价值最高）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-interval", type=float, default=0.0)
    ap.add_argument("--focus", default=DEFAULT_FOCUS,
                    help="当前讨论的机器 id（默认池里第一台）。"
                         "空字符串 = 故意不给上下文，用于测「无上下文」分支")
    args = ap.parse_args()

    focus = args.focus or None
    cases = CASES
    if args.limit:
        cases = cases[: args.limit]

    print("对抗样本模板：%d 条（标签由模板写死，不靠模型自评）" % len(cases))
    print("focus：%s" % (focus or "（无上下文，故意）"))
    print()

    results = []
    ok_n = err_n = fail_n = 0
    t_all = time.time()
    for i, c in enumerate(cases, 1):
        p_i, p_t, p_f, model_ok, dt, err = route_of(c["text"], focus)
        if err or not model_ok:
            fail_n += 1
            rec = dict(c, pred_intent=p_i, pred_topic=p_t, pred_fit=p_f,
                       model_ok=False, correct=False, diff="模型不可用: %s" % err)
            results.append(rec)
            print("[%d/%d] %-22s 模型不可用 %s" % (i, len(cases), c["text"][:20], err[:40]))
            continue
        good, diff = judge_correct((p_i, p_t, p_f), c)
        rec = dict(c, pred_intent=p_i, pred_topic=p_t, pred_fit=p_f,
                   model_ok=True, correct=good, diff=diff)
        results.append(rec)
        if good:
            ok_n += 1
        else:
            err_n += 1
            print("[%d/%d] %-22s ✗ %s" % (i, len(cases), c["text"][:20], diff))
        if args.min_interval:
            time.sleep(args.min_interval)

    total = len(results)
    print()
    print("=" * 66)
    print("模型正确 %d / %d = %.1f%%   判错 %d   不可用 %d"
          % (ok_n, total, 100.0 * ok_n / total if total else 0, err_n, fail_n))
    print("耗时 %.1fs（%.2fs/条）" % (time.time() - t_all, (time.time() - t_all) / max(total, 1)))

    # 按错误类型分组，看模型系统性偏向
    if err_n:
        print()
        print("错误分布（按期望 intent）：")
        import collections
        c2 = collections.Counter(r["intent"] for r in results if not r["correct"])
        for k, v in c2.most_common():
            print("  %-16s %d" % (k, v))
        print()
        print("错误明细：")
        for r in results:
            if not r["correct"] and r["model_ok"]:
                print("  %-24s 期望 %-14s/%-9s/fit=%-5s → 实得 %-14s/%-9s/fit=%s"
                      % (r["text"][:22], r["intent"], r["topic"], r["fit"],
                         r["pred_intent"], r["pred_topic"], r["pred_fit"]))

    if args.write:
        os.makedirs(OUT_DIR, exist_ok=True)
        # 训练集格式：与 export_lora_data 的 train.jsonl 保持一致（messages 三段式）
        rows = []
        for r in results:
            if not r["model_ok"]:
                continue
            if args.only_errors and r["correct"]:
                continue
            gold = json.dumps({"intent": r["intent"], "topic": r["topic"],
                               "fit": r["fit"]}, ensure_ascii=False)
            rows.append({
                "messages": [
                    {"role": "system", "content": "你是选机助手的路由器。只输出 JSON："
                                                  '{"intent":...,"topic":...,"fit":...}'},
                    {"role": "user", "content": r["text"]},
                    {"role": "assistant", "content": gold},
                ],
                "_meta": {"correct_before": r["correct"], "diff": r["diff"],
                          "why": r["why"]},
            })
        tag = "errors" if args.only_errors else "all"
        path = os.path.join(OUT_DIR, "adv-%s.jsonl" % tag)
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print()
        print("写出 %d 条 → %s" % (len(rows), path))

    if not args.write:
        print()
        print("（演练模式。加 --write 写训练集；加 --only-errors 只收模型判错的）")


if __name__ == "__main__":
    main()
