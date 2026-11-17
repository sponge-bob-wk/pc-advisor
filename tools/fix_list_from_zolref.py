# -*- coding: utf-8 -*-
"""fix_list_from_zolref.py —— 把误用天猫快照填充的 price.list 校正为 ZOL 参考价

## 问题

`zol_price.py`（早期版本）在补 `price.list` 时，优先用了 `ps.get("jd") or ps.get("tmall")`。
而 ZOL 详情页的**天猫价不可靠**：

    zol-2137389  机械革命蛟龙16Pro   list=10299  ← 天猫价
                 zolRef=6199                    ← ZOL 参考价（差 4100！）
    zol-2128664  惠普暗影精灵11      list=8698   ← 天猫价
                 zolRef=9999                    ← 差 1301

更硬的证据：3 台配置完全不同的「机械革命极光X」（RTX4060/4060/4070）
拿到**同一个**天猫价 5788 —— 那不可能是本机价，是详情页侧栏推荐位的价格。
全量统计：`tmall` 里 6500 出现 9 次、8698/5788/18799 各 5 次，
而 `zolRef`/`jd` 的重复度都在正常范围。

`price.list` 的语义是「划线价/标价」，用错的价会直接影响涨幅判断。

## 做法

- 凡 `price.basis == "zol快照·天猫"` 且 `priceSnapshots.zolRef` 存在的，
  把 `price.list` 改成 `zolRef`，`basis` 改为「ZOL 参考报价」；
- **不动**任何 `net`/`final`/`subsidy`（那三个是京东直读，口径不同）；
- 没有 `zolRef` 的保持原样并列出，交给人工。

## 用法

    python fix_list_from_zolref.py            # 演练
    python fix_list_from_zolref.py --apply    # 落盘
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    pool = json.load(open(POOL, encoding="utf-8"))
    fixed, skipped = [], []

    for m in pool["machines"]:
        pr = m.get("price") if isinstance(m.get("price"), dict) else {}
        if not pr:
            continue
        basis = str(pr.get("basis") or "")
        if "天猫" not in basis:
            continue
        snap = m.get("priceSnapshots") or {}
        ref = snap.get("zolRef")
        if not ref:
            skipped.append((m["id"], (m.get("name") or "")[:26], pr.get("list")))
            continue
        old = pr.get("list")
        # 值已经等于 zolRef 的，只改口径标签（标签写错会让下游误判来源）
        if old == ref:
            if "天猫" in basis:
                fixed.append((m["id"], (m.get("name") or "")[:26], old, ref))
                if args.apply:
                    pr["basis"] = "ZOL 参考报价（媒体快照）"
                    pr["note"] = ("ZOL 参考报价，非到手价。net/final 需京东商品页直读，本轮未取得。"
                                  "平台快照见 priceSnapshots。")
                    m["price"] = pr
            continue
        fixed.append((m["id"], (m.get("name") or "")[:26], old, ref))
        if args.apply:
            pr["list"] = ref
            pr["basis"] = "ZOL 参考报价（媒体快照）"
            pr["note"] = ("ZOL 参考报价，非到手价。net/final 需京东商品页直读，本轮未取得。"
                          "平台快照见 priceSnapshots。")
            m["price"] = pr
            df = dict(m.get("dataFrom") or {})
            df["price.list"] = "修正：原值来自不可靠的天猫推荐位价 %s → ZOL 参考价 %s" % (old, ref)
            m["dataFrom"] = df

    print("需校正 price.list：%d 台" % len(fixed))
    for mid, name, old, ref in fixed:
        print("  %-16s %-26s %8s → %8s  (差 %+d)" % (mid, name, old, ref, (ref or 0) - (old or 0)))
    if skipped:
        print()
        print("无 zolRef、保留原值：%d 台" % len(skipped))
        for mid, name, v in skipped[:10]:
            print("  %-16s %-26s list=%s" % (mid, name, v))

    if args.apply and fixed:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写入 %s（备份 %s）" % (POOL, bak))
    elif not args.apply:
        print("\n（演练模式，未写文件；加 --apply 落盘）")


if __name__ == "__main__":
    main()
