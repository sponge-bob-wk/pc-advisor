# -*- coding: utf-8 -*-
"""
把淘宝爬虫数据转成与 prices.json 同构的 offers 列表。

关键决策（如实记录）：
1. 淘宝数据**没有 cat 字段**，必须按规则推断。推断结果一律标 `catSrc: "推断"`，
   不伪装成原始字段。原始字段缺失就标缺失，这是本项目的一贯做法。
2. 淘宝的 `型号提示` 是标题式长串，不是规范机型名。**不做同名合并**，
   统一加前缀 `tb-` 与京东 id 物理隔离，避免两个源互相污染。
3. 价格口径有 5 种（券后价/到手价/净价/卡片价/空）。**只保留「到手价」「净价」**，
   券后价/卡片价含未兑现优惠或引流价，直接当到手价会误导。
   —— 实测：有 2570 元的 GeekPro 27 寸整机，明显是引流价。

用法：
    python build_taobao.py            # 只统计，不写文件
    python build_taobao.py --write    # 写 data/taobao.json
"""
import json
import os
import re
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # E:\电脑涨价分析
SRC = r"E:\下载\淘宝电脑采集_2026-11-08.xlsx"
OUT = os.path.join(ROOT, "data", "taobao.json")

# ---- 品类推断规则 ----------------------------------------------------
# 顺序有意义：先判 gaming（特征最明显），再 desktop，最后 thin。
GAMING_SERIES = ("ROG 幻", "ROG 枪神", "ROG 魔霸", "天选", "拯救者", "暗影精灵",
                 "暗影骑士", "游匣", "机械师 F117", "雷神 911", "神舟 战神",
                 "机械革命 极光", "光影精灵", "星 Book Pro")
DESKTOP_SERIES = ("GeekPro", "台式", "主机", "整机")

# 明确这几个系列里有「轻薄」子款，不能整体判 gaming
THIN_OVERRIDE = ("天选 Air", "星 Book Pro 14")


def guess_cat(row):
    """返回 (cat, 依据)。依据要能让人复核，不许只给结论。"""
    series = str(row.get("系列") or "")
    name = str(row.get("型号提示") or "")
    gpu_kind = str(row.get("显卡类型") or "")
    inch = row.get("屏幕（英寸）")

    if any(k in series for k in THIN_OVERRIDE):
        return "thin", "系列属于轻薄子款(%s)" % series
    if any(k in series or k in name for k in DESKTOP_SERIES):
        return "desktop", "系列/标题含台式整机特征"
    if any(k in series for k in GAMING_SERIES):
        return "gaming", "系列属于游戏本(%s)" % series
    # 屏幕 16 寸以上 + 独显，倾向游戏本；但小新/轻薄系不适用
    if gpu_kind == "独显" and isinstance(inch, (int, float)) and inch >= 16:
        return "gaming", "16寸以上+独显，推断为游戏本"
    return "thin", "其余一律按轻薄本（笔记本主流）"


# ---- 数值列的污染清洗 ------------------------------------------------
# 实测 2026-11-11：淘宝这份表的「内存（GB）」列被屏幕尺寸和其他数字污染了。
#   序号56 ThinkPad E14  内存=14  屏幕=14.0   ← 内存列填的是屏幕尺寸
#   序号129 游匣G15      内存=15  屏幕=空     ← 同上
#   序号58 ThinkPad E14/E16 内存=2          ← 疑似"2代"
#   序号75 小新Air14     内存=12           ← 无法解释
# 笔记本内存实际只有 8/16/32/64。按项目红线：**宁可标缺失，也不能用错值。**
VALID_MEM = (8, 16, 32, 64, 128)
VALID_DISK = (128, 256, 512, 1024, 2048, 4096)


def clean_int(v, valid, label):
    """返回 (值 或 None, 是否被丢弃, 原因)。"""
    if pd.isna(v):
        return None, False, ""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None, True, "%s 是非数值文本「%s」" % (label, str(v)[:20])
    if n not in valid:
        return None, True, "%s=%d 不在合理范围 %s，疑似串列" % (label, n, valid)
    return n, False, ""


def num_or_none(v):
    """屏幕/刷新率用：转 float，失败给 None。"""
    try:
        if pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
KEEP_QUOTA = ("到手价", "净价")
DROP_QUOTA = ("券后价/优惠后", "卡片价")


def price_of(row):
    """返回 (价格, 是否可信, 原因)。"""
    p = row.get("价格（元）")
    q = row.get("价格口径")
    if pd.isna(p):
        return None, False, "价格为空"
    if str(q) in DROP_QUOTA:
        return float(p), False, "口径是「%s」，含未兑现优惠或引流价" % q
    if str(q) in KEEP_QUOTA:
        return float(p), True, ""
    return float(p), False, "口径缺失或未知(%s)" % q


