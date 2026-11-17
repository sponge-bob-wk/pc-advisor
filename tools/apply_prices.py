# -*- coding: utf-8 -*-
"""apply_prices.py —— 把多源采集的价格落进 machine_pool.json

## 口径纪律（本脚本的核心约束，改之前先读）

`machine_pool.meta.priceFields` 定义：
    list=划线价 / discounts=可叠券 / net=实付（算涨幅只能用这个） / subsidy=页面补贴 / final=到手价

第三方快照价**不是到手价**。所以：

| 抓到的 | 写进哪 | 能不能进 net/final |
|---|---|---|
| ZOL 参考价 | `price.list`（仅当为空）+ `priceSnapshots.zolRef` | ❌ |
| 京东/天猫/苏宁平台价 | `priceSnapshots.{jd,tmall,suning}` | ❌ |
| 商家报价区间 | `priceSnapshots.merchant{Min,Max,Count}` | ❌ |

**绝不写 net / final / subsidy。** 这三个只认京东商品页直读。
写进去会让「电脑涨价分析」的涨幅计算全部失真 —— 这是最坏的一类数据错误。

## 已有的京东实采价不动

80 台非 ZOL 机器本来就有 net/final（京东实采，带 basisNote）。
本脚本只处理 `price.list` 为空的情况，且**不覆盖任何已有值**。

## 用法

    python apply_prices.py            # 演练，打印将要写什么
    python apply_prices.py --apply    # 落盘（自动备份）
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POOL = os.path.join(ROOT, "data", "machine_pool.json")
RAW = os.path.join(ROOT, "data", "zol", "prices-2026-11-16.jsonl")

SNAPSHOT_KEYS = ("zolRef", "jd", "tmall", "suning",
                 "merchantMin", "merchantMax", "merchantCount")

# 手机/平板不是笔记本：不该占价格字段，否则「243 台机型的均价/涨幅」会被手机价拉歪。
# 这 7 台本身是从 ZOL 列表页误采进池的，删不删等用户拍板，但**价格一律不写**。
NON_PC = re.compile(r"(iPhone|手机|Pura|nova|Redmi\s*Note|^\s*vivo|iPad|Mate\s*Pad|骁龙|天玑)", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(RAW):
        print("没有采集结果：%s（先跑 price_multisource.py）" % RAW)
        return

    records = {}
    for line in open(RAW, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        records[r["id"]] = r

    print("采集记录：%d 台" % len(records))

    pool = json.load(open(POOL, encoding="utf-8"))
    n_snap = 0
    n_list = 0
    n_skip_pc = 0
    detail = []

    for m in pool["machines"]:
        rec = records.get(m["id"])
        if not rec:
            continue
        if NON_PC.search(str(m.get("name") or "")):
            n_skip_pc += 1
            continue
        prices = rec.get("prices") or {}
        if not prices:
            continue

        snap = dict(m.get("priceSnapshots") or {})
        changed = False
        for k in SNAPSHOT_KEYS:
            src = snap.get("_source") or ""
            same_source = "ZOL detail" in src or not src
            if k not in prices:
                # 采集端已判定不可靠（如重复的天猫价）→ 把旧值一并清掉，
                # 否则上一轮写进去的错值会一直留着。
                if same_source and snap.get(k) is not None:
                    snap.pop(k, None)
                    changed = True
                continue
            if snap.get(k) is None or same_source:
                if snap.get(k) != prices[k]:
                    snap[k] = prices[k]
                    changed = True
        if changed:
            snap["_capturedAt"] = rec.get("capturedAt")
            snap["_source"] = "ZOL detail 页（参考报价/平台快照/经销商区间）"
            m["priceSnapshots"] = snap
            n_snap += 1

        # price.list 只在为空时补，且用 ZOL 参考价（媒体口径的"划线价"最接近）
        price = m.get("price") if isinstance(m.get("price"), dict) else {}
        filled_list = False
        if price.get("list") is None and prices.get("zolRef"):
            price["list"] = prices["zolRef"]
            price.setdefault("net", None)
            price.setdefault("final", None)
            price["basis"] = "ZOL 参考报价（媒体快照）"
            price["note"] = ("ZOL 参考报价，非到手价。net/final 需京东商品页直读，本轮未取得。"
                             "平台快照见 priceSnapshots。")
            m["price"] = price
            n_list += 1
            filled_list = True
            detail.append((m["id"], (m.get("name") or "")[:26], prices["zolRef"],
                           prices.get("merchantMin"), prices.get("merchantMax")))

        bf = set(m.get("backfilled") or [])
        bf.add("priceSnapshots")
        if filled_list:
            bf.add("price.list")
        m["backfilled"] = sorted(bf)
        df = dict(m.get("dataFrom") or {})
        df["priceSnapshots"] = "ZOL detail 页 %s" % rec.get("capturedAt")
        m["dataFrom"] = df

    print("priceSnapshots 写入：%d 台" % n_snap)
    print("price.list 补空：%d 台" % n_list)
    print("手机/平板跳过：%d 台（非笔记本口径，价格不写）" % n_skip_pc)
    print()
    print("=== price.list 新补的机型 ===")
    for mid, name, ref, lo, hi in detail:
        print("  %-16s %-26s 参考=%s  商家区间=%s-%s" % (mid, name, ref, lo, hi))

    if args.apply:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写入 %s（备份 %s）" % (POOL, bak))
    else:
        print("\n（演练模式，未写文件；加 --apply 落盘）")


if __name__ == "__main__":
    main()
