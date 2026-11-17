# -*- coding: utf-8 -*-
"""price_cross_check.py —— 同一台机器的多来源价格交叉验证

## 为什么需要

一台机器可能有 4 个价格来源：

    zolRef        ZOL 参考报价（媒体口径）
    jd            ZOL 详情页上的京东平台价
    tmall         ZOL 详情页上的天猫平台价
    merchantMin   经销商最低报价
    net/final     京东商品页直读的到手价（最权威）

这些数字**本该彼此接近**（同一台机器、同一时期）。差得远，说明：
  ① 其中一个抓错了（串到别的 SKU / 别的机型）
  ② 或者是不同配置（8G 版 vs 16G 版）
  ③ 或者时间跨度大（涨价/降价）

## 判定阈值（怎么定出来的）

实测 126 台多来源机器，正常偏差（zolRef vs merchantMin）多数在 10% 以内 ——
经销商价本来就比参考价低一点。

所以：

    ≤ 10%   normal    正常，多个来源互相印证
    10~30%  watch     要留意，可能是不同配置
    > 30%   suspect   高度可疑，必须人看

⚠️ **只比同一量纲**：zolRef / jd / tmall 都是「平台标价」，
   merchantMin 是「经销商报价」，net/final 是「到手价」。
   拿标价去比到手价会制造假冲突（到手价含券含国补，本来就低一截）。
   本脚本把「到手价」单独列为参考，**不参与冲突判定**。

## 用法

    python price_cross_check.py              # 报告
    python price_cross_check.py --suspect    # 只列可疑的
    python price_cross_check.py --json       # 机器可读（给刷新链路用）
"""
import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
POOL = os.path.join(ROOT, "data", "machine_pool.json")

# 参与「同量纲」比较的：都是平台标价口径
LIST_TIER = ("zolRef", "jd", "tmall", "merchantMin")
# 到手价单独列，不参与冲突判定
REAL_TIER = ("net", "final")

NORMAL = 0.10
WATCH = 0.30

# 🔴 2026-11-17 实测：`merchantMin` 这个来源**不可靠**。
#
#    91 个值里 `6434` 出现 **9 次**、`8779` 出现 4 次 ——
#    不同型号、不同配置的机器拿到同一个"经销商最低价"，
#    那不可能是各台的报价，是 ZOL 页面侧栏推荐位的价格（和天猫价同一个坑）。
#
#    后果：拿它参与交叉验证会制造大量假冲突 ——
#    33 台「可疑」里至少 15 台是因为 merchantMin 撞了重复值。
#
#    所以：**merchantMin 默认不参与同量纲比较**，只在报告里单独列出。
#    要恢复它，得先证明它的取值是逐台唯一的。
SUSPECT_SOURCE = "merchantMin"


def find_unreliable_sources(machines):
    """自动识别「串台来源」。

    🔴 试了三种判据，前两种都失败，记下来免得重走：

       ① 「重复占比 > 20%」→ 把 zolRef 误杀（7999 出现 4 次是正常同价）
       ② 「同值覆盖 ≥5 个型号前缀」→ 9 台都叫「联想拯救者」，前缀骗过判据
       ③ 「与其它来源偏差 >50% 的机器占比 >40%」→ merchantMin 只覆盖 91 台，
          其中大部分机器只有一个来源，算不出占比

    ✅ 最终判据：**同一个价格值，出现在价格量级差 2 倍以上的机器上**
       = 那不可能都是真价，是推荐位串台。

       实测 merchantMin：6434 出现在 zolRef 9548~9848 的机器上（差 1.5 倍），
       8779 出现在 zolRef 14199~25399 的机器上（差 1.6~2.9 倍）→ 判死。
       实测 zolRef：7999 出现在 4 台，它们的其它来源也都在 7000~9000 → 正常。
    """
    import collections

    def collect_all(m):
        snap = m.get("priceSnapshots") or {}
        return {k: v for k in LIST_TIER
                if isinstance(v := snap.get(k), (int, float)) and v > 0}

    bad = set()
    for key in LIST_TIER:
        # 收集「这个来源的某个值」→ 那些机器上「其它来源的价格区间」
        buckets = collections.defaultdict(list)
        for m in machines:
            vals = collect_all(m)
            if key not in vals or len(vals) < 2:
                continue
            others = [v for k, v in vals.items() if k != key]
            buckets[vals[key]].append((min(others), max(others)))
        for v, spans in buckets.items():
            if len(spans) < 3:
                continue
            # 这些机器「其它来源」的最低值和最高值，跨度是否 ≥2 倍
            lo = min(s[0] for s in spans)
            hi = max(s[1] for s in spans)
            if lo > 0 and hi / lo >= 2.0:
                bad.add(key)
                break
    return bad


