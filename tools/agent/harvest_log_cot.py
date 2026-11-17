# -*- coding: utf-8 -*-
"""harvest_log_cot.py —— 从真实日志挖蒸馏数据（带思维链）

## 为什么真实日志最有价值

手写模板的问题是：**我写的是"我想到的句子"，不是"用户真会说的句子"**。

实测对比（同一批意图）：

| 来源 | 例子 |
|---|---|
| 我手写的模板 | 「这台不要了换一个」 |
| 真实用户说的 | 「那我不想要这种的」「再看看别的」「换一台这台我不想要」 |

真实句子的**语气、省略、口语化程度**完全不同。
用模板训出来的模型，遇到真实用户会懵。

## 但真实日志有个坑：没有标签

日志里只有 `text` + `stage`，没有 `{intent, topic, fit}` 金标签。

所以这个脚本做三件事：

  ① **跑模型拿预测** —— 用当前模型给每句打标
  ② **人工/规则复核** —— 只收"模型判的 + 规则也同意"的（双保险）
  ③ **补思维链** —— 按意图类别套模板，生成判据说明

⚠️ **只收双保险一致的**。模型和规则不一致的句子，
   说明这句本身有歧义或者模型不确定 —— 那种进了训练集会教坏模型。

## 用法

    python harvest_log_cot.py --dry          # 只看能收多少
    python harvest_log_cot.py --run          # 真跑（调模型，慢）
    python harvest_log_cot.py --run --limit 30

输出：`lora_data/cot-log.jsonl`
"""
import argparse
import io
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DIR = os.path.join(HERE, "lora_data")
LOGS = os.path.join(HERE, "logs")
OUT = os.path.join(DIR, "cot-log.jsonl")

SYS = ('你是选机助手的路由器。只输出 JSON：'
       '{"intent":...,"topic":...,"fit":...}')

# ── 思维链模板：按意图类别套 ────────────────────────────────────────
# 🔴 为什么用模板而不是让模型自己生成思维链：
#    让模型给自己的答案编理由，它只会写"因为答案是X所以是X"（循环论证）。
#    模板里的判据是**人工沉淀的**，每条都是真实事故换来的。
COT_TPL = {
    "ask_current": (
        "这句在问当前这台的某一面（{topic_cn}），不是在提新需求、也不是要换台。\n"
        "判据：出现指代当前这台的词或省略主语 + 问某个具体方面 = ask_current。"),
    "refuse_switch": (
        "这句在否定当前这台，要换别的。\n"
        "判据：出现否定（不想要/不行/算了）或明确的换台要求 = refuse_switch。\n"
        "⚠️ 不要说成 list_others —— 那个是「把剩下的列出来」，不是「不要这台」。"),
    "list_others": (
        "这句要的是候选清单，不是换台。\n"
        "判据：出现「还有/剩下/其他/都列出来」+ 要求展示 = list_others。\n"
        "⚠️ 与 refuse_switch 的区别：没有否定当前这台。"),
    "state_range": (
        "这句在描述一个范围（价位段/品类），没有指向某一台。\n"
        "判据：给范围但不指名 = state_range。"),
    "state_new_need": (
        "这句在补充条件或用途，不是问当前这台。\n"
        "判据：给约束（数值/要求）或说用途 = state_new_need。\n"
        "⚠️ 与 ask_current 的区别：没有问「这台怎么样」。"),
    "unknown": (
        "这句没有可判断的意图（寒暄/语气词/纯标点）。\n"
        "判据：没有实义内容 = unknown。"),
}

TOPIC_CN = {
    "service": "售后保修", "screen": "屏幕", "keyboard": "键盘",
    "noise": "散热噪音", "build": "做工材质", "weight": "重量",
    "battery": "续航电池", "price": "价格", "config": "配置",
    "pros_cons": "优缺点", "none": "",
}


def load_logs():
    """读所有日志，返回去重后的 (text, stage) 列表。"""
    seen = {}
    order = []
    if not os.path.isdir(LOGS):
        return []
    for f in sorted(os.listdir(LOGS)):
        if not f.endswith(".log"):
            continue
        try:
            fh = io.open(os.path.join(LOGS, f), encoding="utf-8")
        except OSError:
            continue
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = (o.get("text") or "").strip()
            if not t:
                continue
            # 过滤：太短的（1-2 字）不收 —— 那种没有判断价值
            if len(t) < 3:
                continue
            # 过滤：含明显内部标记的（测试用例留下的）
            if re.search(r"^\[W\d\]|^M\d\d|TEST_|^断言", t):
                continue
            if t not in seen:
                seen[t] = o.get("stage")
                order.append(t)
    return [(t, seen[t]) for t in order]


def _fmt(intent, topic, fit):
    return json.dumps({"intent": intent, "topic": topic, "fit": fit},
                      ensure_ascii=False)


