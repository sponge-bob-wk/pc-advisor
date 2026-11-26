# -*- coding: utf-8 -*-
"""追问路由的现状体检（2026-11-13 深夜，换血第二轮开工前）

## 为什么要单独体检这一块

第一轮换血接的是"换台 / 列清单 / 客套 / 交权"四个判据，都在 `on_current` 之前。
`on_current` **之后**还有一大块纯规则：
    ask_detail  ← FOLLOWUP_KW（30 词）
    ask_fit     ← FIT_KW（28 词）
    ask_price   ← PRICE_Q_KW（22 词）
这三个词表决定"这句是问细节 / 问匹配 / 问价格"，
是 `_run` 里剩下最集中的规则区（FIT_KW 10 处、FOLLOWUP_KW 9 处引用）。

## 这一块判错的代价（比换台更伤）

换台判错 → 多换一台，用户再说一次就行。
追问判错 → **把"问优点"答成"讲缺点"，或把"问细节"答成"重新推荐一遍"**，
用户会觉得"我问它话，它不听"。这是 2026-11-12 用户原话批的那类：
「按你的思路还是去列举所有可能的说法…没有真正的扛得住我乱测的方法吗」

## 三类标签的边界（写死，后面回归照这个判）

  detail  问这台的某个具体面（屏幕/保修/散热/重量/多重）
          → stage ∈ {followup}，且回复里必须**没有**新机型名
  fit     问这台配不配我的用途（够用吗/能跑 PS 吗/合适吗）
          → stage ∈ {followup, fit}
  price   问这台的价格值不值（划算吗/什么水平/贵不贵）
          → stage ∈ {compare, followup}
  switch  其实是不要这台了 → stage ∈ {recommend, exhausted}
          （混进来是因为很多"换个好点的屏幕"长着追问的脸）

用法：python eval_followup.py [-v]
"""
import io
import sys

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


sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
sys.path.insert(0, HERE)

import agent  # noqa: E402

FOCUS = "thin-wujie14x"
BASE = {"cat": "thin", "budget": 5000, "purpose": ["office"]}
SEEN = ["thin-zhan66"]

OK_STAGE = {
    "detail": {"followup"},
    "fit": {"followup", "fit"},
    "price": {"compare", "followup"},
    "switch": {"recommend", "exhausted"},
}

CASES = [
    # ── detail：问这台的某一面 ──
    ("这机器多重", "detail", "明说重量"),
    ("多重啊", "detail", "省略问法"),
    ("沉的慌不", "detail", "口语，问重量"),
    ("保修几年", "detail", "问保修"),
    ("售后怎么样", "detail", "问售后"),
    ("散热行不行", "detail", "问散热"),
    ("键盘手感如何", "detail", "问键盘"),
    ("接口够用吗", "detail", "问接口（够用会撞 FIT_KW，但主题是接口）"),
    ("屏幕素质怎么样", "detail", "问屏幕"),
    ("有没有独显", "detail", "问配置项，不是要一台带独显的"),
    ("内存能加吗", "detail", "问可升级性"),
    ("声音大不大", "detail", "问噪音"),
    ("做工怎么样", "detail", "问做工"),
    ("这台有什么毛病", "detail", "问缺点"),
    ("它保值吗", "detail", "问保值率"),

    # ── fit：问配不配我的用途 ──
    ("能跑 PS 吗", "fit", "问能不能干某件事"),
    ("打游戏够用吗", "fit", "用途 + 够用"),
    ("剪视频行不行", "fit", "用途 + 行不行"),
    ("学设计够用不", "fit", "用途 + 够用"),
    ("能带进教室吗", "fit", "移动性匹配"),
    ("写代码够吗", "fit", "用途 + 够"),
    ("跑得动 3A 吗", "fit", "用途 + 跑得动"),
    ("这台适合我吗", "fit", "问匹配"),
    ("能撑四年不", "fit", "问寿命"),
    ("带出去累不累", "fit", "问便携感受"),

    # ── price：问这台的价格 ──
    ("这个价怎么样", "price", "问价格位置"),
    ("划算吗", "price", "问值不值"),
    ("值不值这个钱", "price", "问值不值"),
    ("在市场上算什么水平", "price", "问价格位置"),
    ("是不是买贵了", "price", "求证价格判断，不是抱怨"),
    ("这价能买到更好的吗", "price", "问性价比"),
    # ⚠️「还有更便宜的吗」原来标 price，实际走了 recommend（换了一台更便宜的）。
    #    改标注不改代码：用户问"还有更便宜的吗"，拿到一台更便宜的 —— 这正是他要的。
    #    标 price 是我把"问价格"和"要更便宜"搞混了，后者是换台动作。
    ("还有更便宜的吗", "switch", "要更便宜的机器（换台，不是评价格）"),

    # ── switch：长着追问的脸，其实是不要了 ──
    ("屏幕太差了，换个好点的", "switch", "否掉 + 要换"),
    ("这个重量受不了", "switch", "否掉（没明说换）"),
    ("太贵了买不起", "switch", "价格否掉"),
    ("能不能便宜点", "switch", "要降价 ≈ 要换"),
    ("这台不行", "switch", "明确否掉"),
]


def judge(label, res):
    st = res.get("stage")
    reply = res.get("reply") or ""
    return st in OK_STAGE.get(label, set()), st, reply


def main():
    verbose = "-v" in sys.argv
    ok_n = 0
    fails = []
    per = {}
    for t, lab, why in CASES:
        res = agent.run(t, known=dict(BASE), focus=FOCUS, seen=list(SEEN))
        ok, st, reply = judge(lab, res)
        ok_n += ok
        d = per.setdefault(lab, [0, 0])
        d[0] += ok
        d[1] += 1
        if not ok:
            fails.append((t, lab, st, reply))
        if verbose:
            print("  %s %-22s %-7s → %-10s %s"
                  % ("OK " if ok else "XX ", t, lab, st, reply[:56]))

    n = len(CASES)
    print("=" * 78)
    print("追问路由正确率 %d/%d = %.1f%%" % (ok_n, n, 100.0 * ok_n / n))
    print()
    for lab, (a, b) in sorted(per.items(), key=lambda x: -x[1][1]):
        print("  %-8s %2d/%2d = %3.0f%%" % (lab, a, b, 100.0 * a / b))
    if fails:
        print()
        print("失败明细（%d 条）：" % len(fails))
        for t, lab, st, reply in fails:
            print("  x %-22s 期望=%-7s 实际=%-10s | %s"
                  % (t, lab, st, reply.replace("\n", " ")[:56]))
    print("=" * 78)


if __name__ == "__main__":
    main()