def collect(m, exclude=()):
    """返回 {来源: 价格}，只收有值的。exclude 里的来源不收。"""
    out = {}
    snap = m.get("priceSnapshots") or {}
    for k in LIST_TIER:
        if k in exclude:
            continue
        v = snap.get(k)
        if isinstance(v, (int, float)) and v > 0:
            out[k] = v
    pr = m.get("price") or {}
    for k in REAL_TIER:
        v = pr.get(k)
        if isinstance(v, (int, float)) and v > 0:
            out[k] = v
            break          # net/final 是同一件事，只取一个
    return out


def analyze(m, exclude=()):
    """对一台机器算偏差。返回 dict 或 None（来源不足 2 个）。"""
    vals = collect(m, exclude)
    list_vals = {k: v for k, v in vals.items() if k in LIST_TIER}
    real_val = next((vals[k] for k in REAL_TIER if k in vals), None)
    if len(list_vals) < 2:
        return None
    lo = min(list_vals.values())
    hi = max(list_vals.values())
    if lo <= 0:
        return None
    spread = (hi - lo) / lo
    if spread <= NORMAL:
        level = "normal"
    elif spread <= WATCH:
        level = "watch"
    else:
        level = "suspect"
    return {
        "id": m.get("id"),
        "name": m.get("name"),
        "sources": list_vals,
        "real": real_val,
        "min": lo,
        "max": hi,
        "spread": round(spread, 4),
        "level": level,
        # 偏差最大的那一对，方便定位
        "pair": (min(list_vals, key=list_vals.get), max(list_vals, key=list_vals.get)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suspect", action="store_true", help="只列 >30% 的")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pool = json.load(open(POOL, encoding="utf-8"))
    machines = pool["machines"]

    # 自动识别不可靠来源（同一取值在多台机器上重复）
    unreliable = find_unreliable_sources(machines)
    if unreliable:
        print("⚠ 以下来源被判定不可靠（同一取值在多台机器上重复），不参与比较：")
        for k in sorted(unreliable):
            print("    %s" % k)
        print()

    rows = []
    for m in machines:
        r = analyze(m, exclude=unreliable)
        if r:
            rows.append(r)

    by_level = {"normal": [], "watch": [], "suspect": []}
    for r in rows:
        by_level[r["level"]].append(r)

    if args.json:
        print(json.dumps({
            "checked": len(rows),
            "normal": len(by_level["normal"]),
            "watch": len(by_level["watch"]),
            "suspect": len(by_level["suspect"]),
            "items": rows,
        }, ensure_ascii=False, indent=1))
        return

    print("多来源价格交叉验证")
    print("=" * 72)
    print("可核对（≥2 个平台标价来源）：%d 台" % len(rows))
    print("  normal  ≤10%%   %3d 台" % len(by_level["normal"]))
    print("  watch   10~30%%  %3d 台" % len(by_level["watch"]))
    print("  suspect >30%%    %3d 台" % len(by_level["suspect"]))
    print()

    def show(rs, title):
        if not rs:
            return
        print("=" * 72)
        print(title)
        print("=" * 72)
        for r in sorted(rs, key=lambda x: -x["spread"]):
            a, b = r["pair"]
            extra = ""
            if r["real"]:
                # 到手价一般比标价低，低太多也值得看
                d = (r["min"] - r["real"]) / r["real"]
                if d > 0.30:
                    extra = "  ⚠ 到手价 ¥%s 比最低标价还低 %.0f%%，可能标价过期" % (
                        int(r["real"]), d * 100)
            print("  %-26s %+.0f%%   %s ¥%s / %s ¥%s%s" % (
                (r["name"] or "")[:24], r["spread"] * 100,
                a, int(r["sources"][a]), b, int(r["sources"][b]), extra))
        print()

    if args.suspect:
        show(by_level["suspect"], "可疑（>30%，必须人看）")
        return

    show(by_level["suspect"], "可疑（>30%，必须人看）")
    show(by_level["watch"], "留意（10~30%）")

    print("=" * 72)
    print("说明")
    print("=" * 72)
    print("  · 只比「平台标价」同量纲（zolRef / jd / tmall / merchantMin）")
    print("  · 到手价（net/final）单独参考，不参与冲突判定 ——")
    print("    它含券含国补，本来就比标价低一截，拿来比会制造假冲突")
    print("  · 经销商价通常比参考价低 5~15%，属正常")


if __name__ == "__main__":
    main()
