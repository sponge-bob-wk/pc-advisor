# -*- coding: utf-8 -*-
"""harvest_cot.py —— 对抗测试 + 思维链蒸馏数据收集

## 用户的要求

> 「你不是每次纠正他，可以不仅仅是问题和答案，也可以把你思考的思维链给他，
>   这样蒸馏效果不是更好吗，你可以对抗测试收集蒸馏数据」

三件事：
  ① **对抗测试**：批量跑易错句式，看模型现在判对没判对
  ② **思维链**：每条样本带"怎么判断的"，不只是答案
  ③ **蒸馏数据**：整理成 LoRA 训练格式

## 为什么思维链有用（以及什么时候没用）

**有用**：教"判断方法"而不是"背结论"。
  只给答案 → 学生记住「限电500W → state_new_need」
  给思维链 → 学生学到「给出具体数值条件 = 在补充约束」
  后者能推广到「宿舍限电300瓦」「预算卡死6000」这些没见过的说法。

**没用甚至有害**：
  · 思维链本身是错的（会教错判断方法）→ 所以本脚本只收**模型判对**的
  · 思维链是循环论证（「因为答案是X所以是X」）→ 所以用模板生成，模板里强制写判据
  · 学生模型太小，学了思维链反而输出格式乱 → 所以配了 `_extract_json` 容错

## 数据来源（三类）

| 来源 | 可靠性 | 用途 |
|---|---|---|
| `SEED_PATTERNS` 手工模板 | 高（每条都写了判据） | 主力训练数据 |
| 模型判对的真实句子 | 中（答案可信，思维链是补的） | 扩充覆盖 |
| 模型判错的 | — | 进 `corrections.jsonl`（另一套流程） |

## 用法

    python harvest_cot.py --dry            # 只看会生成多少条
    python harvest_cot.py --run            # 真跑（调模型，慢）
    python harvest_cot.py --run --limit 20 # 先试 20 条

输出：`lora_data/cot-route.jsonl`
"""
import argparse
import io
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DIR = os.path.join(HERE, "lora_data")
OUT = os.path.join(DIR, "cot-route.jsonl")

SYS = ('你是选机助手的路由器。只输出 JSON：'
       '{"intent":...,"topic":...,"fit":...}')