def to_offer(row, idx):
    cat, cat_why = guess_cat(row)
    price, ok, why = price_of(row)

    mem, mem_bad, mem_why = clean_int(row.get("内存（GB）"), VALID_MEM, "内存")
    disk, disk_bad, disk_why = clean_int(row.get("硬盘（GB）"), VALID_DISK, "硬盘")
    dirty = [w for w in (mem_why, disk_why) if w]

    spec_parts = []
    if pd.notna(row.get("CPU")):
        spec_parts.append(str(row["CPU"]))
    if mem:
        spec_parts.append("%dG" % mem)
    if disk:
        spec_parts.append(("%dT" % (disk // 1024)) if disk >= 1024 else ("%dG" % disk))

    note = str(row.get("备注") or "")
    sales = row.get("销量线索")

    return {
        "id": "tb-%03d" % idx,
        "name": str(row.get("型号提示") or "").strip(),
        "cat": cat,
        "spec": " · ".join(spec_parts) if spec_parts else "",
        "price": price,
        "brand": str(row.get("品牌") or "").strip(),
        "series": str(row.get("系列") or "").strip(),
        "tag": sales if pd.notna(sales) else "",
        "caution": note[:80] if note else "",
        "sources": [{"title": "淘宝采集 2026-11-08", "date": "2026-11-08"}],
        "pros": [],
        "cons": [],
        "chip": str(row.get("CPU") or "") if pd.notna(row.get("CPU")) else "",
        "gpu": str(row.get("GPU") or "") if pd.notna(row.get("GPU")) else "",
        "gpuKind": str(row.get("显卡类型") or "") if pd.notna(row.get("显卡类型")) else "",
        "ram": mem,
        "disk": disk,
        "screenInch": num_or_none(row.get("屏幕（英寸）")),
        "refreshHz": num_or_none(row.get("刷新率（Hz）")),
        "resRaw": str(row.get("分辨率") or "") if pd.notna(row.get("分辨率")) else "",
        "goodsId": str(int(row["商品ID"])) if pd.notna(row.get("商品ID")) else None,
        "note": note,
        # ---- 溯源标记：让下游知道这个字段不是原始数据 ----
        "catSrc": "推断",
        "catWhy": cat_why,
        "priceOk": ok,
        "priceWhy": why,
        "dirty": dirty,
        "origin": "taobao",
    }


def main():
    df = pd.read_excel(SRC, sheet_name="全部记录")
    offers = [to_offer(r, i + 1) for i, (_, r) in enumerate(df.iterrows())]

    n = len(offers)
    kept = [o for o in offers if o["priceOk"]]
    dropped = [o for o in offers if not o["priceOk"]]

    from collections import Counter
    print("=" * 60)
    print("总行数            : %d" % n)
    print("价格可信（可直接用）: %d" % len(kept))
    print("价格需慎用         : %d" % len(dropped))
    print()
    print("品类推断分布:", dict(Counter(o["cat"] for o in offers)))
    print("价格口径剔除原因:", dict(Counter(o["priceWhy"] for o in dropped if o["priceWhy"])))
    print()
    print("--- 推断依据抽样（可人工复核）---")
    seen = set()
    for o in offers:
        key = (o["cat"], o["catWhy"][:12])
        if key in seen:
            continue
        seen.add(key)
        print("  [%s] %-28s ← %s" % (o["cat"], o["name"][:26], o["catWhy"]))
    print()
    print("--- 价格存疑的条目（前 8 条）---")
    for o in dropped[:8]:
        print("  ¥%-8s %-30s %s" % (o["price"], o["name"][:28], o["priceWhy"]))

    dirty = [o for o in offers if o["dirty"]]
    print()
    print("--- 被清洗掉的脏字段（%d 条）---" % len(dirty))
    for o in dirty:
        print("  %-8s %-26s %s" % (o["id"], o["name"][:24], "；".join(o["dirty"])))

    if "--write" in sys.argv:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        payload = {
            "meta": {
                "source": "taobao",
                "collectedAt": "2026-11-08",
                "builtAt": "2026-11-11",
                "rawFile": SRC,
                "totalRows": n,
                "priceOkRows": len(kept),
                "catInferred": True,
                "note": "品类为规则推断，非原始字段。价格口径已过滤。"
                        "id 统一加 tb- 前缀与京东数据物理隔离。",
            },
            "offers": offers,
        }
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("\n已写出 %s（%d 条）" % (OUT, n))


if __name__ == "__main__":
    main()