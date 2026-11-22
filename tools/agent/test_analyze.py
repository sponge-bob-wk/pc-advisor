# -*- coding: utf-8 -*-
"""test_analyze.py —— 数据分析能力测试

## 为什么单独一个测试文件

`adversarial_v2.py` 测的是**意图分类**（模型判断对不对）。
这个文件测的是**计算结果**（代码算得对不对）。

两者分开的理由：
  · 意图测错 → 走了错误路径，但计算引擎本身没问题
  · 计算错 → 数字就是错的，比意图错更严重（用户会当真）

## 测试分三层

  ① 引擎层（不调模型）：给定结构化查询，算出的数字必须和手工核对一致
  ② 解析层（调模型）：给定自然语言，解析出的查询结构必须正确
  ③ 端到端：完整问句 → 数字正确 + 回答里没有编造的数

## 基准值怎么来的

**全部手工核对过**，不是从引擎输出抄的：
  · 独显 129 台 = pool_offers() 里 gpuKind=='discrete' 的数量
  · 游戏本 ≤8000 平均 2.49kg = 21 台里有重量的 18 台求均值
  · 价格 1999~84999 = min/max(pool price)

## 用法

    python test_analyze.py            # 跑全部（含模型，慢）
    python test_analyze.py --engine   # 只跑引擎层（快，不调模型）
"""
import argparse
import io
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = r"E:\电脑涨价分析\tools\agent"
sys.path.insert(0, HERE)
import analyze
import core

PASS = FAIL = 0
FAILS = []


def ck(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK ] %s" % name)
    else:
        FAIL += 1
        FAILS.append(name)
        print("  [FAIL] %s   %s" % (name, detail))


def test_engine():
    """① 引擎层：不调模型，纯计算正确性。"""
    print("\n[1] 引擎层：计数")
    offs = core.pool_offers()

    # 手工核对基准
    real_dgpu = sum(1 for o in offs if core.has_dgpu(o))
    real_igpu = sum(1 for o in offs if o.get("gpuKind") == "integrated")

    r = analyze.analyze({"agg": "count", "filter": {"gpuKind": "独显"}})
    ck("独显台数 = %d" % real_dgpu, r["ok"] and r["value"] == real_dgpu,
       "引擎 %s / 手工 %d" % (r.get("value"), real_dgpu))

    r = analyze.analyze({"agg": "count", "filter": {"gpuKind": "核显"}})
    ck("核显台数 = %d" % real_igpu, r["ok"] and r["value"] == real_igpu,
       "引擎 %s / 手工 %d" % (r.get("value"), real_igpu))

    r = analyze.analyze({"agg": "count"})
    ck("总台数 = %d" % len(offs), r["ok"] and r["value"] == len(offs),
       "引擎 %s" % r.get("value"))

    print("\n[2] 引擎层：统计量")
    # 游戏本 ≤8000 的平均重量
    pool = [o for o in offs
            if (o.get("price") or 0) <= 8000 and o.get("cat") == "gaming"]
    ws = [o.get("weightKg") for o in pool if o.get("weightKg")]
    real_mean = round(sum(ws) / len(ws), 2)
    r = analyze.analyze({"agg": "mean", "field": "w",
                         "filter": {"price": {"max": 8000}, "cat": "gaming"}})
    ck("游戏本≤8000 平均重量 = %s（%d 台）" % (real_mean, len(ws)),
       r["ok"] and abs(r["value"] - real_mean) < 0.02 and r["n"] == len(ws),
       "引擎 %s（%d 台）" % (r.get("value"), r.get("n")))

    # 价格极值
    prices = [o["price"] for o in offs if o.get("price")]
    r = analyze.analyze({"agg": "min", "field": "price"})
    ck("最低价 = %d" % min(prices), r["ok"] and r["value"] == min(prices),
       "引擎 %s" % r.get("value"))
    r = analyze.analyze({"agg": "max", "field": "price"})
    ck("最高价 = %d" % max(prices), r["ok"] and r["value"] == max(prices),
       "引擎 %s" % r.get("value"))

    # 中位数
    import statistics
    real_med = round(statistics.median(prices), 2)
    r = analyze.analyze({"agg": "median", "field": "price"})
    ck("价格中位数 = %s" % real_med, r["ok"] and abs(r["value"] - real_med) < 1,
       "引擎 %s" % r.get("value"))

    print("\n[3] 引擎层：分组")
    r = analyze.analyze({"agg": "groupby", "group_by": "brand"})
    # ⚠️ 不写死组数 —— 补数据/加机型都会变。
    #    只断言「组数 >= 10」和「各组之和 == 总数」，这两个才是真约束。
    ck("品牌分组数 >= 10", r["ok"] and len(r["groups"]) >= 10,
       "引擎 %d 组" % len(r["groups"]))
    # 各组台数之和必须等于总数
    total = sum(v["n"] for v in r["groups"].values())
    ck("分组台数之和 = %d（不漏不重）" % len(offs), total == len(offs),
       "引擎 %d" % total)
    # 联想是最大组
    top_brand = max(r["groups"].items(), key=lambda x: x[1]["n"])
    ck("最大组是联想（%d 台）" % top_brand[1]["n"], top_brand[0] == "联想",
       "引擎 %s" % top_brand[0])

    print("\n[4] 引擎层：分布")
    r = analyze.analyze({"agg": "distribution", "field": "price",
                         "filter": {"ram": {"min": 32}}})
    ck("32G 内存价格分布有效", r["ok"] and isinstance(r.get("value"), dict)
       and len(r["value"]) > 0, str(r.get("value"))[:50])
    if isinstance(r.get("value"), dict):
        s = sum(r["value"].values())
        ck("分布各档之和 = 样本数", s == r["n"], "和 %d / n %d" % (s, r["n"]))

    print("\n[5] 引擎层：排行")
    r = analyze.analyze({"agg": "top", "field": "perf", "limit": 5})
    ck("性能排行返回 5 条", r["ok"] and len(r["rows"]) == 5,
       "引擎 %d 条" % len(r.get("rows") or []))
    if r.get("rows"):
        vals = [x["value"] for x in r["rows"]]
        ck("排行是降序", vals == sorted(vals, reverse=True), str(vals))
        # 第一名必须等于全库最高分
        real_max = max(o["perf"] for o in offs if o.get("perf"))
        ck("第一名 = 全库最高分 %s" % real_max, vals[0] == real_max,
           "引擎 %s" % vals[0])

    print("\n[6] 引擎层：边界")
    r = analyze.analyze({"agg": "count", "filter": {"price": {"max": 100}}})
    ck("筛空时 ok=False 且有提示", not r["ok"] and "一台都没有" in r["text"],
       r.get("text", "")[:40])
    r = analyze.analyze({"agg": "mean", "field": "batteryWh",
                         "filter": {"price": {"max": 2000}}})
    ck("无数据字段时明说算不了", (not r["ok"]) or r["n"] > 0,
       r.get("text", "")[:40])