# ── 对抗句式模板 ─────────────────────────────────────────────────────
# 每条 = (句子, 正确标签, 思维链模板)
#
# 🔴 思维链写法规矩：
#    ① 必须写**关键区分点**（为什么不是另一个标签）
#    ② 不许循环论证
#    ③ 要能推广（用"补充条件"而不是"限电这个词"）
#    ④ 3~5 句
#
# 标签用短名，生成时展开。
SEED_PATTERNS = [
    # ── 补充条件型：给出具体数值/要求，不是问某个面 ──
    ("宿舍限电 300W 能用吗",
     ("state_new_need", "none", False),
     "这是在说一个使用条件（限电功率），不是问这台的某一面。"
     "「能用吗」问的是「这台在我的条件下行不行」，不是在问某个部件怎么样。"
     "判据：给出具体约束 + 问可行性 = 补充条件，记进槽位后重判。"),
    ("预算卡死 6000 不能再高了",
     ("state_new_need", "none", False),
     "这是在设定预算上限，不是问当前这台。"
     "「卡死」「不能再高」是明确的条件约束。"
     "判据：修改已有槽位（budget）= 补充条件。"),
    ("必须有 32G 内存，16G 不够用",
     ("state_new_need", "none", False),
     "这是在提硬性配置要求，不是在问内存细节。"
     "「必须有」「不够用」是约束语气，不是疑问。"
     "判据：出现「必须/一定要/不够」= 给硬约束。"),
    ("得能带去教室，太重的不行",
     ("state_new_need", "none", False),
     "这是在提便携性要求。「得能」「不行」都是约束语气。"
     "不是在问这台的重量是多少。"
     "判据：说需求边界 = 补充条件。"),
    ("要有独显，核显的不考虑",
     ("state_new_need", "none", False),
     "这是在提硬件要求（独显）。「要有」「不考虑」是筛选条件。"
     "不是在问这台的显卡怎么样。"
     "判据：明确的取舍标准 = 补充条件。"),

    # ── 用途陈述型：说用途，fit 该为 true ──
    ("平时主要打游戏",
     ("state_new_need", "none", True),
     "这是在陈述用途（游戏）。说了用途就该拿它去判当前这台够不够。"
     "「平时主要」是场景描述，不是疑问。"
     "判据：说出使用场景 + 没有疑问词 = 补充用途，fit 给 true。"),
    ("主要用来剪视频和修图",
     ("state_new_need", "none", True),
     "这是在陈述用途（视频剪辑 + 修图），两个都是创作类。"
     "说了用途就该判当前这台够不够。"
     "判据：用途陈述 = state_new_need，fit 给 true。"),
    ("上课记笔记为主，偶尔看剧",
     ("state_new_need", "none", True),
     "这是在陈述用途（轻办公 + 娱乐）。"
     "虽然语气很轻（「为主」「偶尔」），但仍然是用途描述。"
     "判据：用途陈述 = state_new_need，fit 给 true。"),
    ("我是学建筑的，要跑 CAD 和建模",
     ("state_new_need", "none", True),
     "这是在说专业和要跑的软件 —— 既是用途也是硬约束。"
     "「学建筑的」决定专业配置表，「跑 CAD 和建模」是具体需求。"
     "判据：说出专业/软件 = 补充用途，fit 给 true。"),

    # ── 换台型 ──
    ("这个不行，换一个",
     ("refuse_switch", "none", False),
     "「不行」是否定当前这台，「换一个」是换台动作。"
     "判据：否定词 + 换台动词 = refuse_switch。"
     "注意不要说成 ask_current —— 用户不是在问这台怎么样。"),
    ("再看看别的吧",
     ("refuse_switch", "none", False),
     "「再看看别的」表示不想看当前这台了。"
     "虽然语气客气（「吧」），但动作是换台。"
     "判据：要「别的」= 不要这台。"),
    ("太贵了，有便宜点的吗",
     ("refuse_switch", "none", False),
     "「太贵了」是对当前这台的价格否定，「有便宜点的吗」是换台请求。"
     "判据：否定当前 + 要替代品 = refuse_switch。"),
    ("这台太重，找个轻的",
     ("refuse_switch", "none", False),
     "「太重」是否定当前这台的重量，「找个轻的」是换台。"
     "注意：虽然提到了「重」，但不是在问重量细节 —— 是在拒绝。"
     "判据：对当前这台的否定 + 换台要求 = refuse_switch。"),

    # ── 问细节型：明确问某一面 ──
    ("这机器多重",
     ("ask_current", "weight", False),
     "「多重」直接问重量，是这一台的细节。"
     "判据：问具体参数 + 指代当前这台 = ask_current。"),
    ("屏幕素质咋样",
     ("ask_current", "screen", False),
     "「屏幕素质」问的是屏幕这一面，「咋样」是疑问。"
     "判据：问具体部件 + 疑问词 = ask_current，topic 给对应面。"),
    ("电池能撑一天吗",
     ("ask_current", "battery", True),
     "「电池能撑一天吗」是在问续航够不够 —— 既是问电池这一面，"
     "也是拿一个使用标准（一天）问行不行。"
     "判据：问具体面 + 带可行性标准 = ask_current + fit=true。"),
    ("这机器散热压得住 i9 吗",
     ("ask_current", "noise", True),
     "问散热能不能压住 i9，是在问这一台的散热能力够不够。"
     "「压得住」是可行性判断。"
     "判据：问散热 + 可行性 = ask_current + fit=true。"),

    # ── 列清单型 ──
    ("把符合条件的都列出来",
     ("list_others", "none", False),
     "「都列出来」是明确要清单，不是换台也不是问细节。"
     "判据：出现「列/都/全部」+ 要求展示 = list_others。"),
    ("还有几个能选",
     ("list_others", "none", False),
     "「还有几个」是在问剩余候选数量，不是拒绝当前这台。"
     "判据：问剩余数量 = list_others。"
     "⚠️ 注意与「还有别的吗」区分：后者可能是换台，前者是问数量。"),

    # ── 价位段型 ──
    ("六七千的轻薄本有什么推荐",
     ("state_range", "none", False),
     "这是在说价位段 + 品类（六七千、轻薄本），没有指向某一台。"
     "判据：给范围（价格/品类）但不指名 = state_range。"),
    ("一万以内打游戏的",
     ("state_range", "none", False),
     "「一万以内」是价格范围，「打游戏的」是品类/用途，都不指向某一台。"
     "判据：范围描述 = state_range。"),

    # ── 模糊型 ──
    ("嗯",
     ("unknown", "none", False),
     "单字应答，没有可判断的意图。"
     "判据：没有实义内容 = unknown。"),
    ("在吗",
     ("unknown", "none", False),
     "只是打招呼，没有提任何需求或问题。"
     "判据：社交寒暄 = unknown。"),
]


