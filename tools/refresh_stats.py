# -*- coding: utf-8 -*-
"""refresh_stats.py —— 重算 machine_pool.json 的 meta.stats / meta.updated

## 为什么要单独一个脚本

`machine_pool.meta.stats` 里的数字（withPrice / withEfficiency / withThrottle…）
是**手写快照**，补数据时没人回头改，于是长期漂移：

    2026-11-16 实测 vs 库里记的
      withPrice       236  vs  62     ← 差 174
      withEfficiency   81  vs  36     ← 差 45

后果：`make_pick_page.py` 把这个 stats 直接嵌进页面，用户在「按专业选电脑.html」
上看到的机型统计是两周前的。数字看着像真的，其实早就错了 —— 这是本项目最不能犯的错。

## 做法

- 所有计数**从 machines 数组实算**，不读任何缓存；
- `withPrice` 拆成三个口径分别计数（见下），不再混为一个数；
- 同时刷新 `meta.updated`。

## 口径定义（与 lib_judge.price_of 对齐）

| 键 | 含义 |
|---|---|
| `withRealPrice` | 有 `price.net` 或 `price.final`（京东直读的到手价） |
| `withListPrice` | 有 `price.list`（划线价/标价） |
| `withSnapshot` | 有 `priceSnapshots.zolRef` 等三方快照 |
| `withAnyPrice` | 上面任一成立 |

`withPrice` 保留为 `withAnyPrice` 的别名，避免旧代码读不到。

## 用法

    python refresh_stats.py            # 演练
    python refresh_stats.py --apply    # 落盘
"""
import argparse
import json
import os
import shutil
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POOL = os.path.join(ROOT, "data", "machine_pool.json")


def compute(machines):
    def has_any_price(m):
        pr = m.get("price") or {}
        snap = m.get("priceSnapshots") or {}
        return (pr.get("net") is not None or pr.get("final") is not None
                or pr.get("list") is not None or snap.get("zolRef") is not None)

    def has_real(m):
        pr = m.get("price") or {}
        return pr.get("net") is not None or pr.get("final") is not None

    def has_list(m):
        return (m.get("price") or {}).get("list") is not None

    def has_snap(m):
        return (m.get("priceSnapshots") or {}).get("zolRef") is not None

    n = len(machines)

    def spec_src(m):
        """规格主来源。`dataFrom.spec` 的值是来源名（'zol' / 'specs' / 'jd'…），
        不是布尔 —— 直接 `if ...get("spec")` 会把 zol 来源也算成「来自 specs」，
        首版就是这么把 fromSpecs 算成 243 的。"""
        return str((m.get("dataFrom") or {}).get("spec") or "").lower()

    st = {
        "total": n,
        # 规格来自人工核对过的 specs_verified.json（高质量子集）
        "fromSpecs": sum(1 for m in machines if "specs" in spec_src(m)),
        # 规格只来自平台采集（ZOL/京东），未做交叉核实
        "fromCrawlOnly": sum(1 for m in machines
                             if spec_src(m) and "specs" not in spec_src(m)),
        "fromZol": sum(1 for m in machines
                       if m.get("source") == "zol" or str(m.get("id", "")).startswith("zol-")),
        "withRealPrice": sum(1 for m in machines if has_real(m)),
        "withListPrice": sum(1 for m in machines if has_list(m)),
        "withSnapshot": sum(1 for m in machines if has_snap(m)),
        "withAnyPrice": sum(1 for m in machines if has_any_price(m)),
        "withEfficiency": sum(1 for m in machines if m.get("efficiency") is not None),
        "withCertModel": sum(1 for m in machines if m.get("certModel")),
        "withThrottle": sum(1 for m in machines if m.get("throttle") is not None),
        "priceBlocked": sum(1 for m in machines if m.get("priceBlockedBy")),
    }
    st["withoutPrice"] = n - st["withAnyPrice"]
    # 旧字段别名：老代码读 withPrice，语义按"任意价格"算
    st["withPrice"] = st["withAnyPrice"]
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    pool = json.load(open(POOL, encoding="utf-8"))
    old = dict(pool.get("stats") or {})
    new = compute(pool["machines"])

    print("%-18s %10s %10s" % ("字段", "旧值", "实算"))
    print("-" * 42)
    for k in new:
        o = old.get(k)
        flag = "" if o == new[k] else "  ← 变"
        print("%-18s %10s %10s%s" % (k, o if o is not None else "-", new[k], flag))

    if args.apply:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        pool["stats"] = new
        meta = dict(pool.get("meta") or {})
        meta["updated"] = time.strftime("%Y-%m-%d")
        meta["statsNote"] = ("stats 由 tools/refresh_stats.py 从 machines 数组实算，"
                             "补数据后必须重跑，不许手写")
        pool["meta"] = meta
        json.dump(pool, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写入 %s（备份 %s）" % (POOL, bak))
    else:
        print("\n（演练模式，未写文件；加 --apply 落盘）")


if __name__ == "__main__":
    main()
