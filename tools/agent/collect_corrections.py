# -*- coding: utf-8 -*-
"""collect_corrections.py —— 收集「模型判断错了、被纠正」的样本

## 为什么单独做一个脚本

现有三个收集器都不管这件事：

| 脚本 | 管什么 | 不管什么 |
|---|---|---|
| `harvest_samples.py` | 给存量提问补**回答** | 不判对错 |
| `adversarial_harvest.py` | 手写模板造**对抗样本** | 不是真实误判 |
| `harvest_from_logs.py` | 从日志抽**真实句子** | 不知道模型判对没判对 |

**真实误判是最贵的训练数据** —— 它是模型在当前版本下确实会错的地方，
比手写模板更贴近真实分布。

## 记什么

一条样本 = 用户原话 + 模型判的 + 正确答案 + 为什么错。

```json
{
  "messages": [system, user, assistant(正确答案)],
  "_meta": {
    "wrong": "{\"intent\":\"ask_current\",\"topic\":\"battery\",\"fit\":true}",
    "correct_before": false,
    "why": "「限电500W」是补充条件，不是问电池；模型把"限电"当成了"电池"相关",
    "src": "correction:2026-11-17:power-limit",
    "found": "用户实测对话 chat-2026-11-17.log 第 11 条"
  }
}
```

`_meta.wrong` 是模型当时的错误输出 —— **这一栏很重要**，
将来做 DPO/拒绝采样时它就是负样本。

## 用法

    python collect_corrections.py --list          # 看已收集多少
    python collect_corrections.py --add           # 交互式添加一条
    python collect_corrections.py --from-log DATE # 从日志批量扫（需人工确认）

## 与 route-train.jsonl 的关系

本脚本产出 `lora_data/corrections.jsonl`（独立文件，不混进 route-train）。
`build_route_lora.py` 会把它并进训练集，并优先保证 eval 里不出现重复。

**为什么不直接写 route-train.jsonl**：
那个文件是 `build_route_lora.py` 的产物，手写进去下次重建就被覆盖了。
"""
import argparse
import datetime
import io
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DIR = os.path.join(HERE, "lora_data")
OUT = os.path.join(DIR, "corrections.jsonl")

SYS = ('你是选机助手的路由器。只输出 JSON：'
       '{"intent":...,"topic":...,"fit":...}')

VALID_INTENT = {"ask_current", "refuse_switch", "state_range",
                "state_new_need", "list_others", "unknown"}
VALID_TOPIC = {"service", "screen", "keyboard", "noise", "build", "weight",
               "battery", "price", "config", "pros_cons", "none"}

