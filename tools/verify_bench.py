# -*- coding: utf-8 -*-
"""跑分数据交叉验证（2026-11-16）

## 为什么要有这个

`bench.json` 里的分数来自不同来源：ZOL 评测、太平洋评测、Notebookcheck 均值、
web-search 汇编。**只要是多来源，就一定会有不一致**，而项目红线是
"数字零写权限" —— 一个来源不明的分数进了库，下游就会拿它排序、推荐、说
"这台性能更强"。错了没人能发现，因为数字看着很合理。

所以每一条分数都必须能被三个问题检验：
  ① 同一个 benchmark，不同来源差多少？（差太多说明有一方测错了，或者是不同机型）
  ② 不同 benchmark 之间，强弱的结论一致吗？（R23 说 A>B，GB6 也说 A>B？）
  ③ 单核/多核的比例合理吗？（MP Ratio 异常 = 数据抄错或混了桌面版）

## 三项检查，各自的判据和阈值

### 检查 1：同一 benchmark 的多来源离散度
   用 `observedR23.variants` 里记录的每个实测值，算 (max-min)/median。
   · ≤ 10%  正常（不同机型散热差异的合理范围）
   · 10~20% 要注意，多核分尤其常见（功耗墙 55W 和 130W 能差一倍）
   · > 20%  标记 FAIL，必须复核
   ⚠️ **单核分不该有这么大差异** —— 单核几乎不受散热限制，同一颗 CPU
      单核差 20% 基本只能是抄错。所以单核和多核用不同的阈值。

### 检查 2：跨 benchmark 的排序一致性（这是用户要的"互相验证"）
   取所有同时有 R23 multi 和 Geekbench 6 multi 的 CPU，各自在两个榜上排名，
   算 Spearman 秩相关系数。
   · ≥ 0.85  两个 benchmark 结论一致 → 数据可信
   · 0.6~0.85 有分歧，看具体是哪些
   · < 0.6   严重不一致 → 至少一个 benchmark 的数据有问题
   ⚠️ 注意：R23 测的是渲染（重多核吞吐），GB6 测的是综合负载。
      两者本来就不该完全一致（相关系数 0.9 左右是正常的，1.0 反而可疑）。
      所以判据是"不低于 0.85"，不是"必须等于 1"。

### 检查 3：逐颗 CPU 的百分位差
   对每颗 CPU，算它在 R23 榜和 GB6 榜上的**百分位**，差值 > 30 个百分点就列出来。
   例：某颗 CPU 在 R23 排前 10%，在 GB6 排后 40% —— 差 50 个百分点，
       要么它的架构特别偏科（有可能，大小核在渲染和综合负载上表现确实不同），
       要么数据有问题。列出来人工看，不自动判错。

### 检查 4：MP Ratio（多核/单核）合理性
   · 正常范围 2 ~ 40（1 核到 24 核的跨度）
   · < 1.5  多核比单核还低 → 必然抄错
   · > 45   超过 32 核的量级 → 混进桌面版/服务器版了

## 用法

    python tools/verify_bench.py            # 只检查，不改文件
    python tools/verify_bench.py --fix      # 把 spreadPct 重算并写回（只改这一个字段）
"""
import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(os.path.dirname(HERE), "data", "bench.json")

# 单核差异容忍度比多核低得多 —— 单核几乎不吃散热，差 20% 只能是数据错
TOL_SINGLE = 0.08
TOL_MULTI = 0.20

OK, WARN, FAIL = "OK", "WARN", "FAIL"


def load():
    return json.load(open(BENCH, encoding="utf-8"))


def spread(vals):
    """离散度。返回 (pct, min, max, median)。少于 2 个样本返回 (0, ...)。"""
    v = [x for x in vals if isinstance(x, (int, float)) and x > 0]
    if len(v) < 2:
        return 0.0, (v[0] if v else None), (v[0] if v else None), (v[0] if v else None)
    v = sorted(v)
    med = v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2
    if not med:
        return 0.0, v[0], v[-1], med
    return (v[-1] - v[0]) / med, v[0], v[-1], med


