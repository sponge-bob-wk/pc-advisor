# -*- coding: utf-8 -*-
"""放宽预算 —— 判据不许再漂（2026-11-13 用户报的真 bug 的回归）

事故原文（日志 chat-2026-11-13 第 71→72 条）：

    【71】stage=exhausted
      reply: 这个条件下库里就这几台，都看过了。要不放宽预算，或者换个品类我再看看？
    【72】用户：「可以，放宽预算推荐几台」
      slots.budget: 7000  ← 没动
      focus: thin-zhan66  ← 又是同一台

两个错：
  ① 「放宽预算」只是一句话，没有变成槽位的动作 → 池子一点没变
  ② 这句不算「换一台」，所以 exclude=None，看过的三台全部重新参选

最扎人的是：**这句建议是系统自己出的**，用户照着做了却原地打转。

本测试守四条：
  R1 「该认出的」必须认出
  R2 「不许误杀的」必须不认（关键是「预算够用」「够了」不能被当成放宽）
  R3 放宽后的预算数值符合约定（+20%、至少跨 1000、99999 不硬加）
  R4 端到端：旧预算 exhausted 后说放宽 → 预算变、换机器、seen 只增不减
"""

import sys
import os
import io
import re

# 🔴 2026-11-26 加：**强制 stdout/stderr 用 UTF-8**。
#    Windows 控制台默认 GBK，打印 ⚠️/✓/✗ 这类字符会 UnicodeEncodeError 直接崩。
#    实测：server.py 第 588 行的 ⚠️ 导致服务起不来。
#    不依赖外部 PYTHONIOENCODING 环境变量，在代码里写死。
#    errors="replace" 是双保险 —— 宁可显示成 ? 也不能让脚本崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent  # noqa: E402


# ── R1 「该认出的」 ─────────────────────────────────────────────
SHOULD_CATCH = [
    "可以，放宽预算推荐几台",
    "放宽预算",
    "那就放宽点预算吧",
    "预算可以提高一点",
    "预算再涨点",
    "预算加点",
    "加点预算",
    "多点预算",
    "再给点钱",
    "不差钱",
    "预算不是问题",
    "预算不限",
    "可以加点",
    "可以多点",
    "往上加",
    "往上走",
    "提高预算",
    "提一下预算",
    "预算多点",
    "预算高点",
    "预算抬一抬",
]

# ── R2 「不许误杀的」 ───────────────────────────────────────────
# 这些都不是"要求放宽"，误判会把用户原本的预算偷偷改掉。
SHOULD_NOT_CATCH = [
    "预算够了",                    # 够了 ≠ 要加
    "7000 够了吧",
    "预算 7000",
    "这台多少钱",
    "7000 以内有什么",
    "能不能便宜点",                # 要降不是要加
    "太贵了",                      # 要换台，不是加钱
    "换一台",
    "还有别的吗",
    "这台屏幕怎么样",
    "内存能加吗",                  # 加的是内存，不是预算
    "内存可以加装吗",
    "加个硬盘",
    "处理器性能怎么样",
    "",
    None,
]


def run_r1_r2():
    bad = []
    for t in SHOULD_CATCH:
        if not agent.is_relax_budget(t):
            bad.append(("漏认", t))
    for t in SHOULD_NOT_CATCH:
        if agent.is_relax_budget(t):
            bad.append(("误认", t))
    total = len(SHOULD_CATCH) + len(SHOULD_NOT_CATCH)
    return bad, total


# ── R3 数值约定 ────────────────────────────────────────────────
def run_r3():
    cases = [
        (7000, 8000),    # 7000×1.2=8400 → 取整到 8000（1000 档）
        (5000, 6000),    # 5000×1.2=6000
        (4000, 5000),    # 4800 → 5000
        (3999, 5000),    # 4798.8 → 5000
        (12000, 14000),  # 14400 → 14000
        (9500, 11000),   # 11400 → 11000
        (100, 1000),     # 极小值也要真的变
        (0, 0),          # 没预算 → 不动
        (None, None),
        (99999, 99999),  # 「没说预算」→ 不硬加
    ]
    bad = []
    for src, want in cases:
        got = agent.relaxed_budget(src)
        if got != want:
            bad.append((src, want, got))
    # 单调性：放宽后必须严格大于原值（除了 0/None/99999 这几个哨兵）
    for b in range(1000, 20000, 500):
        if agent.relaxed_budget(b) <= b:
            bad.append(("未增大", b, agent.relaxed_budget(b)))
    return bad, len(cases) + len(range(1000, 20000, 500))