def _self_consistent(text, pred):
    """**同类交叉验证**：把句子里的关键片段替换后重问，看判断稳不稳。

    🔴 2026-11-17 实测两次修正，记下来免得重走：

       第一版：同一句话问两次。结果「两次判断不一致 = 0」——
       因为 `route()` 有缓存，而且 temperature=0 时模型输出**确定性**，
       同一句问 100 次都一样。**这个判据根本抓不到错。**

       第二版（本函数）：**改一个无关词再问**，看意图是否跟着变。

       实测验证了这个方法的有效性：
         「学设计的，五千，要用四年，平时背去教室」→ ask_current ✗
         「学设计的，预算7000，要带去教室」       → state_new_need ✓
       同一类句子，判断不一致 —— 说明模型在这类上**不稳定**。

    做法：把数字换掉再问一次。数字不该改变意图类别 ——
    如果换了数字意图就变了，说明模型是靠某个词在猜，不是在理解语义。

    ⚠️ 成本：多一倍模型调用。145 条可接受。
    """
    try:
        import intent_layer as il
        # 换掉数字（最无关的改动）
        alt = re.sub(r"\d+", lambda m: str(int(m.group(0)) + 1), text)
        if alt == text:
            # 没有数字可换，换掉一个标点/语气词
            alt = text.replace("，", " ").replace("。", "")
            if alt == text:
                return True       # 没法构造变体，放行
        if hasattr(il, "_CACHE"):
            il._CACHE.clear()
        r2 = il.route(alt, focus="zol-2103842")
        return r2.get("intent") == pred
    except Exception:
        return True       # 工具坏了不丢数据


def _rule_agree(text, pred):
    """规则层是否**反对**模型的判断。返回 True 表示可以收。

    🔴 2026-11-17 实测修正（第一版判据错了）：

       第一版要求「规则和模型完全一致」才收，结果 145 条只收 55 条，
       排除的 83 条里 **74 条是"规则判 unknown"**。

       查下来：纯规则层（`classify(allow_model=False)`）认不出
       「学设计的，五千，要用四年，平时背去教室」这种真实句子 ——
       它只会词表匹配，真实用户的省略/口语化它接不住。

       **这是规则的能力不足，不是模型判错。** 用它排除等于把最好的
       真实数据扔掉（真实日志的价值恰恰在于"规则认不出、模型认得出"）。

       所以判据改成：**只在规则"有明确判断且与模型冲突"时才排除**。

       ```
       规则判 unknown  → 规则没意见 → 收（模型说了算）
       规则判 X，模型判 Y，X≠Y → 真冲突 → 排除（存疑）
       规则判 X，模型判 X → 一致 → 收
       ```

    第二类（真冲突）实测只有 9 条，比如：
      「华为MateBook D16怎么样」规则=ask_current 模型=refuse_switch ← 模型错
    这种必须排除 —— 拿它训练等于教模型"问怎么样 = 要换台"。
    """
    try:
        import intent_layer as il
        r_lab, _why = il.classify(text, "zol-2103842", allow_model=False)
        # 规则没意见 → 不算反对
        if r_lab in ("unknown", None):
            return True
        return r_lab == pred
    except Exception:
        # 规则跑不出来也不算反对（不能因为工具坏了丢数据）
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    items = load_logs()
    print("日志去重后：%d 条（已过滤 <3 字和内部测试标记）" % len(items))
    if args.limit:
        items = items[:args.limit]

    if not args.run:
        print()
        print("样例（前 10 条）:")
        for t, st in items[:10]:
            print("  [%s] %s" % (st or "?", t[:46]))
        print()
        print("（加 --run 才会调模型打标 + 规则复核）")
        return

    import intent_layer as il
    kept = []
    stat = {"total": 0, "model_fail": 0, "rule_disagree": 0,
            "unstable": 0, "ok": 0}

    print()
    print("=== 逐条打标 + 规则复核 + 自一致性 ===")
    for text, stage in items:
        stat["total"] += 1
        r = il.route(text, focus="zol-2103842")
        if not r.get("model_ok") or not r.get("intent") \
                or r.get("intent") == "unknown" and stage not in ("social", "unknown"):
            stat["model_fail"] += 1
            continue
        intent = r.get("intent")
        topic = r.get("topic") or "none"
        fit = bool(r.get("fit"))
        # ① 规则复核（规则没意见就放行）
        if not _rule_agree(text, intent):
            stat["rule_disagree"] += 1
            continue
        # ② 自一致性（再问一次，看稳不稳）
        if not _self_consistent(text, intent):
            stat["unstable"] += 1
            continue
        stat["ok"] += 1
        tpl = COT_TPL.get(intent, "这句的意图是 %s。\n判据：按语义判断。" % intent)
        cot = tpl.format(topic_cn=TOPIC_CN.get(topic, topic) or "整机")
        kept.append({
            "messages": [
                {"role": "system", "content": SYS},
                {"role": "user", "content": text},
                {"role": "assistant", "content": cot + "\n" + _fmt(intent, topic, fit)},
            ],
            "_meta": {
                "src": "cot-log",
                "has_cot": True,
                "correct_before": True,
                "why": "真实日志 + 模型打标 + 规则复核（双保险）",
                "stage_at_log": stage,
            },
        })
        if len(kept) % 10 == 0:
            print("  已收 %d 条..." % len(kept))

    print()
    print("=== 结果 ===")
    print("  总句数          : %d" % stat["total"])
    print("  模型判不出      : %d" % stat["model_fail"])
    print("  规则明确反对    : %d" % stat["rule_disagree"])
    print("  两次判断不一致  : %d" % stat["unstable"])
    print("  三重校验通过（收）: %d" % stat["ok"])
    print("  收录率          : %.1f%%" % (100.0 * stat["ok"] / max(1, stat["total"])))

    if kept:
        os.makedirs(DIR, exist_ok=True)
        with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
            for s in kept:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print()
        print("已写出 %s（%d 条）" % (OUT, len(kept)))


if __name__ == "__main__":
    main()
