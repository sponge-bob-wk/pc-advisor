# -*- coding: utf-8 -*-
"""
槽位抽取的真实弱点探测 —— 专攻「正则无解、模型也可能失手」的表达。

为什么只测这一类：
    diag_slot_gap.py 已证明常规表达 125 条里只失败 1 条（1%），
    再测常规样本没有信息量。真正值得投入的是那些「需要常识推理才能换算」的说法，
    这类正则永远覆盖不了，也是 LoRA 唯一可能真正加分的地方。

判据：只看模型原生输出（不经 normalize / guard_budget），
      因为后处理会把错误掩盖掉。
"""
import json
import agent

# (编号, 用户原话, 期望槽位 dict, 说明)
CASES = [
    # --- 群体一：金额需要推理换算 ---
    ("R01", "别超我半个月工资",                    {"budget": None},        "无从换算，应保守给 null"),
    ("R02", "预算就一个月工资吧",                  {"budget": None},        "同上"),
    ("R03", "一万出头能接受",                      {"budget": 10000},       "出头 ≈ 略高于一万"),
    ("R04", "万元以内，别太贵",                    {"budget": 10000},       "万元以内"),
    ("R05", "三四千的样子",                        {"budget": (3000, 4000)}, "区间内任一值都算对"),
    ("R06", "小一万",                              {"budget": 10000},       "小一万 ≈ 接近一万"),
    ("R07", "六千上下浮动五百",                    {"budget": (5500, 6500)}, "基准±浮动都算对"),
    ("R08", "预算够买台MacBook就行",                {"budget": None},        "参照物价，非数字"),
    ("R09", "比上代贵一点无所谓，八千以内",         {"budget": 8000},        "以明确数字为准"),
    ("R10", "加上国补一共八千",                    {"budget": 8000},        "含补贴的总价"),

    # --- 群体二：品类需要推理 ---
    ("R11", "就想买个能带出去写代码的",            {"cat": "thin"},         "写代码+带出门 → 轻薄本"),
    ("R12", "宿舍里打游戏用，不带走",              {"cat": None},           "未说品类 → 不该替用户定，null 才对"),
    ("R13", "主要剪 4K，要能扛",                    {"cat": None},           "未明说品类，不该乱猜"),
    ("R14", "给我升级下内存和固态",                {"cat": "upgrade"},      "升级件"),
    ("R15", "就是普通办公，看看表格",              {"cat": "thin"},         "办公 → 轻薄本"),

    # --- 群体三：否定与排除 ---
    ("R16", "什么牌子都行，就是不要联想",          {"cat": None},           "不该因排除品牌而猜品类"),
    ("R17", "别给我推游戏本，太重了",              {"cat": "thin"},         "排除游戏本 → 轻薄"),
    ("R18", "不要二手的",                          {},                       "二手非槽位，应忽略"),

    # --- 群体四：携带与年限 ---
    ("R19", "天天背着上下班",                      {"carry": True},         "背着 → carry"),
    ("R20", "放家里用，不移动",                    {"carry": False},        "不移动 → carry False"),
    ("R21", "想用到大四毕业",                      {"years": 4},            "隐含本科四年，模型推 4 是对的"),
    ("R22", "至少用五年，越长越好",                {"years": 5},            "至少五年"),
    ("R23", "用个三四年吧",                        {"years": 3},            "区间取下限或首值"),

    # --- 群体五：宿舍限电 ---
    ("R24", "宿舍限电 800 瓦",                     {"dormWatt": 800},       "明确瓦数"),
    ("R25", "宿舍功率有限，不能太大",              {"dormWatt": None},      "未给数字，应 null"),

    # --- 群体六：多意图混杂 ---
    ("R26", "五千左右，轻薄，能剪视频，用四年，不要荣耀", {},
     "六意图混杂，只看是否整体崩坏"),
    ("R27", "打游戏为主，偶尔带去图书馆，一万二以内，宿舍限电", {},
     "缺瓦数，不该编数字"),
]


def raw(s):
    out = agent.chat([{"role": "system", "content": agent.SLOT_SYS},
                      {"role": "user", "content": s}],
                     fmt="json", num_predict=160)
    try:
        return json.loads(out)
    except Exception:
        return {"__parse_error__": out[:100]}


def check(got, want):
    """只检查 want 里显式给出的字段。

    want 的值可以是单值，也可以是 (lo, hi) 区间元组 —— 表示「这一区间内任一值都算对」。
    为什么需要区间：中文说「三四千」时，取 3000、3500、4000 都是合理理解，
    强行要求某个具体值是拿判据凑结论（项目红线）。
    """
    bad = []
    if "__parse_error__" in got:
        return [("__all__", got["__parse_error__"], "可解析 JSON")]
    for k, v in want.items():
        gv = got.get(k)
        if isinstance(gv, str) and k == "purpose":
            gv = [gv]
        if gv in ([], ""):
            gv = None
        # 区间判据
        if isinstance(v, tuple) and len(v) == 2:
            lo, hi = v
            if not isinstance(gv, (int, float)) or not (lo <= gv <= hi):
                bad.append((k, gv, "在 %d~%d 之间" % (lo, hi)))
            continue
        if gv != v:
            bad.append((k, gv, v))
    return bad


def main():
    ok = fail = 0
    details = []
    for cid, text, want, note in CASES:
        g = raw(text)
        bad = check(g, want) if want else []
        if bad:
            fail += 1
            details.append((cid, text, note, bad, g))
        else:
            ok += 1
        print("%-4s %-34s %s" % (cid, text[:32], "PASS" if not bad else "FAIL"))
        if bad:
            for k, gv, wv in bad:
                print("        %-9s 模型给了 %r, 期望 %r" % (k, gv, wv))

    print()
    print("=" * 64)
    print("通过 %d / %d   (失败率 %.0f%%)" % (ok, len(CASES), 100.0 * fail / len(CASES)))
    print()
    if details:
        print("失败明细（这些才是 LoRA 可能有用的地方）：")
        for cid, text, note, bad, g in details:
            print("  %s  %s" % (cid, text))
            print("      注: %s" % note)
            print("      模型实际: %s" % json.dumps(g, ensure_ascii=False)[:150])
    print()
    print("判据：失败率 > 10% 且失败样本成簇 → 才值得考虑 LoRA")


if __name__ == "__main__":
    main()