# ── R4 端到端 ──────────────────────────────────────────────────
INCIDENT_KNOWN = {
    "cat": "thin", "budget": 7000, "purpose": ["dev"], "years": 4, "carry": True,
    "_minUpgrade": 2, "_minRam": 16, "_majorId": "code",
}
INCIDENT_SEEN = [
    "thin-zhan66",
    "spec-parsed-jd-asus-wuwei16-2026-r7h260-16g-512g-202",
    "spec-parsed-jd-lenovo-xiaoxinpro16-r7h255-24g-1t-yih",
]


def run_r4():
    bad = []
    out = agent.run("可以，放宽预算推荐几台",
                    known=dict(INCIDENT_KNOWN),
                    seen=list(INCIDENT_SEEN),
                    last_stage="exhausted")

    # ① 预算必须真的变
    nb = (out.get("slots") or {}).get("budget")
    if nb == 7000:
        bad.append("预算没变（还是 7000）")
    elif nb != 8000:
        bad.append("预算变成了 %r，期望 8000" % (nb,))

    # ② 不许再把已经看过的那台端上来（这是用户实际看到的症状）
    if out.get("focus") in INCIDENT_SEEN:
        bad.append("推的还是看过的那台：%s" % out.get("focus"))

    # ③ seen 只增不减 —— 清零会让看过的机器重新参选（我中途踩过的错）
    seen_out = out.get("seen") or []
    for sid in INCIDENT_SEEN:
        if sid not in seen_out:
            bad.append("seen 丢了一条：%s" % sid)
    if len(seen_out) < len(INCIDENT_SEEN):
        bad.append("seen 变短了：%d → %d" % (len(INCIDENT_SEEN), len(seen_out)))

    # ④ 放宽后得真的推出东西，别改成"什么都没了"
    if not out.get("focus"):
        bad.append("放宽后没推出任何机器（stage=%r）" % (out.get("stage"),))

    # ⑤ 放宽后再放宽一次，还得继续能走（不能只在第一次有效）
    out2 = agent.run("再放宽点", known=dict(out.get("slots") or {}),
                     seen=list(seen_out), last_stage="recommend")
    nb2 = (out2.get("slots") or {}).get("budget")
    if nb2 == nb:
        bad.append("第二次放宽预算没动：%r" % (nb2,))

    return bad, 6


# ── R4b 用户给的数字优先于"放宽"这个动作 ────────────────────────
# test_multiturn M03 抓到的回归：改放宽分支时，把「预算提高到7000」
# 当成"放宽"，用 5000×1.2=8000 覆盖了用户亲口说的 7000。
# 用户给的数字被系统改掉，比不推荐更严重 —— 这条必须单独立闸。
def run_r4b():
    bad = []
    cases = [
        # (上一轮槽位, 这一句, 期望的预算)
        ({"cat": "design", "budget": 5000, "purpose": ["design"]},
         "预算提高到7000，屏幕素质我有要求", 7000),
        ({"cat": "thin", "budget": 5000, "purpose": ["office"]},
         "预算加到8000", 8000),
        ({"cat": "thin", "budget": 6000, "purpose": ["office"]},
         "预算涨到9000吧，可以放宽", 9000),
        ({"cat": "thin", "budget": 6000, "purpose": ["office"]},
         "放宽预算到 7500", 7500),
    ]
    for known, text, want in cases:
        out = agent.run(text, known=dict(known), seen=[], last_stage="recommend")
        got = (out.get("slots") or {}).get("budget")
        if got != want:
            bad.append("%r → budget=%r，期望 %r（用户给的数字被改掉了）"
                       % (text, got, want))
    return bad, len(cases)