def test_parse():
    """② 解析层：模型把自然语言翻译成结构化查询。"""
    print("\n[7] 解析层：模型翻译（调模型，慢）")
    CASES = [
        ("有多少台有独显",
         lambda d: d.get("agg") == "count"
         and "gpuKind" in (d.get("filter") or {})),
        ("8000以内游戏本平均多重",
         lambda d: d.get("agg") == "mean" and d.get("field") == "w"
         and (d.get("filter") or {}).get("cat") == "gaming"),
        ("各品牌机型数量对比",
         lambda d: d.get("agg") == "groupby"
         and d.get("group_by") == "brand"),
        ("性能最强的五台",
         lambda d: d.get("agg") == "top" and d.get("field") == "perf"),
    ]
    for text, check in CASES:
        d = analyze.parse_query(text)
        ok = bool(d) and check(d)
        ck("解析「%s」" % text, ok, str(d)[:70])


def test_e2e():
    """③ 端到端：完整问句 → 数字正确。"""
    print("\n[8] 端到端：完整问句（调模型 + 计算）")
    import agent
    CASES = [
        ("有多少台有独显", "129"),
        ("8000以内的游戏本平均多重", "2.49"),
    ]
    for text, expect in CASES:
        r = agent.run(text)
        rep = r.get("reply") or ""
        ck("「%s」回答含 %s" % (text, expect), expect in rep,
           "stage=%s reply=%s" % (r.get("stage"), rep[:70]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", action="store_true", help="只跑引擎层")
    args = ap.parse_args()

    print("=" * 70)
    print("数据分析能力测试")
    print("=" * 70)

    test_engine()
    if not args.engine:
        test_parse()
        test_e2e()

    print()
    print("=" * 70)
    print("通过 %d / %d" % (PASS, PASS + FAIL))
    if FAILS:
        print("失败：")
        for x in FAILS:
            print("  - %s" % x)
    else:
        print("全绿。")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
