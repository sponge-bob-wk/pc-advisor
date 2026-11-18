# -*- coding: utf-8 -*-
"""test_chain.py —— 整条链的对抗测试

## 为什么要有这个（用户原话）

> 「然后这时候的对抗测试才是有意义的，而且是一条链条的训练」

现有测试的问题：**各测一段，不测衔接**。

  test_intent   只测意图分类对不对
  test_neg      只测判据会不会误杀
  test_named    只测点名机型的回答

但真实事故都发生在**衔接处**：

  意图判对了 → 但槽位没落进去 → 推荐错了（车辆工程那次）
  槽位对了   → 但筛选漏了约束   → 推荐核显机（车辆工程那次）
  筛选对了   → 但审计误杀       → 回答退化成模板（很死板那次）
  审计放行了 → 但模型编造       → 品牌串台（机械革命那次）

## 链条的六个环节

```
用户原话
  ↓ ① 意图     intent 判对了吗（模型）
  ↓ ② 槽位     cat/budget/purpose/专业 抽对了吗（模型+规则）
  ↓ ③ 约束     专业要求落成硬约束了吗（规则）
  ↓ ④ 筛选     选出的机器满足约束吗（规则）
  ↓ ⑤ 审计     回答没被误杀吗（规则）
  ↓ ⑥ 表达     回答包含该说的信息吗（模型）
```

**任何一环断了，整条链就是错的。** 所以每条用例要断言全部六环。

## 用法

    python test_chain.py              # 跑全部
    python test_chain.py --verbose    # 打印每环的中间值
    python test_chain.py --only 车辆   # 只跑名字含"车辆"的
"""
import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent
import core

# ── 用例定义 ─────────────────────────────────────────────────────────
# 每条 = {
#   name      : 用例名
#   turns     : [(用户原话, 断言字典), ...]   多轮，按顺序跑
# }
#
# 断言字典可以含：
#   intent    : 期望的意图
#   cat       : 期望的品类
#   min_ram   : 期望的 _minRam
#   want_dgpu : 期望的 _wantDgpu
#   min_vram  : 期望的 _minVram
#   focus_has : 推荐机名必须包含的字
#   focus_not : 推荐机名不能包含的字
#   dgpu      : 推荐机必须有独显（True/False）
#   reply_has : 回答必须包含的字符串（list）
#   reply_not : 回答不能包含的字符串（list）
#   min_len   : 回答最短字数
CASES = [
    {
        "name": "车辆工程→要独显（2026-11-18 实测事故）",
        "turns": [
            ("车辆工程专业", {
                # ⚠️ 这里**不该断言 cat**：用户只说了专业，没说笔记本还是台式，
                #    系统问"是笔记本还是台式"是正确行为（2026-11-18 修测试时发现
                #    第一版断言 cat=thin 是错的，那不是产品 bug 是测试写错）。
                #    该断言的是：专业要求必须落成硬约束。
                "min_ram": 32,           # 专业要求
                "want_dgpu": True,       # 专业要求 ← 这次漏的就是它
                "min_vram": 4,
            }),
            ("笔记本", {"cat": "thin"}),
            ("预算7500专业", {
                "cat": "thin",
                "want_dgpu": True,
                # 关键：7500 里没有「独显+32G」的轻薄本
                # 所以要么 focus 是独显机，要么明说没有
                "reply_not": ["核显机型", "没有独立显卡"],
            }),
        ],
    },
    {
        "name": "软件工程→问机械革命（2026-11-18 专业被覆盖）",
        "turns": [
            ("我是软件工程专业的，平时也喜欢打游戏", {
                "cat": "gaming",         # 打游戏 → 游戏本
                "min_ram": 16,           # 软件工程要求
                "want_dgpu": False,      # 软件工程不要求独显
            }),
            ("预算 8000 左右", {
                "cat": "gaming",
                "min_ram": 16,
            }),
            ("有没有机械革命的同配置的", {
                # 🔴 关键断言：专业不许被「机械革命」污染
                "min_ram": 16,           # 必须还是 16，不能变 32
                "reply_not": ["机械设计", "三维建筑"],
            }),
        ],
    },
    {
        "name": "限电约束（2026-11-17 事故）",
        "turns": [
            ("预算6000轻薄本上课用", {}),
            ("宿舍限电400瓦", {
                # 必须提到限电，且不许编整机功耗
                "reply_has": ["限电"],
                "reply_not": ["功耗设计通常远超", "大概率跑不满"],
            }),
        ],
    },
    {
        "name": "换台不重复（2026-11-17 死循环）",
        "turns": [
            ("预算7000打游戏", {}),
            ("换一台", {
                # 第二次的机器必须和第一次不同
                # 由 runner 特殊处理（见 _check_switch）
            }),
        ],
    },
    {
        "name": "诚实声明不被误杀（2026-11-18 回答太短）",
        "turns": [
            ("预算8000打游戏", {
                # 🔴 关键：回答不许退化成干巴配置罗列
                "min_len": 150,
                # 诚实的部分要留住
                "reply_not": ["抱歉", "无法回答"],
            }),
        ],
    },
    {
        "name": "品牌归属不许串（2026-11-18 机械革命暗影精灵）",
        "turns": [
            ("预算7000打游戏", {}),
            ("有没有机械革命的", {
                # 回答里如果把当前这台说成机械革命 → 错
                # 但"库里没有机械革命的机型"是诚实的 → 放行
                "reply_not": ["属于机械革命", "机械革命暗影精灵"],
            }),
        ],
    },
]