# ── R6 协作层：规则确定时不许被模型改掉 ─────────────────────────
# 2026-11-13 晚加。设计要点是"规则先说话"：
#   · 规则抽到金额 → set_budget，模型没机会推翻（数字可追溯，是硬事实）
#   · 规则命中放宽词 → relax，模型也没机会推翻
#   · 规则不认识 → 才问模型
# 这里测的就是这条"不许推翻"—— 它是整个协作设计的安全边界。
def run_r6():
    bad = []
    try:
        import budget_intent as BI
    except Exception as e:
        return ["budget_intent 导入失败：%s" % e], 1

    # 6a 规则确定的，必须由 rule 给出（不许 model 介入）
    rule_decided = [
        # (句子, 期望标签)
        ("放宽预算到 7500", "set_budget"),      # 有金额
        ("预算提高到7000，屏幕素质我有要求", "set_budget"),
        ("再加两千吧", "set_budget"),
        ("我妈最多给一万", "set_budget"),
        ("可以，放宽预算推荐几台", "relax"),      # 有放宽词无金额
        ("预算不是问题", "relax"),
        ("不用给我省", "relax"),                 # 双重否定，不能被拒绝词误伤
        ("预算够了", "none"),                    # 拒绝加钱
        ("不用加", "none"),
        ("能不能便宜点", "none"),                # 反向：要降价
        ("太贵了", "none"),
    ]
    for text, want in rule_decided:
        lab, why = BI.classify_budget(text)
        if lab != want:
            bad.append("%r → %s，期望 %s（%s）" % (text, lab, want, why))
            continue
        if not why.startswith("rule:"):
            bad.append("%r 被模型接管了（%s）—— 规则确定的句子不许问模型"
                       % (text, why))

    # 6b 模型不许凭空说"用户给了数字"—— 原话抽不出金额就必须降级
    lab, why = BI.classify_budget("我爸妈说能多给些")
    if lab == "set_budget":
        bad.append("「我爸妈说能多给些」被判成 set_budget —— 原话没有金额，"
                   "这会让下游猜一个数（%s）" % why)

    # 6c 数值算数必须来自 agent，不许在 budget_intent 里重写一遍
    if BI.relax_step(7000) != agent.relaxed_budget(7000):
        bad.append("relax_step 与 agent.relaxed_budget 不一致 —— 判据抄了第二遍")

    # 6d 模型不可用时必须降级而不是报错（宁可弃权）
    try:
        lab, why = BI.classify_budget("手头松了点", allow_model=False)
        if lab not in ("none", "relax", "set_budget"):
            bad.append("禁用模型后返回了非法值 %r" % (lab,))
    except Exception as e:
        bad.append("禁用模型时抛异常：%r —— 协作层必须能降级" % (e,))

    return bad, len(rule_decided) + 3


# ── R5 源码闸：放宽判据只能有一处 ───────────────────────────────
def _code_lines(src):
    """去掉注释和文档字符串，只留真实代码行。

    为什么要剥：源码闸要数"逻辑写了几遍"，而我上一版把
    `RELAX_STEP_RATIO` 也算进注释里了 —— 注释里提到一次就误报一次。
    统计类判据必须先排除注释，否则它检的是"我写了多少字"。
    """
    out = []
    in_triple = False
    for line in src.splitlines():
        s = line.strip()
        if in_triple:
            if '"""' in s or "'''" in s:
                in_triple = False
            continue
        if s.startswith('#'):
            continue
        if ('"""' in s or "'''" in s) and not s.startswith(("r'", 'r"')):
            # 单行 docstring 或 docstring 开头
            q = '"""' if '"""' in s else "'''"
            if s.count(q) == 1:
                in_triple = True
            continue
        if "#" in s:
            s = s.split("#", 1)[0].rstrip()
        if s:
            out.append(s)
    return "\n".join(out)


def run_r5():
    """判据漂移是本项目复现过 10 次的病。这里钉住："""
    bad = []
    here = os.path.dirname(os.path.abspath(__file__))
    raw = io.open(os.path.join(here, "agent.py"), encoding="utf-8").read()
    src = _code_lines(raw)

    # 放宽的判定只许在 is_relax_budget 里做
    n = len(re.findall(r"RELAX_BUDGET\.(search|match|findall)", src))
    if n != 1:
        bad.append("RELAX_BUDGET 被用了 %d 次 —— 判据又分岔了" % n)

    # 放宽的算数只许在 relaxed_budget 里做。
    # 数"用了"不数"定义"：`RELAX_STEP_RATIO = 0.20` 那行是定义，
    # 一个常量本来就该有一次定义 + 一次使用，拿总数当判据会永远报错。
    n = len(re.findall(r"RELAX_STEP_RATIO(?!\s*=)", src))
    if n != 1:
        bad.append("RELAX_STEP_RATIO 被引用 %d 次，算数逻辑重复了" % n)

    # 只在 6d2 一处改 budget —— 别处再写一遍就是第二份判据
    n = len(re.findall(r'slots\["budget"\]\s*=', src))
    if n != 1:
        bad.append('slots["budget"] 被赋值 %d 次，应该只有 6d2 一处' % n)

    return bad, 3


def main():
    groups = [
        ("R1+R2 认出 / 不许误杀", run_r1_r2),
        ("R3 数值约定", run_r3),
        ("R4 端到端（事故原样复现）", run_r4),
        ("R4b 明确金额优先于放宽", run_r4b),
        ("R6 协作层：规则确定时不许被改", run_r6),
        ("R5 源码闸（判据只一处）", run_r5),
    ]
    total_bad = 0
    for name, fn in groups:
        bad, n = fn()
        total_bad += len(bad)
        mark = "PASS" if not bad else "FAIL"
        print("[%s] %-28s %d 项" % (mark, name, n))
        for b in bad:
            print("       ✗", b)

    print("-" * 52)
    if total_bad:
        print("失败 %d 项" % total_bad)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())