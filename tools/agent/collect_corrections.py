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