def check_one(c):
    """检查单颗 CPU 的多来源一致性 + MP Ratio。返回问题列表。"""
    issues = []
    name = c.get("name") or c.get("id")
    bn = c.get("bench") or {}

    # 🔴 「离散度大」不等于「数据错」——必须先看有没有已记录的差异原因。
    #
    #    交接文档 §5.3 已经写明：「一颗 CPU 一个分数本身是错的」，
    #    Ultra 7 356H 多核从 9019（ThinkPad X1 超轻薄）到 20298（游戏本）
    #    差 2.25 倍，那是**功耗墙的真实差别**，不是抄错。
    #
    #    所以判据要分两档：
    #      observedR23.detail 或 note 里**说明了原因** → 最多 WARN（已解释）
    #      没有任何说明且离散度超限 → FAIL（可能真抄错）
    obs = c.get("observedR23") or {}
    explained = bool(obs.get("detail") or obs.get("note"))

    # --- 检查 1：多来源离散度 ---
    var = obs.get("variants") or []
    if len(var) >= 2:
        singles = [v[0] for v in var if len(v) >= 2]
        multis = [v[1] for v in var if len(v) >= 2]
        ps, s0, s1, smed = spread(singles)
        pm, m0, m1, mmed = spread(multis)
        tail = "（差异已记录原因，按功耗墙/机型条件解释）" if explained else ""
        if ps > TOL_SINGLE:
            # 单核离散：即使有解释，单核不该差太多（单核受功耗墙影响小）
            lvl = WARN if explained else (FAIL if ps > TOL_SINGLE * 2 else WARN)
            issues.append((lvl,
                           "R23 单核多来源差 %.0f%%（%s~%s）—— 单核不该有这么大差异%s"
                           % (ps * 100, s0, s1, tail)))
        if pm > TOL_MULTI:
            lvl = WARN if explained else (FAIL if pm > TOL_MULTI * 1.5 else WARN)
            issues.append((lvl,
                           "R23 多核多来源差 %.0f%%（%s~%s）—— %s%s"
                           % (pm * 100, m0, m1,
                              "已记录的功耗墙差异" if explained else "可能是功耗墙差异，也可能是抄错",
                              "")))

    # --- 检查 4：MP Ratio ---
    for tag in ("r23", "r24", "r26"):
        b = bn.get(tag)
        if not b:
            continue
        s, m = b.get("single"), b.get("multi")
        if not (s and m):
            continue
        r = m / s
        if r < 1.5:
            issues.append((FAIL, "%s MP Ratio=%.2f（多核低于单核的 1.5 倍）→ 数字抄反或抄错" % (tag, r)))
        elif r > 45:
            issues.append((FAIL, "%s MP Ratio=%.0f（超过 32 核量级）→ 可能混进桌面版/服务器版" % (tag, r)))

    return name, issues


def spearman(xs, ys):
    """秩相关系数。纯标准库实现，不引 scipy。"""
    n = len(xs)
    if n < 3:
        return None

    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    if not dx or not dy:
        return None
    return num / (dx * dy)


