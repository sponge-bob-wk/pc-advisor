# -*- coding: utf-8 -*-
"""adversarial_v2.py —— 整链对抗测试 + 训练集采集

用户要求：
> 「做对抗测试收集训练集你的纠错思维链存训练集后续 Lora 微调」
> 「理解和输出都靠模型，规则兜底，别动不动给我扯到规则」

## 与旧版 harvest_cot.py 的区别

旧版：只测意图分类（intent/topic/fit）。
新版：测**整条链**（意图 → 槽位 → 约束 → 筛选 → 审计 → 表达），
     每条失败都记录**完整纠错思维链**。

## 为什么要有思维链

只给"问题+正确答案"，学生学的是背结论：
  「接口够不够 → ask_current」记住了，但「散热行不行」又不会。

给思维链，它学的是判断方法：
  「问的是某个参数面 → ask_current + topic；不是问用途匹配 → fit=False」

## 用法

    python adversarial_v2.py --dry        # 只看用例
    python adversarial_v2.py --run        # 跑一遍，报告失败
    python adversarial_v2.py --collect    # 跑 + 把失败的写成训练样本
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
import intent_layer as il

OUT = os.path.join(HERE, "lora_data", "adversarial-v2.jsonl")

# ── 用例：每条 = (用户原话, 期望的 intent, 期望的 topic, 期望的 fit,
#                失败时的纠错思维链) ──
#
# 思维链写法（同 collect_corrections.py 的四条规矩）：
#   ① 必须写出关键区分点 —— 为什么不是另一个标签
#   ② 不许写"因为答案是 X 所以是 X"
#   ③ 要能推广
#   ④ 3~5 句
CASES = [
    # ═══ 参数主题 vs 用途匹配（2026-11-21 实测事故）═══
    ("接口够不够", "ask_current", "config", False,
     "问的是「这台有哪些接口」，不是「这台适不适合我的用途」。\n"
     "「够不够」容易被判成 fit=True（以为在问匹配度），但它问的是接口数量。\n"
     "判据：出现具体参数面（接口/散热/屏幕/键盘）→ topic 给对应面，fit 给 False。\n"
     "⚠️ 对比「这个能打游戏吗」——没有参数面，才是真问用途匹配。"),

    ("散热行不行", "ask_current", "noise", False,
     "问的是「这台的散热怎么样」，属于参数面（散热/噪音）。\n"
     "「行不行」不是问用途匹配，是问这一项表现如何。\n"
     "判据：参数面 + 评价性问法（怎么样/行不行/好不好）= ask_current。\n"
     "⚠️ 只有「能不能跑XX软件」那种才涉及用途匹配。"),

    ("屏幕怎么样", "ask_current", "screen", False,
     "「屏幕」是明确参数面，「怎么样」是问这一项表现。\n"
     "没有提任何用途，所以 fit=False。\n"
     "判据：问具体部件 + 评价性问法 = ask_current + 对应 topic。"),

    ("有雷电接口吗", "ask_current", "config", False,
     "问的是接口的有无（雷电），属于参数面。\n"
     "「有…吗」是疑问形态，不是提要求（「我要雷电接口」才是要求）。\n"
     "判据：问某接口在不在 = ask_current/config。"),

    # ═══ 用户明确说品类（2026-11-21 实测事故）═══
    ("轻薄本", "state_new_need", "none", False,
     "这是在**指定品类**，不是问当前这台。\n"
     "关键：用户明说品类时，优先级高于上一轮的用途推断 ——\n"
     "  上一轮说「打游戏」推成游戏本，这一轮说「轻薄本」就该切回轻薄本。\n"
     "判据：出现品类词（轻薄本/游戏本/台式）= 补充条件，覆盖旧品类。"),

    ("换轻薄本", "refuse_switch", "none", False,
     "「换」是明确的换台动作，「轻薄本」是新的品类要求。\n"
     "两个信息都要：既要换台（refuse_switch），也要改品类。\n"
     "判据：换 + 品类词 = refuse_switch（不是 state_new_need，因为有「换」）。"),

    # ═══ 限电约束（2026-11-18 复发过）═══
    ("宿舍限电400瓦", "state_new_need", "none", False,
     "这是在补充环境约束（宿舍供电上限 400W），不是问电池。\n"
     "陷阱：「限电」有「电」字，容易判成 topic=battery。\n"
     "但整句没有「电池多大」「能撑多久」的问法，给的是具体数值条件。\n"
     "判据：限电/供电 + 数字瓦 = 补充条件。"),

    ("限电500W能用吗", "state_new_need", "none", False,
     "带问号也还是补充条件 —— 用户是拿条件问可行性。\n"
     "关键：这不是问「电池怎么样」，是问「我这条件能不能用」。\n"
     "判据：条件 + 可行性问法 = state_new_need（要记槽位再判）。"),

    # ═══ 品牌名 vs 专业名（2026-11-18 事故）═══
    ("有没有机械革命的同配置的", "ask_current", "config", False,
     "「机械革命」是**笔记本品牌**，不是「机械设计」专业。\n"
     "这句在问替代品（同品牌同配置），属于 ask_current。\n"
     "判据：先看是不是品牌名 —— 品牌名里的字不参与专业匹配。"),

    ("我是软件工程专业的，平时也喜欢打游戏",
     "state_new_need", "none", True,
     "同时给了专业和用途，两个都是条件 → state_new_need。\n"
     "fit 给 True 是对的（有用途可判），但 **intent 不是 ask_current**。\n"
     "关键：fit=true 只表示「有用途可判」，不代表「在问当前这台」。\n"
     "⚠️ 这是「fit 污染 intent」的典型错误。"),

    # ═══ 用途陈述 ═══
    ("平时主要打游戏", "state_new_need", "none", True,
     "陈述用途（游戏），没有疑问词。\n"
     "说了用途就该拿它判当前这台够不够 → fit=True。\n"
     "⚠️ 不许因为少了「我想」就判成问句 —— 模型过度依赖显式标记。\n"
     "判据：用途陈述 = state_new_need + fit=True。"),

    ("主要用来剪视频和修图", "state_new_need", "none", True,
     "陈述用途（视频剪辑 + 修图），两个都是创作类。\n"
     "判据：用途陈述 = state_new_need，fit 给 True。"),

    # ═══ 换台 ═══
    ("再看看别的吧", "refuse_switch", "none", False,
     "「再看看别的」= 不要当前这台了，要换。\n"
     "与 list_others 的区别：后者是「把剩下的列出来」，语气中性；\n"
     "这句带「吧」，是对当前这台不满意。\n"
     "判据：出现「别的」+ 暗示当前不合适 = refuse_switch。"),

    ("把剩下的都列出来", "list_others", "none", False,
     "「都列出来」是明确要清单，不是换台。\n"
     "判据：列/都/全部 + 要求展示 = list_others。\n"
     "⚠️ 没有否定当前这台的意思。"),

    ("除了这台还有吗", "list_others", "none", False,
     "「除了」是排除法 —— 这台我留着，但想看看别的。\n"
     "与 refuse_switch 的区别：没有否定词（不要/不行/算了）。\n"
     "判据：问「除当前外还有什么」= list_others。"),

    # ═══ 预算表达 ═══
    ("预算 5000，写代码", "state_new_need", "none", True,
     "预算 + 用途，两个都是条件 → state_new_need。\n"
     "⚠️ 标点不改变语义：「预算5000写代码」和「预算5000，写代码」是同一件事。\n"
     "判据：有没有「推荐/有什么」这类索取动作 —— 有才是 state_range。"),

    ("六七千的轻薄本有什么推荐", "state_range", "none", False,
     "给的是价位段（六七千）+ 品类（轻薄本），没有指向某一台。\n"
     "有「有什么推荐」= 索取动作，所以是 state_range 而不是 state_new_need。\n"
     "判据：范围描述 + 索取动作 = state_range。"),

    # ═══ 无实义 ═══
    ("嗯", "unknown", "none", False,
     "单字语气词，没有需求也没有问题。\n"
     "判据：无实义 = unknown。"),

    ("谢谢", "unknown", "none", False,
     "表达感谢，对话可以结束。\n"
     "判据：社交收尾 = unknown，不是新需求。"),
]


def run_cases(verbose=False):
    """跑全部用例，返回 (通过数, 总数, 失败列表)。"""
    ok = 0
    fails = []
    for text, want_i, want_t, want_f, cot in CASES:
        il._CACHE.clear()
        r = il.route(text, focus="zol-2103842")
        got_i = r.get("intent")
        got_t = r.get("topic") or "none"
        got_f = bool(r.get("fit"))
        good = (got_i == want_i and got_t == want_t and got_f == want_f)
        ok += 1 if good else 0
        if not good:
            fails.append({
                "text": text,
                "wrong": {"intent": got_i, "topic": got_t, "fit": got_f},
                "correct": {"intent": want_i, "topic": want_t, "fit": want_f},
                "cot": cot,
            })
        if verbose:
            mark = "✓" if good else "✗"
            print("  %s %-24s 期望 %-14s/%-8s/%-5s 实际 %s/%s/%s"
                  % (mark, text[:22], want_i, want_t, want_f,
                     got_i, got_t, got_f))
    return ok, len(CASES), fails


def to_sample(f):
    """把失败转成训练样本（带思维链）。"""
    c = f["correct"]
    return {
        "messages": [
            {"role": "system",
             "content": '你是选机助手的路由器。只输出 JSON：'
                        '{"intent":...,"topic":...,"fit":...}'},
            {"role": "user", "content": f["text"]},
            {"role": "assistant",
             "content": f["cot"].strip() + "\n" + json.dumps(c, ensure_ascii=False)},
        ],
        "_meta": {
            "src": "adversarial-v2",
            "has_cot": True,
            "correct_before": False,
            "wrong": json.dumps(f["wrong"], ensure_ascii=False),
            "why": "整链对抗测试失败样本",
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--collect", action="store_true",
                    help="跑 + 把失败样本写进 adversarial-v2.jsonl")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    print("用例 %d 条（覆盖参数主题/品类切换/限电/品牌/用途/换台/预算/无实义）"
          % len(CASES))
    if args.dry or not (args.run or args.collect):
        print()
        print("（加 --run 跑一遍，加 --collect 收集失败样本）")
        return 0

    print()
    print("=== 跑用例 ===")
    ok, total, fails = run_cases(verbose=args.verbose)

    print()
    print("通过 %d / %d = %.1f%%" % (ok, total, 100.0 * ok / max(1, total)))

    if fails:
        print()
        print("=== 失败 %d 条 ===" % len(fails))
        for f in fails:
            print("  %-24s" % f["text"][:22])
            print("     期望 %s" % f["correct"])
            print("     实际 %s" % f["wrong"])

    if args.collect and fails:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with io.open(OUT, "w", encoding="utf-8", newline="\n") as fp:
            for f in fails:
                fp.write(json.dumps(to_sample(f), ensure_ascii=False) + "\n")
        print()
        print("已写出 %d 条训练样本 → %s" % (len(fails), OUT))
        print("（每条带纠错思维链，可直接进 build_route_lora.py）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
