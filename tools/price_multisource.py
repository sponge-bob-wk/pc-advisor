# -*- coding: utf-8 -*-
"""price_multisource.py —— 多源公开价格采集（不依赖京东登录）

## 为什么要有这个脚本

项目红线是「价格只认京东商品页」，但京东要求登录，抓不到。
用户 2026-11-16 明确放宽：**「价格网上爬取就行，三方也可以，不用京东，先做个完整项目就行」**。

于是在**保持口径可区分**的前提下，接第三方公开源。口径纪律不变：
`price.net` / `price.final` 依然是「到手价」语义，第三方快照价**不许冒充**。

## 三个数据源（全部实测 2026-11-16）

### 源 1：ZOL detail 页 —— 每台机器都有，最全
`https://detail.zol.com.cn/notebook/index<产品id>.shtml`

    <div class="price__reference"><strong>参考报价：</strong>
      <div class="price price-normal"><span><b class="price-sign">￥</b>
      <b class="price-type">13149</b></span></div>          ← ZOL 参考价

    <dd id="_j_local_price" ...>
      <a class="price">&yen;12869-14650</a>
      <a class="num">（共<em>12</em>家经销商）</a>            ← 商家报价区间

    FreeVer2_jd ... 京东 <span class="m-price _j_price_num">&yen;16699</span>
    FreeVer2_tmall ... 天猫 <span class="m-price _j_price_num">&yen;17999</span>

⚠️ **必须避开的陷阱**：页面上还有大量 `interest-compare` 区块，
里面是**「相关产品」其他机型**的价格（￥9449、￥7299…）。
只用上面三个精确容器，绝不做全页「￥数字」搜索 —— 那会把竞品价写成本机价。

### 源 2：ZOL 参数页 —— 电商报价（已知，覆盖率约 1/3）
`.../param.shtml` 的 `电商报价` 行，含 `param-list-b2c-jd` 锚点。
实测 163 台里只有 54 台有这个区块（其余页面确实没有）。

### 源 3：慢慢买（历史价，可选）
`manmanbuy.com` 首页可达，但商品查询接口有风控。**本轮不接**，留给后续。

## 口径映射（关键，别改）

| 抓到的 | 写进哪 | 理由 |
|---|---|---|
| ZOL「参考报价」 | `priceSnapshots.zolRef` + `price.list`（若空） | 是媒体参考价，不是到手价 |
| 京东/天猫平台价 | `priceSnapshots.jd` / `.tmall` | 平台标价快照，不含券/国补 |
| 商家报价区间 | `priceSnapshots.merchantMin/Max/Count` | 经销商报价，非自营 |

**`price.net` / `price.final` / `price.subsidy` 一律不写。**
那三个是「到手价」口径，只有京东商品页直读才够格。第三方快照写进去
会让「电脑涨价分析」的涨幅计算全部失真 —— 这是这个项目最不能犯的错。

## 用法

    python price_multisource.py --limit 5      # 试跑
    python price_multisource.py               # 全量（断点续跑）
    python price_multisource.py --apply       # 落盘进 machine_pool.json
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
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POOL = os.path.join(ROOT, "data", "machine_pool.json")
RAW_OUT = os.path.join(ROOT, "data", "zol", "prices-2026-11-16.jsonl")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# ── 精确容器正则（都要求锚点在容器内，避免命中相关产品）──────────────
# 🔴 货币符号要同时认三种写法：`￥`（全角）、`¥`（半角）、`&yen;`（HTML 实体）。
#    GBK 解码后 `&yen;` **不会**自动变成 `¥`（它只解 GBK 双字节，不解 HTML 实体），
#    所以少写一个分支就整类价格抓不到 —— 首轮 142 台只有经销商数量、没有区间价就是这么来的。
_CUR = r"(?:&yen;|&#165;|￥|¥)"
_RE_REF = re.compile(
    r'price__reference.*?price-sign[^>]*>' + _CUR + r'</b>\s*<b class="price-type">([\d,]+)</b>',
    re.S)
_RE_MER = re.compile(r'_j_local_price.*?>' + _CUR + r'\s*([\d,]+)\s*[-–~]\s*([\d,]+)', re.S)
_RE_MER_N = re.compile(r'_j_local_price.*?<em>\s*(\d+)\s*</em>\s*家', re.S)
_RE_PLAT = re.compile(
    r'FreeVer2_(jd|tmall|suning)"[^>]*>(?:(?!FreeVer2_).){0,200}?'
    r'<span class="m-price _j_price_num"[^>]*>' + _CUR + r'\s*([\d,]+)</span>', re.S)


def fetch(url, retry=2):
    last = None
    for i in range(retry + 1):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "gzip",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }), timeout=20)
            d = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                d = gzip.decompress(d)
            return d.decode("gbk", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise last


def _int(s):
    if not s:
        return None
    try:
        return int(str(s).replace(",", ""))
    except ValueError:
        return None


def parse_prices(html):
    """从 detail 页抽价格。返回 dict，取不到就是空 dict（不猜）。"""
    out = {}
    m = _RE_REF.search(html)
    if m:
        v = _int(m.group(1))
        if v:
            out["zolRef"] = v
    m = _RE_MER.search(html)
    if m:
        lo, hi = _int(m.group(1)), _int(m.group(2))
        if lo and hi:
            out["merchantMin"] = lo
            out["merchantMax"] = hi
    m = _RE_MER_N.search(html)
    if m:
        out["merchantCount"] = _int(m.group(1))
    for m in _RE_PLAT.finditer(html):
        plat, price = m.group(1), _int(m.group(2))
        if plat and price:
            out.setdefault("jd" if plat == "jd" else (
                "tmall" if plat == "tmall" else "suning"), price)
    return out


def drop_unreliable(prices, all_prices):
    """剔掉页面上「看着像本机价、其实是别人家的」价格。

    🔴 实测证据（2026-11-16，163 台）：
       `tmall` 的取值里 6500 出现 **9 次**、8698 / 5788 / 18799 各 5 次，
       而 `zolRef` 与 `jd` 的重复度都在合理范围（最多 2-3 台）。
       3 台配置完全不同的「机械革命极光X」（RTX4060/4060/4070）拿到**同一个**天猫价 5788
       —— 那显然不是本机价，是详情页侧栏「同类商品」的价格。

    ZOL 的 `FreeVer2_tmall` 锚点落在推荐位里，不可靠。
    所以：**天猫价只保留「全库唯一」的取值**（重复≥2 一律丢弃），
    `jd` 与 `zolRef` 保留（它们的重复属于正常的同价机型）。

    参数 all_prices: 全量采集结果 {id: prices}，用于统计重复度。
    """
    tmall_vals = [p["tmall"] for p in all_prices.values() if p.get("tmall")]
    from collections import Counter
    cnt = Counter(tmall_vals)
    if "tmall" in prices and cnt.get(prices["tmall"], 0) > 1:
        prices.pop("tmall")
        prices["_droppedTmall"] = "重复值，疑为推荐位价格"
    return prices


def detail_url(m):
    """从池里的 param 页 URL 推出 detail 页 URL。"""
    u = m.get("url") or ""
    mm = re.search(r"/(\d{4,8})/param\.shtml", u)
    if mm:
        pid = mm.group(1)
        return "https://detail.zol.com.cn/notebook/index%s.shtml" % pid, pid
    mm = re.search(r"/notebook/index(\d+)\.shtml", u)
    if mm:
        return u, mm.group(1)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-interval", type=float, default=0.9)
    args = ap.parse_args()

    pool = json.load(open(POOL, encoding="utf-8"))
    targets = []
    for m in pool["machines"]:
        u, pid = detail_url(m)
        if u:
            targets.append((m["id"], u, pid))
    print("可抓 ZOL detail 页的机型：%d / %d" % (len(targets), len(pool["machines"])))

    os.makedirs(os.path.dirname(RAW_OUT), exist_ok=True)
    done = set()
    if os.path.exists(RAW_OUT):
        for line in open(RAW_OUT, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    done.add(json.loads(line)["id"])
                except Exception:  # noqa: BLE001
                    pass
    todo = [t for t in targets if t[0] not in done]
    print("已抓 %d，待抓 %d" % (len(done), len(todo)))
    if args.limit:
        todo = todo[: args.limit]

    ok = nohit = fail = 0
    with open(RAW_OUT, "a", encoding="utf-8") as f:
        for i, (mid, url, pid) in enumerate(todo, 1):
            try:
                h = fetch(url)
                p = parse_prices(h)
                rec = {"id": mid, "url": url, "pid": pid,
                       "capturedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                       "prices": p}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                if p:
                    ok += 1
                    print("[%d/%d] %-16s %s" % (i, len(todo), mid,
                                                json.dumps(p, ensure_ascii=False)))
                else:
                    nohit += 1
                    print("[%d/%d] %-16s 页面无价格容器" % (i, len(todo), mid))
            except Exception as e:  # noqa: BLE001
                fail += 1
                print("[%d/%d] %-16s FAIL %s" % (i, len(todo), mid, str(e)[:60]))
            time.sleep(args.min_interval)

    print()
    print("完成：有价 %d，无价 %d，失败 %d" % (ok, nohit, fail))
    print("原始记录 → %s" % RAW_OUT)
    if args.apply:
        print("（价格写入请跑 apply_prices.py）")


if __name__ == "__main__":
    main()