# ── 2026-11-17 实测误判（用户实测对话里抓到的）────────────────────────
# 每条都是**真跑出来的错**，不是猜的。
#
# 🔴 `cot` = 思维链（2026-11-17 加）。
#    用户要求：「不仅仅是问题和答案，也可以把你思考的思维链给他，
#    这样蒸馏效果不是更好吗」——对。
#    只给答案，学生学到"背结论"；给思维链，它学到"怎么判断"。
#
# 写 cot 的规矩（很重要，写错会教坏模型）：
#   ① 必须写出**关键区分点** —— 为什么不是另一个标签
#   ② 不许写"因为答案是 X 所以是 X"（循环论证）
#   ③ 要能推广 —— 用「补充条件」而不是「限电这个词」
#   ④ 3~5 句，别写成小作文
SEED = [
    {
        "text": "限电 500W",
        "wrong": {"intent": "ask_current", "topic": "battery", "fit": True},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "cot": ("这句在说一个使用条件（宿舍限电功率），不是在问这台的某一面。\n"
                "「电」字容易被带偏成电池/续航，但整句没有疑问形态，"
                "也没有问「这台的电池怎么样」。\n"
                "判据：给出一个具体数值条件 = 在补充约束，"
                "要记进槽位（dormWatt）并据此重判推荐。\n"
                "所以 intent 是 state_new_need，topic 给 none —— "
                "它不是在问某个面。"),
        "why": ("「限电500W」是在**补充一个条件**（宿舍限电功率），不是问电池。"
                "模型被「限电」里的「电」字带偏，判成了 topic=battery。"
                "正确动作：记下 dormWatt=500，重新跑推荐并说明够不够用。"),
        "found": "chat-2026-11-17.log 第 11 条（用户实测）",
    },
    {
        "text": "我想打游戏",
        "wrong": {"intent": "state_new_need", "topic": "none", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": True},
        "cot": ("这是**在说用途**，不是泛泛提需求。\n"
                "「我想打游戏」= 我的使用场景是游戏，"
                "所以下一步该拿 gaming 去判「当前这台够不够」。\n"
                "关键区分：fit=true 表示「拿用途问这台行不行」，"
                "这里虽然没出现「够用吗」，但说了用途就是在为匹配判断提供输入。\n"
                "所以 intent 是 state_new_need（补充了用途），fit 给 true。"),
        "why": ("「我想打游戏」是**说用途**，不是泛泛提需求 —— fit 该为 true，"
                "因为下一句就该判「当前这台打游戏够不够」。"
                "模型给了 fit=false，导致系统跳过匹配判断直接问预算。"),
        "found": "chat-2026-11-17.log 第 6 条（用户实测）",
    },
    {
        "text": "可以",
        "wrong": {"intent": "state_new_need", "topic": "none", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "cot": ("这句单看是个应答词，脱离上下文无法判断含义。\n"
                "上一句是「要不放宽预算？」，所以「可以」= 同意放宽预算。\n"
                "但**单看这句话本身**，它不包含任何新的信息量，"
                "判 state_new_need 是合理的（它在回应一个提议）。\n"
                "真正的问题是系统没有「放宽预算」这个动作 —— 那是代码缺口，"
                "不是分类错误。"),
        "why": ("这条**意图判对了**，但系统动作错了：上一句是"
                "「要不放宽预算？」，「可以」是**同意放宽**，"
                "该把 budget 调高并清空 seen，而不是重跑同一份 known。"
                "收录它是因为它是「省略主语的应答」典型，"
                "模型需要学会结合上一轮问句理解 —— 但单看这句话，"
                "判 state_new_need 是合理的，所以 correct 与 wrong 相同。"
                "⚠️ 这条更像「系统动作缺失」而非「模型判断错」，"
                "留作分析样本，训练时可按需过滤（_meta.actionable=false）。"),
        "found": "chat-2026-11-17.log 第 10 条（用户实测）",
        "actionable": False,
    },
    # ── 2026-11-17 10:57 用户第二次实测（换一台流程）──────────────────
    {
        "text": "打游戏",
        "wrong": {"intent": "ask_current", "topic": "config", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "cot": ("上一轮系统问「主要拿来干什么？」，用户答「打游戏」——"
                "这是**在回答用途问题**，不是问当前这台。\n"
                "「打游戏」没有疑问词，也没有指代这台的「它/这台」。\n"
                "判据：如果上一轮是反问，用户给的是答案，那就是在补槽位。\n"
                "⚠️ 不许因为「打游戏」像在问「能不能打游戏」就判 ask_current —— "
                "那是「能打游戏吗」的形态，不是「打游戏」。"),
        "why": ("上一轮系统问「主要拿来干什么？」，用户答「打游戏」——"
                "这是**在回答用途问题**，不是问当前这台。"
                "模型判成 ask_current 的后果很严重：系统以为用户在问"
                "MateBook D16 能不能打游戏，走了 answer_followup，"
                "**根本没做槽位抽取**，purpose 丢失，"
                "于是后面「换一台」时不知道用户要游戏本，"
                "又推回同一台轻薄本。"
                "对比：同样的话说成「我想打游戏」模型就判对了（state_new_need）——"
                "说明模型过度依赖「我想/我要」这类显式标记。"),
        "found": "chat-2026-11-17.log 第 17 条（用户实测）",
    },
    {
        "text": "拿他打游戏",
        "wrong": {"intent": "ask_current", "topic": "config", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "cot": ("「拿他打游戏」是**说用途**（我要拿这台去干什么），"
                "不是问「这台能不能打游戏」。\n"
                "「他」在这里指代的是「这台」，但整句的动词是「拿…去…」——"
                "是在陈述使用方式。\n"
                "关键区分：「能拿它打游戏吗」有疑问词「吗」→ ask_current；"
                "「拿它打游戏」是陈述 → state_new_need。\n"
                "所以 topic 给 none，因为不是在问某个面。"),
        "why": ("和「打游戏」同一类错。区别是这句带了「拿他」——"
                "「拿他打游戏」是**说用途**（我要拿这台去干什么），"
                "不是问「这台能不能打游戏」。"
                "模型被「他」这个指代词带偏，判成在问当前这台。"
                "正确理解：用户在给用途，系统该判 fit（够不够）或记下 purpose。"),
        "found": "chat-2026-11-17.log 第 13 条（用户实测）",
    },
    {
        "text": "那换一台吧",
        "wrong": {"intent": "ask_current", "topic": "none", "fit": False},
        "correct": {"intent": "refuse_switch", "topic": "none", "fit": False},
        "cot": ("「换一台」是明确的换台请求。\n"
                "「那」是承接上文的连接词，「吧」是缓和语气 —— "
                "都不改变「换」这个动作。\n"
                "判据：出现「换 + 量词（一台/个/款）」且没有否定它 = 拒绝当前这台。\n"
                "注意：不要说成 ask_current —— 用户不是在问这台怎么样，"
                "是在说要别的。"),
        "why": ("「换一台」是明确的换台请求，模型判成 ask_current 属于离谱错误。"
                "但注意：这句里有「那」+「吧」，是承接上一轮的口气，"
                "模型可能把它当成了「那（这台）怎么样吧」。"
                "判错的后果：系统没走换台流程，反而去问「是笔记本还是台式」，"
                "把已经说过的品类又问了一遍。"),
        "found": "chat-2026-11-17.log 第 14 条（用户实测）",
    },
    # ── 2026-11-17 22:30 对抗测试抓到的 5 条（harvest_cot.py --run）──
    # 判对率 18/23 = 78.3%，错的 5 条全在「补充条件」这一类。
    {
        "text": "宿舍限电 300W 能用吗",
        "wrong": {"intent": "ask_current", "topic": "config", "fit": True},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "cot": ("这是在说一个使用条件（限电功率），不是问这台的某一面。\n"
                "「能用吗」问的是「这台在我的条件下行不行」，"
                "不是在问某个部件怎么样 —— 所以 fit 该是 false，"
                "因为 fit 表示「拿用途问这台」，而这里是拿**供电条件**问。\n"
                "判据：给出具体约束 + 问可行性 = 补充条件，记进槽位后重判。"),
        "why": ("和「限电 500W」同一类。模型看到「能用吗」就判 ask_current，"
                "但用户给的是约束（300W），不是问某个面。"),
        "found": "harvest_cot.py --run 对抗测试",
    },
    {
        "text": "预算卡死 6000 不能再高了",
        "wrong": {"intent": "state_range", "topic": "price", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "cot": ("这是在设定预算上限，不是在说价位段。\n"
                "区别：「六七千的有什么推荐」= 给范围（state_range）；"
                "「卡死 6000 不能再高」= 给**当前对话的硬上限**（state_new_need）。\n"
                "「卡死」「不能再高」是明确的约束语气，"
                "意味着这个数字要写进 budget 槽位并约束后续所有推荐。\n"
                "判据：修改已有槽位（而非描述范围）= 补充条件。"),
        "why": ("模型判成 state_range（说价位段），但这是在改当前对话的预算上限。"
                "两者的实际区别：state_range 是「给我看看这个价位的」，"
                "state_new_need 是「我的预算是这个数」。"),
        "found": "harvest_cot.py --run 对抗测试",
    },
    {
        "text": "得能带去教室，太重的不行",
        "wrong": {"intent": "ask_current", "topic": "weight", "fit": True},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "cot": ("这是在提便携性要求，不是在问这台的重量。\n"
                "「得能」「不行」都是约束语气 —— 用户在划边界，不是在提问。\n"
                "「重」字容易被带偏成 topic=weight，"
                "但整句没有「多重」「沉不沉」这种问法。\n"
                "判据：说需求边界（得能…/…不行）= 补充条件。"),
        "why": ("模型被「重」字带偏成 topic=weight，还给了 fit=true。"
                "但用户是在划便携性边界，不是问重量是多少。"),
        "found": "harvest_cot.py --run 对抗测试",
    },
    {
        "text": "平时主要打游戏",
        "wrong": {"intent": "ask_current", "topic": "config", "fit": True},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": True},
        "cot": ("这是在陈述用途（游戏），不是问当前这台。\n"
                "「平时主要」是场景描述，没有疑问词。\n"
                "和「我想打游戏」的区别只是少了「我想」两个字 —— "
                "**但不能因为少了这两个字就判成问句**。\n"
                "判据：说出使用场景 = 补充用途（state_new_need），"
                "同时 fit 给 true（用途在手，下一步该判这台够不够）。\n"
                "⚠️ 关键：intent 是 state_new_need，不是 ask_current —— "
                "「说用途」和「问这台行不行」是两件事，"
                "后者要等系统拿用途去判。"),
        "why": ("模型判成 ask_current + topic=config。"
                "这是「过度依赖显式标记」的典型："
                "有「我想」就判对，没有就判错。"
                "注意这条 fit 仍然是 true —— 因为它确实提供了用途，"
                "只是 intent 不该是 ask_current。"),
        "found": "harvest_cot.py --run 对抗测试",
    },
    {
        "text": "再看看别的吧",
        "wrong": {"intent": "list_others", "topic": "none", "fit": False},
        "correct": {"intent": "refuse_switch", "topic": "none", "fit": False},
        "cot": ("「再看看别的」表示不想看当前这台了，要换。\n"
                "和「把符合条件的都列出来」的区别：\n"
                "  后者是**在当前结果里**要清单（list_others）\n"
                "  前者是**不要这台了**，要别的（refuse_switch）\n"
                "「吧」是缓和语气，不改变「不要这台」这个动作。\n"
                "判据：出现「别的」+ 暗示当前这台不合适 = refuse_switch。"),
        "why": ("模型判成 list_others。这两个容易混："
                "list_others 是「把剩下的列出来」，"
                "refuse_switch 是「这台不要了」。"
                "「再看看别的」语气上更像后者 —— 用户对当前这台不满意。"),
        "found": "harvest_cot.py --run 对抗测试",
    },
    # ── 2026-11-17 23:00 第二批对抗测试（50 条，82.0%）──────────────
    {
        "text": "除了这台还有吗",
        "wrong": {"intent": "refuse_switch", "topic": "none", "fit": False},
        "correct": {"intent": "list_others", "topic": "none", "fit": False},
        "cot": ("「除了这台还有吗」是在问**其他选项**，不是在否定当前这台。\n"
                "关键区分：\n"
                "  「除了这台还有吗」= 这台我要留着，但想看看别的（list_others）\n"
                "  「这台不要了换一个」= 明确否定（refuse_switch）\n"
                "前者的「除了」是排除法，后者的「不要」是否定。\n"
                "判据：问「除当前外还有什么」= list_others。"),
        "why": ("模型判成 refuse_switch。这两个容易混："
                "「除了」看着像排除（不要），但实际是「保留这台的同时也想看别的」。"
                "区分点：有没有明确的否定词（不要/不行/算了）。"),
        "found": "harvest_cot.py --run 第二批对抗测试",
    },
    {
        "text": "算了，看别的",
        "wrong": {"intent": "list_others", "topic": "none", "fit": False},
        "correct": {"intent": "refuse_switch", "topic": "none", "fit": False},
        "cot": ("「算了」是放弃当前这台的信号，「看别的」是换台动作。\n"
                "和「把剩下的列出来」的区别：\n"
                "  后者是**在已有结果里**要清单（list_others）\n"
                "  前者是**放弃当前这台**，要重新找（refuse_switch）\n"
                "「算了」这两个字是判据 —— 它表示否定/放弃。\n"
                "判据：出现「算了/不要了/不行」= refuse_switch。"),
        "why": ("模型判成 list_others。"
                "「看别的」和「列出来」表面都是要更多，但「算了」透露了否定态度。"
                "判据：有没有放弃当前这台的意思。"),
        "found": "harvest_cot.py --run 第二批对抗测试",
    },
    {
        "text": "不行不行",
        "wrong": {"intent": "ask_current", "topic": "none", "fit": False},
        "correct": {"intent": "refuse_switch", "topic": "none", "fit": False},
        "cot": ("重复的否定词，态度明确：不要当前这台。\n"
                "「不行」单独出现可能是在回答某个问题（比如「5000 够吗？」→「不行」），\n"
                "但**重复两次**是强调，表示明确的拒绝。\n"
                "判据：连续否定词 = 强烈拒绝 = refuse_switch。\n"
                "⚠️ 单次「不行」要结合上下文，重复的可以直接判。"),
        "why": ("模型判成 ask_current —— 这个错得比较离谱，"
                "「不行」是明确否定，不可能是问句。"
                "可能是模型把重复的否定当成了某种疑问。"),
        "found": "harvest_cot.py --run 第二批对抗测试",
    },
    {
        "text": "这个价位还有别的不",
        "wrong": {"intent": "list_others", "topic": "price", "fit": False},
        "correct": {"intent": "refuse_switch", "topic": "none", "fit": False},
        "cot": ("「还有别的不」在问替代选项，隐含对当前这台不满意 —— "
                "否则用户不会问「还有别的」。\n"
                "和「六七千的有什么推荐」的区别：\n"
                "  后者是描述一个价位段范围（state_range）\n"
                "  前者是**针对当前这台**问替代（refuse_switch）\n"
                "「还有」是关键 —— 它表示「除了这个」。\n"
                "⚠️ 这条与 list_others 的边界确实模糊。"
                "如果标注争议大，宁可不收 —— 歧义样本会教坏模型。"),
        "why": ("模型判成 list_others + topic=price。"
                "它抓住了「价位」这个词，但整句是「还有别的不」= 要替代品。"
                "这条本身有歧义，标注时已注明。"),
        "found": "harvest_cot.py --run 第二批对抗测试",
    },
    {
        "text": "预算卡死 6000 不能再高了",
        "wrong": {"intent": "state_range", "topic": "price", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "cot": ("这是在设定预算上限，不是在描述价位段。\n"
                "关键区分：\n"
                "  「六七千的有什么推荐」= 给一个范围，让系统去找（state_range）\n"
                "  「卡死 6000 不能再高」= 给当前对话的**硬上限**（state_new_need）\n"
                "「卡死」「不能再高」是约束语气 —— "
                "它要求这个数字写进 budget 槽位并约束后续所有推荐。\n"
                "判据：修改已有槽位（而非描述范围）= 补充条件。"),
        "why": ("模型判成 state_range（说价位段），"
                "但这是在改当前对话的预算上限。"
                "两者实际区别：state_range 是「给我看看这个价位的」，"
                "state_new_need 是「我的预算是这个数」。"),
        "found": "harvest_cot.py --run 第二批对抗测试",
    },
    # ── 2026-11-17 23:40 从真实日志交叉验证发现的「标点敏感」错误 ──
    #    这批最值得记：同一句话改个标点，模型判断就变了 ——
    #    说明它靠某个词在猜，不是在理解语义。
    {
        "text": "预算 5000，写代码",
        "wrong": {"intent": "state_range", "topic": "none", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": True},
        "cot": ("这句同时给了预算和用途，两个都是**条件**，所以是 state_new_need。\n"
                "关键区分：\n"
                "  「预算 5000」单独出现 = 只说价位段 → state_range\n"
                "  「预算 5000，写代码」= 给了预算**和用途** → state_new_need\n"
                "差别就在有没有用途词。用途在手就该判 fit（这台够不够写代码）。\n"
                "⚠️ 标点不改变语义 —— 「预算5000写代码」和「预算5000，写代码」"
                "是同一件事，判断必须一致。"),
        "why": ("实测发现的标点敏感错误：\n"
                "  「预算 5000 写代码」（无逗号）→ 模型判 state_new_need ✓\n"
                "  「预算 5000，写代码」（有逗号）→ 模型判 state_range ✗\n"
                "同一句话，只多了一个逗号，判断就变了 —— "
                "说明模型是靠「写代码」这个位置在猜，不是理解语义。"),
        "found": "harvest_log_cot.py 交叉验证（标点变体）",
    },
    {
        "text": "预算 8000 打游戏",
        "wrong": {"intent": "state_range", "topic": "none", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": True},
        "cot": ("预算 + 用途，两个都是条件 → state_new_need，且 fit 给 true。\n"
                "「打游戏」是明确用途，拿到它就该判当前这台够不够。\n"
                "⚠️ 与「一万以内打游戏的有什么推荐」区分：\n"
                "  后者带「有什么推荐」，是在**要清单** → state_range\n"
                "  前者只是给条件，没说要不要推荐 → state_new_need"),
        "why": ("模型判成 state_range。这类「预算+用途」的短句最容易混："
                "它既有价位数字（像 state_range），又有用途词（像 state_new_need）。"
                "判据：有没有「推荐/有什么/看看」这类**索取动作** —— "
                "有就是 state_range，没有就是纯给条件。"),
        "found": "harvest_log_cot.py 交叉验证",
    },
    {
        "text": "32G内存不能更改也行吧，够用了",
        "wrong": {"intent": "ask_current", "topic": "none", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": True},
        "cot": ("这是在**表达接受**（「也行吧」「够用了」），同时确认了一个条件（32G 内存）。\n"
                "不是在问当前这台的内存怎么样 —— 没有疑问词，也没有问「能不能加」。\n"
                "「也行吧」是让步接受，「够用了」是判断 —— 两句都是表态，不是提问。\n"
                "判据：表态（接受/拒绝/够用）而非提问 = 补充条件。"),
        "why": ("模型判成 ask_current。"
                "「32G内存」让它以为是问内存细节，"
                "但整句是「不能改也行 + 够用了」= 用户在做让步决定。"
                "这类「用户自己下结论」的句子容易被误判成提问。"),
        "found": "harvest_log_cot.py 交叉验证",
    },
    # ── 2026-11-18 11:04 用户实测（品牌名被当成专业）──────────────────
    #    这是本轮最严重的 bug：用户说「我是软件工程专业的」用了 7 轮，
    #    第 8 轮问「有没有机械革命的同配置的」，专业被改成了
    #    「机械设计制造及其自动化」—— 因为品牌名里含「机械」。
    {
        "text": "有没有机械革命的同配置的",
        "wrong": {"intent": "state_new_need", "topic": "none", "fit": False},
        "correct": {"intent": "ask_current", "topic": "config", "fit": False},
        "cot": ("「机械革命」是一个**笔记本品牌**，不是「机械设计制造及其自动化」专业。\n"
                "这句在问：有没有同品牌、同配置的机型 —— 是在问当前这台的替代品，\n"
                "属于 ask_current（问这台的配置/替代），不是提新需求。\n"
                "关键陷阱：中文里很多品牌名和专业名共用词汇：\n"
                "  机械革命（品牌） vs 机械设计（专业）\n"
                "  游戏本（品类） vs 游戏艺术设计（专业）\n"
                "判据：**先看是不是品牌名**。品牌名里的字不参与专业匹配。"),
        "why": ("实测事故：用户第 1 轮说「我是软件工程专业的」，"
                "第 8 轮问「有没有机械革命的同配置的」——"
                "「机械」触发了专业别名 '机械' → 机械设计制造及其自动化，"
                "硬约束从「16G 起、要能加装」变成「32G 起、要独显」，推荐逻辑全变。"
                "修法：① 删掉别名 '机械'，只留「机械设计/机械制造」等精确词；"
                "② 已确定的专业不许被单轮覆盖（除非用户明确说「我是学…」）。"),
        "found": "chat-2026-11-18.log 第 8 条（用户实测）",
    },
    # ── 2026-11-18 11:04 品牌串台：把惠普的机器说成机械革命 ──
    {
        "text": "有没有机械革命的同配置的",
        "wrong": {"intent": "state_new_need", "topic": "none", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "cot": ("⚠️ 这条是**品牌串台**样本，不是意图分类样本。\n"
                "当前机器是「暗影精灵11 锐龙版」，品牌=**惠普**。\n"
                "模型在回答里写「这台机器属于**机械革命**暗影精灵系列」——\n"
                "把用户上一句提到的品牌，安到了当前机器头上。\n"
                "判据：**用户提过的品牌 ≠ 当前这台的品牌**。\n"
                "品牌归属说错会直接误导售后/保值判断，比编数字更危险。"),
        "why": ("用户问「有没有机械革命的同配置的」，模型回答里说"
                "「这台机器属于机械革命暗影精灵系列」——"
                "暗影精灵是惠普的产品线，跟机械革命无关。"
                "模型把用户刚提的品牌名安到了当前机器上。"
                "已加 _brand_mismatch 判据（7/7 自测通过）拦截这类。"),
        "found": "chat-2026-11-18.log 第 8 条（用户实测）",
        "actionable": False,
    },
    # ── 2026-11-18 11:03 限电 400W 没被响应 ──
    {
        "text": "宿舍限电400瓦",
        "wrong": {"intent": "state_new_need", "topic": "none", "fit": False},
        "correct": {"intent": "state_new_need", "topic": "none", "fit": False},
        "cot": ("这是在补充一个使用条件（宿舍限电功率 400W），不是问这台的某一面。\n"
                "「限电」+ 具体数值 = 环境约束，要记进 dormWatt 槽位。\n"
                "关键：记下之后**必须据此重判当前这台够不够**，\n"
                "并说明库里有没有整机功耗数据。\n"
                "判据：给具体数值约束 = 补充条件（state_new_need）。"),
        "why": ("日志第 4 条：用户说「宿舍限电400瓦」，系统 stage=recommend，"
                "但回答里**一个字没提限电**，只把专业要求又念了一遍。"
                "这是「记了条件但没用」——槽位可能抽到了，但回答没体现。"
                "已加限电事实通道（有 dormWatt 时补一句："
                "适配器功率 + 明说没整机功耗 + 指路）。"),
        "found": "chat-2026-11-18.log 第 4 条（用户实测）",
        "actionable": False,
    },
]


def _fmt(d):
    """把 dict 转成 assistant 那一栏要的 JSON 字符串（键序固定）。"""
    return json.dumps({"intent": d["intent"], "topic": d["topic"],
                       "fit": d["fit"]}, ensure_ascii=False)


def load():
    out = []
    if os.path.exists(OUT):
        for line in io.open(OUT, encoding="utf-8"):
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def make_sample(text, correct, wrong=None, why="", found="", actionable=True,
                reasoning=""):
    """造一条纠错样本。

    🔴 2026-11-17：支持**思维链蒸馏**（`reasoning` 参数）。

    为什么加：
      只给答案的样本，学生学到的是"背结论"——
      它记住「限电500W → state_new_need」，但换个说法「宿舍限电300瓦」又不会了。
      带上思维链，它学的是**判断方法**：
      「这句话在补充一个条件，不是问某个面 → state_new_need」。

    格式（assistant 那一栏）：
      reasoning 非空 → "<思维链>\\n{JSON}"
      reasoning 为空 → "{JSON}"            （与旧样本兼容）

    解析端（`intent_layer._extract_json`）从后往前找 JSON，
    所以思维链在前、答案在后是安全的。
    """
    meta = {
        "correct_before": False,
        "why": why,
        "src": "correction:%s" % datetime.date.today().isoformat(),
        "found": found,
        "actionable": actionable,
    }
    if wrong:
        meta["wrong"] = _fmt(wrong)
    if reasoning:
        meta["has_cot"] = True
        content = reasoning.strip() + "\n" + _fmt(correct)
    else:
        content = _fmt(correct)
    return {
        "messages": [
            {"role": "system", "content": SYS},
            {"role": "user", "content": text},
            {"role": "assistant", "content": content},
        ],
        "_meta": meta,
    }


def validate(correct, wrong=None):
    """标签合法性检查 —— 非法标签进训练集会把模型带坏。"""
    errs = []
    if correct.get("intent") not in VALID_INTENT:
        errs.append("correct.intent 非法: %s" % correct.get("intent"))
    if correct.get("topic") not in VALID_TOPIC:
        errs.append("correct.topic 非法: %s" % correct.get("topic"))
    if not isinstance(correct.get("fit"), bool):
        errs.append("correct.fit 不是布尔")
    if wrong:
        if wrong.get("intent") not in VALID_INTENT:
            errs.append("wrong.intent 非法")
        if wrong.get("topic") not in VALID_TOPIC:
            errs.append("wrong.topic 非法")
        if correct == wrong:
            errs.append("correct 与 wrong 完全相同 —— 这条不是误判样本")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seed", action="store_true",
                    help="写入 2026-11-17 实测的 3 条误判")
    ap.add_argument("--add", action="store_true", help="交互式添加")
    args = ap.parse_args()

    os.makedirs(DIR, exist_ok=True)
    existing = load()

    if args.list or (not args.seed and not args.add):
        print("纠错样本：%d 条  (%s)" % (len(existing), OUT))
        if existing:
            print()
            for s in existing:
                u = s["messages"][1]["content"]
                a = json.loads(s["messages"][2]["content"])
                m = s.get("_meta") or {}
                print("  %-16s → %s/%s/fit=%s" % (
                    u[:14], a["intent"], a["topic"], a["fit"]))
                if m.get("wrong"):
                    w = json.loads(m["wrong"])
                    print("       模型错判：%s/%s/fit=%s" % (
                        w["intent"], w["topic"], w["fit"]))
        return

    if args.seed:
        have = {s["messages"][1]["content"] for s in existing}
        added = skipped = 0
        lines = []
        for item in SEED:
            if item["text"] in have:
                skipped += 1
                continue
            errs = validate(item["correct"], item["wrong"])
            if errs:
                print("  ✗ 跳过 %s：%s" % (item["text"], "; ".join(errs)))
                continue
            s = make_sample(item["text"], item["correct"], item["wrong"],
                            item["why"], item["found"],
                            item.get("actionable", True),
                            item.get("cot") or "")
            lines.append(s)
            added += 1
            _cot = "（带思维链）" if item.get("cot") else ""
            print("  ✓ %-14s %s错误 %s/%s → 正确 %s/%s/fit=%s" % (
                item["text"], _cot,
                item["wrong"]["intent"], item["wrong"]["topic"],
                item["correct"]["intent"], item["correct"]["topic"],
                item["correct"]["fit"]))
        if lines:
            with io.open(OUT, "a", encoding="utf-8", newline="\n") as f:
                for s in lines:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print()
        print("新增 %d 条，跳过 %d 条（已存在）。总计 %d 条。"
              % (added, skipped, len(existing) + added))
        return

    if args.add:
        print("交互式添加（直接回车放弃）")
        text = input("用户原话: ").strip()
        if not text:
            print("放弃")
            return
        wi = input("模型判的 intent: ").strip()
        wt = input("模型判的 topic: ").strip()
        wf = input("模型判的 fit (true/false): ").strip().lower() == "true"
        ci = input("正确 intent: ").strip()
        ct = input("正确 topic: ").strip()
        cf = input("正确 fit (true/false): ").strip().lower() == "true"
        why = input("为什么错: ").strip()
        wrong = {"intent": wi, "topic": wt, "fit": wf}
        correct = {"intent": ci, "topic": ct, "fit": cf}
        errs = validate(correct, wrong)
        if errs:
            print("✗ 校验失败：%s" % "; ".join(errs))
            return
        s = make_sample(text, correct, wrong, why, "手工录入")
        with io.open(OUT, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print("✓ 已添加")
        # 🔴 重要提醒：`lora_data/` 被 .gitignore 排除（训练数据体积大、可重建），
        #    所以写进 corrections.jsonl 的样本**只在本地**，换机器就没了。
        #    真正持久化的做法是把它加进本文件的 SEED 常量。
        print()
        print("⚠️ 这条只在本地（lora_data/ 不入库）。")
        print("   要永久保留，请把这条加进 collect_corrections.py 的 SEED 常量，")
        print("   然后 git commit。SEED 里的条目跑 --seed 可重建。")


if __name__ == "__main__":
    main()