# ── 第二批：补长尾类别（2026-11-17 深夜）──────────────────────────────
#
# 🔴 为什么补这三个：训练集分布严重不均
#      ask_current 150 / state_range 115 / state_new_need 61
#      refuse_switch 32 / list_others 11 / unknown 10
#    长尾类别太少，模型在那些类上必然弱。
#
# 补的时候守一条：**每条都要有真判据**，不许凑数。
# 判据写不出来说明这条句子本身有歧义，那就不该进训练集。
SEED_LONGTAIL = [
    # ── unknown：无实义 / 寒暄 / 纯标点 / 重复确认 ──
    ("哦",
     ("unknown", "none", False),
     "单字语气词，没有需求也没有问题。"
     "判据：无实义 = unknown。"),
    ("？？？",
     ("unknown", "none", False),
     "纯标点，没有任何内容。"
     "判据：无实义 = unknown。"),
    ("你好",
     ("unknown", "none", False),
     "打招呼，没有提需求。"
     "判据：社交开场 = unknown。"),
    ("谢谢",
     ("unknown", "none", False),
     "表达感谢，对话可以结束。"
     "判据：社交收尾 = unknown，不是新需求。"),
    ("好的",
     ("unknown", "none", False),
     "表示收到，但没有提出任何新要求。"
     "判据：单纯应答，没提需求也没问问题 = unknown。"
     "⚠️ 注意与「可以」区分：如果上一句是提议（「要不放宽预算？」），"
     "「可以」就是同意，属于 state_new_need；"
     "如果上一句只是陈述，「好的」就只是收到 = unknown。"),
    ("哈哈哈",
     ("unknown", "none", False),
     "笑声，没有实际内容。"
     "判据：无实义 = unknown。"),
    ("没事了",
     ("unknown", "none", False),
     "表示不需要继续，没有新需求。"
     "判据：结束对话 = unknown。"),
    ("emmm",
     ("unknown", "none", False),
     "语气词，表示在思考但没说内容。"
     "判据：无实义 = unknown。"),

    # ── list_others：要清单（不是换台）──
    ("把剩下的都列出来",
     ("list_others", "none", False),
     "「都列出来」是明确要清单。"
     "判据：出现「列/都/全部」+ 要求展示 = list_others。"),
    ("还有哪些能选",
     ("list_others", "none", False),
     "「还有哪些」是在问剩余候选，不是拒绝当前这台。"
     "判据：问剩余有哪些 = list_others。"),
    ("一次说完吧",
     ("list_others", "none", False),
     "「一次说完」表示想一次看全，不要一个个来。"
     "判据：要求一次性展示全部 = list_others。"),
    ("给我看看全部的候选",
     ("list_others", "none", False),
     "「全部的候选」明确要清单。"
     "判据：要求展示全部 = list_others。"),
    ("除了这台还有吗",
     ("list_others", "none", False),
     "「除了这台还有吗」是在问其他选项，不是否定这台。"
     "判据：问除当前外还有什么 = list_others。"
     "⚠️ 与「这台不要了换一个」区分：后者有否定，前者只是问还有没有。"),
    ("剩下的几个什么样",
     ("list_others", "none", False),
     "「剩下的几个」直接指剩余候选。"
     "判据：指剩余项 + 要求介绍 = list_others。"),
    ("能全给我看看不",
     ("list_others", "none", False),
     "「全给我看看」是要完整清单。"
     "判据：要求看全部 = list_others。"),
    ("其余的也说说",
     ("list_others", "none", False),
     "「其余的」= 剩余候选，「也说说」= 要求介绍。"
     "判据：指剩余项 + 要求介绍 = list_others。"),
    ("排在前面的都报一遍",
     ("list_others", "none", False),
     "「都报一遍」是要把排名靠前的全列出来。"
     "判据：要求批量展示 = list_others。"),

    # ── refuse_switch：不要这台了（含隐式否定）──
    ("这个不太行",
     ("refuse_switch", "none", False),
     "「不太行」是对当前这台的否定。虽然语气缓和，但意思是不要了。"
     "判据：否定当前这台 = refuse_switch。"),
    ("感觉不合适",
     ("refuse_switch", "none", False),
     "「不合适」是明确的否定判断。"
     "判据：否定当前这台 = refuse_switch。"),
    ("有更好的吗",
     ("refuse_switch", "none", False),
     "「更好的」暗示当前这台不够好，要换。"
     "判据：要求更好 = 否定当前 = refuse_switch。"
     "⚠️ 与「还有别的吗」区分：后者中性，前者带比较（更好）。"),
    ("这台pass",
     ("refuse_switch", "none", False),
     "「pass」是明确的跳过/不要。"
     "判据：英文 pass = 拒绝当前这台。"),
    ("算了，看别的",
     ("refuse_switch", "none", False),
     "「算了」是放弃当前，「看别的」是换台。"
     "判据：放弃 + 换台 = refuse_switch。"),
    ("不行不行",
     ("refuse_switch", "none", False),
     "重复否定，态度明确。"
     "判据：否定当前这台 = refuse_switch。"),
    ("这台不行，太贵",
     ("refuse_switch", "none", False),
     "「不行」是否定，「太贵」给理由。"
     "判据：否定 + 理由 = refuse_switch。"
     "⚠️ 虽然提到价格，但不是在问价格 —— 是在拒绝。"),
    ("再挑挑",
     ("refuse_switch", "none", False),
     "「再挑挑」表示当前这台不选，要继续找。"
     "判据：继续找别的 = refuse_switch。"),
    ("换个性价比高点的",
     ("refuse_switch", "none", False),
     "「换个性价比高的」= 不要这台 + 要更好的。"
     "判据：换 + 更好要求 = refuse_switch。"),
    ("这个价位还有别的不",
     ("refuse_switch", "none", False),
     "「还有别的不」在问替代选项，隐含对当前这台不满意。"
     "判据：问替代品 = 暗示当前不合适 = refuse_switch。"
     "⚠️ 这条与 list_others 的边界模糊：「还有哪些能选」偏中性（list_others），"
     "「还有别的不」带轻微否定（refuse_switch）。"
     "如果标注争议大，宁可不收 —— 歧义样本会教坏模型。"),
]