def percentile_map(pairs):
    """{name: 分数} → {name: 百分位 0~100}（越大越靠前记为越高百分位）。"""
    items = sorted(pairs.items(), key=lambda kv: kv[1])
    n = len(items)
    return {k: (i / max(1, n - 1)) * 100 for i, (k, _) in enumerate(items)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="把 spreadPct 重算写回")
    args = ap.parse_args()

    d = load()
    cpus = d.get("cpus") or []
    print("bench.json CPU: %d 条" % len(cpus))
    print()

    # ============ 检查 1 + 4：逐颗 ============
    print("=" * 74)
    print("一、逐颗检查：多来源离散度 + MP Ratio")
    print("=" * 74)
    n_ok = n_warn = n_fail = 0
    for c in cpus:
        name, issues = check_one(c)
        if not issues:
            n_ok += 1
            continue
        worst = FAIL if any(l == FAIL for l, _ in issues) else WARN
        if worst == FAIL:
            n_fail += 1
        else:
            n_warn += 1
        print("[%s] %s" % (worst, name))
        for lv, msg in issues:
            print("      · %s" % msg)
    print()
    print("  正常 %d / 注意 %d / 不合格 %d" % (n_ok, n_warn, n_fail))

    # ============ 检查 2：跨 benchmark 秩相关 ============
    print()
    print("=" * 74)
    print("二、跨 benchmark 排序一致性（交叉验证的核心）")
    print("=" * 74)
    pairs = []          # (name, r23_multi, gb6_multi, r24_multi)
    for c in cpus:
        bn = c.get("bench") or {}
        r23 = (bn.get("r23") or {}).get("multi")
        # 🔴 字段名有两个：`gb6`（NBC 批量入库用的）和 `geekbench6`（早期手工填的）。
        #    只读一个会漏掉另一批 —— 2026-11-16 实测：只读 geekbench6 时
        #    样本只有 3 颗（rho=0.500，看着像"数据有问题"），
        #    两个都读是 13 颗，才够做秩相关（交接文档要求 10~15 颗）。
        gb = ((bn.get("gb6") or {}).get("multi")
              or (bn.get("geekbench6") or {}).get("multi"))
        r24 = (bn.get("r24") or {}).get("multi")
        if r23 and gb:
            pairs.append((c.get("name"), r23, gb, r24))
    print("同时有 R23 多核 和 Geekbench6 多核 的 CPU: %d 颗" % len(pairs))
    if len(pairs) >= 3:
        rho = spearman([p[1] for p in pairs], [p[2] for p in pairs])
        if rho is not None:
            verdict = "一致，数据可信" if rho >= 0.85 else (
                "有分歧，看下面的逐颗清单" if rho >= 0.6 else "严重不一致，至少一方数据有问题")
            print("  Spearman 秩相关 rho = %.3f  → %s" % (rho, verdict))
            print("  （R23 测渲染吞吐，GB6 测综合负载，正常在 0.9 左右；"
                  "1.0 反而可疑）")
    else:
        print("  ⚠️ 样本不足 3 颗，无法算相关性 —— 需要先补 Geekbench 数据")

    # ============ 检查 3：逐颗百分位差 ============
    print()
    print("=" * 74)
    print("三、逐颗百分位差（两个榜上排名差 >30 个百分点的列出来）")
    print("=" * 74)
    if len(pairs) >= 3:
        p23 = percentile_map({p[0]: p[1] for p in pairs})
        pgb = percentile_map({p[0]: p[2] for p in pairs})
        rows = sorted(((abs(p23[n] - pgb[n]), n, p23[n], pgb[n]) for n in p23),
                      reverse=True)
        suspicious = [r for r in rows if r[0] > 30]
        if suspicious:
            for gap, n, a, b in suspicious:
                print("  [注意] %-22s R23 百分位 %3.0f vs GB6 %3.0f（差 %2.0f）"
                      % (n, a, b, gap))
            print()
            print("  ⚠️ 这不一定错 —— 大小核架构在\"渲染\"和\"综合负载\"上本来就可能偏科。")
            print("     但如果某颗差的特别大（>50），优先怀疑数据抄错。")
        else:
            print("  全部 %d 颗的百分位差都在 30 以内，两个榜结论一致。" % len(rows))
            print("  最大差 %.0f 个百分点（%s）" % (rows[0][0], rows[0][1]))

    # ============ 覆盖率 ============
    print()
    print("=" * 74)
    print("四、数据覆盖率（决定\"能验证到什么程度\"）")
    print("=" * 74)
    for tag, label in [("r23", "Cinebench R23"), ("r24", "Cinebench R24"),
                       ("r26", "Cinebench R26")]:
        n = sum(1 for c in cpus if (c.get("bench") or {}).get(tag))
        print("  %-16s %2d/%d = %3.0f%%" % (label, n, len(cpus), 100.0 * n / max(1, len(cpus))))
    # Geekbench 6 有两个字段名（见 §二 的注释），覆盖率也要合并算，
    # 否则会出现「上面说 16 颗可比、下面说只有 3 颗有数据」的自相矛盾。
    n_gb = sum(1 for c in cpus
               if (c.get("bench") or {}).get("gb6")
               or (c.get("bench") or {}).get("geekbench6"))
    print("  %-16s %2d/%d = %3.0f%%" % ("Geekbench 6", n_gb, len(cpus),
                                        100.0 * n_gb / max(1, len(cpus))))

    gpus = d.get("gpus") or []
    n_ts = sum(1 for g in gpus if (g.get("bench") or {}).get("timeSpyGraphics"))
    n_sn = sum(1 for g in gpus if (g.get("bench") or {}).get("steelNomad"))
    print("  %-16s %2d/%d" % ("3DMark TimeSpy", n_ts, len(gpus)))
    print("  %-16s %2d/%d" % ("3DMark SteelNomad", n_sn, len(gpus)))

    # ============ --fix ============
    if args.fix:
        changed = 0
        for c in cpus:
            var = (c.get("observedR23") or {}).get("variants") or []
            if len(var) < 2:
                continue
            pm, _, _, _ = spread([v[1] for v in var if len(v) >= 2])
            old = (c.get("observedR23") or {}).get("spreadPct")
            new = round(pm * 100)
            if old != new:
                c["observedR23"]["spreadPct"] = new
                changed += 1
        if changed:
            json.dump(d, open(BENCH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print()
        print("已重算 spreadPct：%d 条被修正" % changed)


if __name__ == "__main__":
    main()
