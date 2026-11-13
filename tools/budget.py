# -*- coding: utf-8 -*-
"""budget.py —— 给一个预算，回答「哪些专业方向现在就能学」。

为什么需要它：
  pick.py 是「先选专业，再看买哪台」。但学生真实的决策顺序经常是反的 ——
  家里先说"最多给你六千"，然后才问"我这分数能报的专业，哪些能学得动"。
  这个工具把方向倒过来：预算是约束，专业组是待筛的选项。

口径（和 pick.py 完全一致，不另立一套）：
  · 门槛判定用 lib_judge.judge，字段矩阵用 field_matrix.json
  · 「买得到」= 判定为 OK 或 DEGRADED，且到手价 ≤ 预算
  · 买不到时不算含糊：直接告诉你差多少钱
  · 没有价格的机器不参与预算比较（宁可不报，也不猜价）

用法：
    python tools/budget.py 6000
    python tools/budget.py 6000 --all      # 列出每组具体机型
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_judge import judge, r_fields_of  # noqa: E402
from pick import value_metric, _bench_for, money  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load(n):
    return json.loads((DATA / n).read_text(encoding="utf-8"))


def scan(pool, groups, matrix, budget):
    """每组算一次：够用且买得起几台、最便宜多少、该组维度下每元最优是哪台。"""
    out = []
    for g in groups:
        gid = g["id"]
        rf = r_fields_of(matrix, gid)
        req = g.get("req") or {}
        gates = g.get("hardGates") or []
        vmk, vlabel = value_metric(gid, rf)

        good, over = [], []
        for m in pool:
            v, why, _miss = judge(m, req, rf, gates)
            if v not in ("OK", "DEGRADED"):
                continue
            pr = m.get("price") or {}
            price = pr.get("final") or pr.get("net")
            if not price:
                continue
            (good if price <= budget else over).append((price, m))

        good.sort(key=lambda t: t[0])
        over.sort(key=lambda t: t[0])

        # 每元性能：只在买得起的机器之间比才有意义
        best_value = None
        if len(good) >= 2:
            scored = []
            for price, m in good:
                b = _bench_for(m, vmk)
                if b:
                    scored.append((b / price, m, b, price))
            if scored:
                scored.sort(key=lambda t: -t[0])
                best_value = scored[0]

        out.append({
            "gid": gid, "name": g["name"], "tagline": g.get("tagline", ""),
            "n": len(good), "cheapest": good[0] if good else None,
            "gap": (over[0][0] - budget) if (not good and over) else None,
            "over": over, "good": good, "vm": vmk, "vlabel": vlabel,
            "best_value": best_value, "majors": g.get("majors") or [],
        })
    return out


def major_names(g, k=3):
    names = []
    for it in g.get("majors") or []:
        names.append(it if isinstance(it, str) else it.get("name"))
    return "、".join([n for n in names if n][:k])


def main():
    ap = argparse.ArgumentParser(description="给预算，看哪些专业方向买得到")
    ap.add_argument("budget", type=int, nargs="?", default=None, help="预算（元）")
    ap.add_argument("--all", action="store_true", help="列出具体机型")
    a = ap.parse_args()
    if not a.budget:
        print(__doc__)
        return 1

    pool = load("machine_pool.json")["machines"]
    fmat = load("field_matrix.json")
    majors = load("majors.json")
    rows = scan(pool, majors["groups"], fmat["matrix"], a.budget)

    ok_rows = [r for r in rows if r["n"]]
    no_rows = [r for r in rows if not r["n"]]
    ok_rows.sort(key=lambda r: -r["n"])
    no_rows.sort(key=lambda r: (r["gap"] is None, r["gap"] or 0))

    print()
    print(f"预算 {a.budget:,} 元 —— {len(ok_rows)}/{len(rows)} 个方向现在就能买")
    print("=" * 74)
    print(f"{'专业方向':<14}{'买得到':>7}   最便宜的够用款")
    print("-" * 74)
    for r in ok_rows:
        price, m = r["cheapest"]
        b = _bench_for(m, r["vm"])
        extra = f"   {r['vlabel'].replace('每元', '')} {b:,}" if b else ""
        print(f"{r['name']:<14}{r['n']:>4} 台   {money(price):>9} 元  {m['name'][:22]}{extra}")
        print(f"{'':14}{'':>7}   {major_names(r)} …")

    if no_rows:
        print()
        print("这些方向这个预算买不到（不是没机器，是够用的机器都超了）")
        for r in no_rows:
            if r["gap"] is not None:
                print(f"  {r['name']:<12} 最少要 {money(a.budget + r['gap'])} 元，"
                      f"差 {money(r['gap'])} 元  —— {r['over'][0][1]['name'][:26]}")
            else:
                print(f"  {r['name']:<12} 库里没有够用且有价格的机器（数据缺口，不是买不到）")

        # 加多少钱能解锁什么 —— 按价格阶梯累计显示，比逐个报"差多少"更接近真实决策
        steps = sorted({r["gap"] for r in no_rows if r["gap"]})
        if steps:
            print()
            print("  往上加钱的阶梯（同样的钱能多换来几个方向）")
            shown = 0
            for s in steps:
                names = [r["name"] for r in no_rows
                         if r["gap"] and r["gap"] <= s]
                print(f"    到 {money(a.budget + s):>9}（+{money(s)}）"
                      f" 可买 {len(ok_rows) + len(names)}/{len(rows)} 个方向"
                      f"  新解锁：{'、'.join(sorted({r['name'] for r in no_rows if r['gap'] == s}))}")
                shown += 1
                if shown >= 4 and s != steps[-1]:
                    rest = [r for r in no_rows if r["gap"] and r["gap"] > s]
                    if rest:
                        print(f"    …… 再往上还有 {len(rest)} 个方向要更多预算")
                    break

    # 只在"最划算"确实不是"最便宜"那台时才提。
    # 否则会出现"更划算的选择：X，比最便宜那台贵一点"—— 而 X 就是最便宜那台，自相矛盾。
    vrows = []
    same = []
    for r in ok_rows:
        bv = r["best_value"]
        if not bv:
            continue
        ratio, m, b, price = bv
        cheap_m = r["cheapest"][1] if r["cheapest"] else None
        if cheap_m is None:
            continue
        if m["id"] == cheap_m["id"]:
            same.append(r["name"])
            continue
        vrows.append((r, m, b, price))
    if vrows:
        print()
        print("同预算内更划算的选择（比最便宜那台贵一点，但每元性能更高）")
        for r, m, b, price in vrows:
            cheap = r["cheapest"][0]
            delta = price - cheap
            extra = f"，贵 {money(delta)}" if delta > 5 else ""
            print(f"  {r['name']:<12} {money(price):>9} 元  {m['name'][:24]}  "
                  f"{r['vlabel'].replace('每元', '')} {b:,}{extra}")
    if same:
        print()
        print(f"  另 {len(same)} 个方向：最便宜的那台同时也是每元性能最优，不用纠结")

    if a.all:
        print()
        print("逐组机型明细")
        for r in ok_rows:
            print(f"\n【{r['name']}】买得到 {r['n']} 台")
            for price, m in r["good"][:8]:
                b = _bench_for(m, r["vm"])
                tag = f"  {r['vlabel'].replace('每元', '')} {b:,}" if b else ""
                print(f"  {money(price):>9} 元  {m['name']}{tag}")
            if len(r["good"]) > 8:
                print(f"  …… 还有 {len(r['good']) - 8} 台")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