def _check_turn(prev_known, text, asserts, verbose=False, seen=None):
    """跑一轮，返回 (ok, 失败原因列表, known, focus, seen)。

    🔴 2026-11-18：**必须传 seen**。
       第一版没传，导致「换一台」测试假失败 ——
       `run()` 靠 seen 才能把看过的机器排除掉，
       不传 seen 它就不知道看过啥，当然推回同一台。
       这是测试自己的 bug，不是产品的。
    """
    fails = []
    known = dict(prev_known or {})
    r = agent.run(text, known=known, seen=seen or [])
    slots = r.get("slots") or {}
    focus = r.get("focus") or ""
    reply = r.get("reply") or ""
    stage = r.get("stage")
    new_seen = list(r.get("seen") or [])

    def _fail(env, want, got):
        fails.append("%s: 期望 %r，实际 %r" % (env, want, got))

    # ① 意图
    if "intent" in asserts:
        got = (r.get("route") or {}).get("intent") if r.get("route") else None
        if got != asserts["intent"]:
            _fail("意图", asserts["intent"], got)

    # ② 槽位
    if "cat" in asserts and slots.get("cat") != asserts["cat"]:
        _fail("品类", asserts["cat"], slots.get("cat"))
    if "min_ram" in asserts:
        got = slots.get("_minRam")
        if got != asserts["min_ram"]:
            _fail("内存约束", asserts["min_ram"], got)
    if "want_dgpu" in asserts:
        got = bool(slots.get("_wantDgpu"))
        if got != asserts["want_dgpu"]:
            _fail("独显约束", asserts["want_dgpu"], got)
    if "min_vram" in asserts:
        got = slots.get("_minVram")
        if got != asserts["min_vram"]:
            _fail("显存约束", asserts["min_vram"], got)

    # ③ 推荐机器的显卡（只在有 focus 时查）
    if "dgpu" in asserts and focus:
        o = core.get_offer(focus)
        if o:
            got = core.has_dgpu(o)
            if got != asserts["dgpu"]:
                _fail("推荐机独显", asserts["dgpu"], got)

    # ④ 回答内容
    for s in asserts.get("reply_has") or []:
        if s not in reply:
            _fail("回答应含", s, reply[:60])
    for s in asserts.get("reply_not") or []:
        if s in reply:
            _fail("回答不该含", s, reply[:60])
    if "min_len" in asserts and len(reply) < asserts["min_len"]:
        _fail("回答长度", ">=%d" % asserts["min_len"], len(reply))

    if verbose:
        print("      意图=%s 品类=%s 内存=%s 独显=%s 显存=%s stage=%s 长度=%d"
              % ((r.get("route") or {}).get("intent"), slots.get("cat"),
                 slots.get("_minRam"), slots.get("_wantDgpu"),
                 slots.get("_minVram"), stage, len(reply)))

    # 累积 known
    for k in ("cat", "budget", "purpose", "_minRam", "_minUpgrade",
              "_wantDgpu", "_minVram", "_majorId", "_majorName"):
        if slots.get(k) is not None:
            known[k] = slots[k]
    return (not fails), fails, known, focus, new_seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    total = passed = 0
    for case in CASES:
        if args.only and args.only not in case["name"]:
            continue
        print()
        print("【%s】" % case["name"])
        known = {}
        seen = []
        focuses = []
        case_ok = True
        for text, asserts in case["turns"]:
            total += 1
            ok, fails, known, focus, seen = _check_turn(
                known, text, asserts, verbose=args.verbose, seen=seen)
            if ok:
                passed += 1
                print("  ✓ %s" % text)
            else:
                case_ok = False
                print("  ✗ %s" % text)
                for f in fails:
                    print("      %s" % f)
            if focus:
                focuses.append(focus)

        # 换台不重复的特殊断言
        if case["name"].startswith("换台") and len(focuses) >= 2:
            if focuses[0] == focuses[1]:
                case_ok = False
                print("  ✗ 换台后还是同一台：%s" % focuses[0])
            else:
                print("  ✓ 换台生效：%s → %s"
                      % (focuses[0][:20], focuses[1][:20]))

    print()
    print("=" * 60)
    print("通过 %d / %d" % (passed, total))
    if passed == total:
        print("全绿。")
    else:
        print("有失败项 —— 链条上某一环断了，见上面明细。")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
