# -*- coding: utf-8 -*-
"""zol_price.py —— 从 ZOL 参数页补「电商报价 / 平台快照价」到 price 结构

## 为什么不能直接写 price.final

项目口径（AGENTS.md §4.1、`machine_pool.meta.priceFields`）：
    list=划线价 / discounts=可叠券 / net=实付（算涨幅只能用这个） / subsidy=页面补贴 / final=到手价

ZOL 给的「电商报价 ￥13198」是**平台标价快照**，不含国补、不含券、也不保证实时。
把它写进 `net` 或 `final` 会直接污染「涨价分析」这个项目的核心指标 ——
这是最坏的一类错误（数字看着像真的，口径却是另一个）。

所以本脚本只做两件事：
1. `price.list` 为空时，用 ZOL 电商报价填 `list`，并在 `basis` 写明是 ZOL 快照；
2. 把 ZOL 的京东/天猫双平台价单独存进 `priceSnapshots`（不参与判定、不参与涨价计算），
   留给以后接实时价格时做对照。

**绝不写 net / final / subsidy。** 这三个只能来自京东商品页直读。

## 用法

    python zol_price.py            # 演练
    python zol_price.py --apply    # 落盘（自动备份）
"""
import argparse
import gzip
import json
import os
import re
import shutil
import sys
import time
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POOL = os.path.join(ROOT, "data", "machine_pool.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 平台域名 → 字段名。结构：`<a id="param-list-b2c-jd" ...>京东商城</a>` 之后紧跟价格
_PLATFORM = [("param-list-b2c-jd", "jd"), ("param-list-b2c-tmall", "tmall"),
             ("param-list-b2c-suning", "suning")]


def fetch(url, retry=2):
    last = None
    for _ in range(retry + 1):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Encoding": "gzip",
                "Accept-Language": "zh-CN,zh;q=0.9"}), timeout=20)
            d = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                d = gzip.decompress(d)
            return d.decode("gbk", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2)
    raise last


def prices(html):
    """从参数页抽各平台快照价。返回 {'jd': 13198, 'tmall': 13999}"""
    out = {}
    for anchor, key in _PLATFORM:
        # 锚点之后 1200 字符内出现的第一个「￥数字」就是该平台价。
        # 限制跨度是必须的：页面下方「相关产品」区也挂着别家机器的参考价。
        i = html.find(anchor)
        if i < 0:
            continue
        seg = html[i:i + 1200]
        m = re.search(r"[￥¥]\s*([\d,]{3,7})", seg)
        if m:
            try:
                out[key] = int(m.group(1).replace(",", ""))
            except ValueError:
                pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    pool = json.load(open(POOL, encoding="utf-8"))
    targets = [m for m in pool["machines"]
               if "detail.zol.com.cn" in (m.get("url") or "")
               and "param.shtml" in (m.get("url") or "")]
    if args.limit:
        targets = targets[: args.limit]
    print("目标 %d 台（ZOL 参数页）" % len(targets))

    filled_list = snaps = failed = 0
    for i, m in enumerate(targets, 1):
        try:
            h = fetch(m["url"])
            ps = prices(h)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("[%d/%d] %-16s FAIL %s" % (i, len(targets), m["id"], str(e)[:60]))
            continue
        if not ps:
            continue

        price = m.get("price") if isinstance(m.get("price"), dict) else {}
        changed = False
        if price.get("list") is None:
            # 优先京东快照，其次天猫 —— 标 USD 一致性：都是「平台标价」口径
            v = ps.get("jd") or ps.get("tmall")
            if v:
                price["list"] = v
                price.setdefault("final", None)
                price.setdefault("net", None)
                price["basis"] = "zol快照·%s" % ("京东" if ps.get("jd") else "天猫")
                price["note"] = "ZOL 平台标价快照，不含国补/券，**不等于到手价**；net/final 仍缺"
                m["price"] = price
                changed = True
                filled_list += 1

        # 双平台快照单独存，不进 price 口径
        snap = dict(m.get("priceSnapshots") or {})
        for k, v in ps.items():
            if k not in snap:
                snap[k] = v
        if snap != (m.get("priceSnapshots") or {}):
            m["priceSnapshots"] = snap
            snap["_capturedAt"] = time.strftime("%Y-%m-%d %H:%M")
            snaps += 1
            changed = True

        if changed:
            bf = set(m.get("backfilled") or [])
            bf.add("price.list")
            m["backfilled"] = sorted(bf)
            print("[%d/%d] %-16s %s" % (i, len(targets), m["id"], json.dumps(ps, ensure_ascii=False)))
        time.sleep(0.8)

    print()
    print("price.list 补上 %d 台；平台快照记录 %d 台；失败 %d 台" % (filled_list, snaps, failed))

    if args.apply:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("已写入 %s（备份 %s）" % (POOL, bak))
    else:
        print("（演练模式，未写文件；加 --apply 落盘）")


if __name__ == "__main__":
    main()