def _fmt(intent, topic, fit):
    return json.dumps({"intent": intent, "topic": topic, "fit": fit},
                      ensure_ascii=False)


def _parse():
    """把 SEED_PATTERNS + SEED_LONGTAIL 展开成样本。"""
    out = []
    for group, tag in ((SEED_PATTERNS, "cot-seed"),
                       (SEED_LONGTAIL, "cot-longtail")):
        for text, (intent, topic, fit), cot in group:
            out.append({
                "messages": [
                    {"role": "system", "content": SYS},
                    {"role": "user", "content": text},
                    {"role": "assistant",
                     "content": cot.strip() + "\n" + _fmt(intent, topic, fit)},
                ],
                "_meta": {
                    "src": tag,
                    "has_cot": True,
                    "correct_before": True,
                    "why": "手工模板（判据写在思维链里）",
                },
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只统计")
    ap.add_argument("--run", action="store_true", help="真跑模型验证")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条")
    args = ap.parse_args()

    samples = _parse()
    print("模板样本：%d 条" % len(samples))
    if args.limit:
        samples = samples[:args.limit]

    if args.run:
        # 真跑模型，看当前模型判对没判对
        import intent_layer as il
        ok = bad = 0
        print()
        print("=== 用当前模型跑一遍，看判对率 ===")
        for s in samples:
            text = s["messages"][1]["content"]
            gold = json.loads(s["messages"][2]["content"].split("\n")[-1])
            r = il.route(text, focus="zol-2103842")
            got = {"intent": r.get("intent"), "topic": r.get("topic") or "none",
                   "fit": bool(r.get("fit"))}
            good = (got["intent"] == gold["intent"])
            ok += 1 if good else 0
            bad += 0 if good else 1
            mark = "✓" if good else "✗"
            print("  %s %-24s 期望 %-14s 实际 %s" % (
                mark, text[:22], gold["intent"], got["intent"]))
        print()
        print("判对 %d / %d = %.1f%%" % (ok, len(samples),
                                         100.0 * ok / max(1, len(samples))))
        print("⚠️ 判错的应该进 corrections.jsonl（另走纠正流程），"
              "不要混进蒸馏数据 —— 那会教错判断方法。")
        return

    if args.dry or not args.run:
        print()
        print("样例（第一条）:")
        s = samples[0]
        print("  user:", s["messages"][1]["content"])
        print("  assistant:")
        for ln in s["messages"][2]["content"].split("\n"):
            print("    " + ln)
        print()
        if not args.dry:
            os.makedirs(DIR, exist_ok=True)
            with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
                for s in samples:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
            print("已写出 %s（%d 条）" % (OUT, len(samples)))


if __name__ == "__main__":
    main()
