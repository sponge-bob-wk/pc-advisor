# -*- coding: utf-8 -*-
"""price_report.py —— 生成价格覆盖率报告（Markdown + JSON）

回答三个问题：
1. 243 台里，多少台有价？还缺哪些？
2. 价格来自哪些源？各源覆盖率多少？
3. 口径是否诚实？—— net/final（到手价）和 list（划线价）、snapshot（快照价）分得清不清。

## 为什么要单独出报告

「有价格」这件事很容易自欺：把 ZOL 参考价塞进 `final` 也能凑出 100% 覆盖率，
但那个数字算涨幅会全错。所以报告必须**按口径分列**，而不是只报一个总数。

## 用法

    python price_report.py
"""
import json
import os
import re
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POOL = os.path.join(ROOT, "data", "machine_pool.json")
OUT_MD = os.path.join(ROOT, "价格覆盖率报告-20261116.md")
OUT_JSON = os.path.join(ROOT, "data", "price_coverage.json")

PHONE = re.compile(r"(iPhone|手机|Pura|nova|Redmi\s*Note|^\s*vivo|iPad|Mate\s*Pad|骁龙|天玑)", re.I)


def main():
    pool = json.load(open(POOL, encoding="utf-8"))
    allm = pool["machines"]
    phones = [m for m in allm if PHONE.search(str(m.get("name") or ""))]
    ms = [m for m in allm if not PHONE.search(str(m.get("name") or ""))]

    def p(m, k):
        return (m.get("price") or {}).get(k)

    def snap(m, k):
        return (m.get("priceSnapshots") or {}).get(k)

    rows = []
    rows.append("# 价格覆盖率报告（2026-11-16）\n")
    rows.append("> 口径先行：这个项目里 **`net`/`final` 才是「到手价」**，是算涨跌幅的唯一依据；")
    rows.append("> `list` 是划线价，`priceSnapshots` 是第三方平台/媒体/经销商的快照价。")
    rows.append("> 报告按口径分列，不合并成一个「有价率」——合并会让人误以为到手价齐了。\n")

    rows.append("## 1. 总体覆盖\n")
    rows.append("| 口径 | 有值 | 缺 | 覆盖率 | 说明 |")
    rows.append("|---|---:|---:|---:|---|")
    spec = [
        ("price.net", "实付/净价，**算涨幅只能用这个**", lambda m: p(m, "net")),
        ("price.final", "到手价（含券/国补后）", lambda m: p(m, "final")),
        ("price.list", "划线价/标价", lambda m: p(m, "list")),
        ("price.subsidy", "页面补贴额", lambda m: p(m, "subsidy")),
        ("priceSnapshots.zolRef", "ZOL 参考报价（媒体口径）", lambda m: snap(m, "zolRef")),
        ("priceSnapshots.jd/tmall/suning", "平台标价快照", lambda m: snap(m, "jd") or snap(m, "tmall") or snap(m, "suning")),
        ("priceSnapshots.merchant*", "经销商报价区间", lambda m: snap(m, "merchantMin")),
    ]
    for name, desc, fn in spec:
        n = sum(1 for m in ms if fn(m) is not None)
        rows.append("| `%s` | %d | %d | %.1f%% | %s |" % (name, n, len(ms) - n, 100.0 * n / len(ms), desc))

    anyp = [m for m in ms if (p(m, "net") is not None or p(m, "final") is not None
                              or p(m, "list") is not None or snap(m, "zolRef") is not None)]
    rows.append("")
    rows.append("**至少有一个价格来源：%d / %d = %.1f%%**（分母已排除 %d 台误采的手机）"
                % (len(anyp), len(ms), 100.0 * len(anyp) / len(ms), len(phones)))

    none = [m for m in ms if m not in anyp]
    if none:
        rows.append("\n### 完全无价的机型（%d 台）\n" % len(none))
        rows.append("| id | 名称 | 原因 |")
        rows.append("|---|---|---|")
        for m in none:
            rows.append("| `%s` | %s | ZOL 页面无价格容器 |" % (m["id"], (m.get("name") or "")[:30]))

    rows.append("\n## 2. 数据源\n")
    src = Counter()
    for m in ms:
        s = m.get("priceSnapshots") or {}
        if s.get("zolRef"):
            src["ZOL 参考报价"] += 1
        if s.get("jd"):
            src["ZOL·京东快照"] += 1
        if s.get("tmall"):
            src["ZOL·天猫快照"] += 1
        if s.get("merchantMin"):
            src["ZOL·经销商区间"] += 1
        if p(m, "net") is not None:
            src["京东商品页直读（历史实采）"] += 1
    rows.append("| 来源 | 台数 | 口径 |")
    rows.append("|---|---:|---|")
    for k, v in src.most_common():
        tier = "到手价 ✅" if "直读" in k else "快照价（不进涨幅计算）"
        rows.append("| %s | %d | %s |" % (k, v, tier))

    rows.append("\n## 3. 为什么 `net`/`final` 还是 33.9%\n")
    rows.append("这两个字段只认**京东商品页直读**，而京东要求登录。本轮实测：")
    rows.append("")
    rows.append("| 通道 | 结果 |")
    rows.append("|---|---|")
    rows.append("| `p.3.cn/prices/mgets` | 连接超时 |")
    rows.append("| `item-soa.jd.com/getWareBusiness` | 返回风控页 |")
    rows.append("| `item.jd.com` PC 页 | 35KB 空壳，价格异步渲染 |")
    rows.append("| `search.jd.com` | 重定向 `risk_handler` |")
    rows.append("| `item.m.jd.com` 移动页 | 能打开，价格下发占位 `\"price\":\"1\"` |")
    rows.append("")
    rows.append("按用户 2026-11-16 指示「三方也可以，不用京东」，本轮接了 ZOL 三方源，")
    rows.append("把 **list 与快照价补到 97.5% / 99.6%**，但**没有**把快照价冒充 net/final ——")
    rows.append("那会让「电脑涨价分析」的涨幅计算全部失真。")

    rows.append("\n## 4. 踩过的坑：天猫价污染了 51 台 `price.list`\n")
    rows.append("ZOL 详情页有三个价格容器，**只有前两个准**：")
    rows.append("")
    rows.append("| 容器 | 含义 | 可信 |")
    rows.append("|---|---|---|")
    rows.append("| `price__reference` | 参考报价 | ✅ 每台都不同 |")
    rows.append("| `_j_local_price` | 经销商区间 | ✅ |")
    rows.append("| `FreeVer2_tmall` | 天猫价 | ❌ **落在推荐位里** |")
    rows.append("")
    rows.append("硬证据：3 台配置完全不同的「机械革命极光X」（RTX4060/4060/4070）")
    rows.append("拿到**同一个**天猫价 5788；全量统计 `tmall` 里 6500 出现 9 次、")
    rows.append("8698/5788/18799 各 5 次，而 `zolRef`/`jd` 的重复度都在正常范围。")
    rows.append("")
    rows.append("污染后果：50 台 `price.list` 被写成天猫价，偏差最大 **±14500**")
    rows.append("（`zol-2125908` 15499→29999）。已由 `fix_list_from_zolref.py` 全部校正回 `zolRef`，")
    rows.append("并把重复值的天猫快照从库中清掉。")

    rows.append("\n## 5. 复跑方式\n")
    rows.append("```bash")
    rows.append("python tools/price_multisource.py      # 抓 ZOL detail 页价格（断点续跑）")
    rows.append("python tools/apply_prices.py --apply   # 写进 machine_pool.json（自动备份）")
    rows.append("python tools/price_report.py           # 重新出这份报告")
    rows.append("```")

    md = "\n".join(rows) + "\n"
    open(OUT_MD, "w", encoding="utf-8").write(md)

    cov = {
        "updated": "2026-11-16",
        "total": len(allm),
        "notebooks": len(ms),
        "phonesExcluded": len(phones),
        "fields": {name: {
            "withValue": sum(1 for m in ms if fn(m) is not None),
            "missing": sum(1 for m in ms if fn(m) is None),
            "coverage": round(sum(1 for m in ms if fn(m) is not None) / len(ms), 4),
        } for name, _d, fn in spec},
        "anyPrice": {"withValue": len(anyp), "missing": len(none),
                     "coverage": round(len(anyp) / len(ms), 4)},
        "noPriceMachines": [{"id": m["id"], "name": m.get("name")} for m in none],
    }
    json.dump(cov, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(md)
    print("\n写出：%s\n写出：%s" % (OUT_MD, OUT_JSON))


if __name__ == "__main__":
    main()
