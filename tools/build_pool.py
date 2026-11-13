#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_pool.py —— 把三份分散的数据合并成一个统一的「机型池」

为什么必须先做这一步：

  现在项目里同一台机器散在三个地方，各自有一半信息：
    data/specs_verified.json  29 台  规格齐全（交叉核实过），但大部分没有价格
    data/crawl/*.jsonl        64 台  有价格（京东实采），但配置是新旧两代格式混着
    data/prices.json          34 台  涨价分析的老样本（含手机、DIY 整机），与上面不是同一批

  结果是：任何一个工具单独看其中一份，都会得出"没有一台能推荐"的假象。
  真正能推荐的那台机器，规格在 A 文件、价格在 B 文件、能效在 C 文件。

合并规则（宁缺勿错，这是本项目的底线）：

  1. 以 specs_verified.json 为主干 —— 它的每个字段都有 sources 与 evidenceTier，
     是可追溯的。crawl 只是"平台页面抄下来的数字"，没核实过。
  2. 用 modelHint 精确相等来关联价格，不做模糊匹配。
     踩过的坑：`小新 Pro 14` 里的 "Pro" 会命中所有 `小新 Pro 27`，12/29 全是假命中。
  3. crawl 里 config 是字符串的旧格式记录，只取价格，不取配置。
     取配置会把 "核显" 当成显卡型号写进结构化字段，后面所有判断都跟着错。
  4. 价格四字段分开存（list / discounts / net / subsidy / final），
     不合并成单一"价格" —— 算涨幅只能用 net，国补金额要基于 net 而不是 list。

输出 data/machine_pool.json，每台机器带 `dataFrom` 说明每个字段来自哪。
"""
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# 价格字段顺序固定，别处引用时不会错位
PRICE_KEYS = ("list", "discounts", "net", "subsidy", "final")


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def load_crawl():
    """读全部采集记录，按 SKU 去重，保留「更新 / 更全」的一条。

    pending / backup 文件不读 —— 它们是未完成的草稿，混进来会得到半台机器。

    去重要按两个维度判断，只按时间会出错（真实踩过）：
    同一台机器在 2026-11-07_jd.jsonl 与 2026-11-07_jd_v2.jsonl 里各有一条，
    capturedAt 完全相同，但旧文件那条没有 platformSkuId。若只比时间并用 >=
    保留先读到的，就会留下字段残缺的旧版，导致后面用 SKU 去重时对不上。
    所以：时间新的优先；时间相同时，字段多的优先。
    """
    fields = ("platformSkuId", "energyModel", "certModel", "rawTitle", "shopTypeEvidence")
    rows, dropped = [], {"placeholder": 0, "dup": 0}
    seen = {}

    def richness(o):
        return sum(1 for k in fields if o.get(k))

    for f in sorted(glob.glob(str(DATA / "crawl" / "2026-*.jsonl"))):
        if ".bak" in f or "pending" in f or "v1_backup" in f:
            continue
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            # 空壳记录：只有 id 没有名字，是 specs 派生时留下的占位符
            if not o.get("modelHint") and not o.get("config"):
                dropped["placeholder"] += 1
                continue
            # 去重键必须是「这台机器是谁」，不能用采集记录 id。
            # 真实案例：同一台 Y7000P 被采了两次 —— 11-07 那条没有 platformSkuId、
            # 11-08 那条有，两条 id 不同、价格相同。用 id 当键会当成两台机器，
            # 推荐列表里就会重复出现。用「型号 + 实付价」当键，两次采集才能并成一条。
            pr = o.get("price") or {}
            paid = pr.get("net") or pr.get("final")
            key = "m:" + str(o.get("modelHint")) + "|" + str(paid)
            prev = seen.get(key)
            if prev is None:
                seen[key] = o
                continue
            dropped["dup"] += 1
            pt, ct = prev.get("capturedAt") or "", o.get("capturedAt") or ""
            if ct > pt or (ct == pt and richness(o) > richness(prev)):
                seen[key] = o
    rows = list(seen.values())
    return rows, dropped


def digest_struct(cfg):
    """判断 crawl 的 config 是不是结构化格式。

    31 条旧记录的 config.cpu 是字符串 "R7-H255"，不是 dict。
    这种记录只能提供价格，不能提供配置 —— 拿它当配置用，
    后面会写出 `gpu.type = "RTX5060（115W）"` 这种既不是型号也不是类型的东西。
    """
    return all(isinstance(cfg.get(k), dict) for k in ("cpu", "ram", "gpu", "screen"))


def price_from(rec):
    """从采集记录里取四段式价格。返回 (price_dict, capturedAt) 或 (None, None)"""
    p = rec.get("price")
    if not isinstance(p, dict):
        return None, None
    net = p.get("net")
    if net is None and p.get("final") is None:
        return None, None
    out = {
        "list": p.get("list"),
        "discounts": p.get("discounts") or [],
        "net": p.get("net"),
        "subsidy": p.get("subsidy"),
        "final": p.get("final"),
    }
    out["basisNote"] = p.get("basisNote")
    return out, rec.get("capturedAt")


def subsidy_of(net, rules):
    """按国补规则算该补多少。电脑走家电线：15%、封顶 1500、无门槛。

    这里只做计算，不做"是否够条件"的判断 —— 那要看能效等级（efficiency == 1），
    是 recommend 阶段的活。此处返回理论上限，供对比"页面写的补贴"是否可信。
    """
    if not net:
        return None
    r = rules.get("pc") or {}
    if r.get("threshold") and net > r["threshold"]:
        return 0
    amt = net * (r.get("rate") or 0) / 100.0
    if r.get("cap"):
        amt = min(amt, r["cap"])
    return round(amt, 2)


# 这些字段的冲突意味着「不是同一台机器」，不能挂价格
# cpu.model / gpu.model / storageGB 变了就是另一个 SKU，规格和价格不能拼
IDENTITY_FIELDS = ("cpu.model", "gpu.model", "cpu.cores", "storageGB")


def unattached_reason(spec):
    """这台规格为什么不能挂价格。返回原因字符串，或 None 表示可以挂。

    踩过的坑（真实发生过）：暗影精灵 11 在 specs 里记的是 R9-8945HX/32G/2T，
    而京东那个 SKU 是 Ultra7-251HX/16G/1T —— 同系列的两条产品线。
    如果只按 modelHint 相等挂价格，就会拼出「R9 的规格 + Ultra7 的价格」这台
    根本不存在的机器，而且看起来毫无破绽。

    但不是所有冲突都这么严重，必须区分：
      · cpu.model / gpu.model 冲突 → 两个不同的 SKU，绝对不能挂价格
      · 屏幕色域 / 面板 / 内存形态冲突 → 同一台机器的标注不一致，
        价格照挂，只是判断结论时要说明这一项不确定
    早期版本把所有 adopted=null 都拦下来，结果 4 台里 3 台被误伤 ——
    小新 Pro 14 只是色域标注不同，它并没有变成另一台机器。
    """
    for c in (spec.get("conflicts") or []):
        if c.get("adopted") is None and c.get("field") in IDENTITY_FIELDS:
            vals = " vs ".join(str(v.get("v")) for v in c.get("values") or [])
            return f"{c.get('field')} 存在未裁决冲突（{vals}）"
    return None


def soft_conflicts(spec):
    """不影响挂价格、但要在推荐时提示的冲突字段。"""
    out = []
    for c in (spec.get("conflicts") or []):
        if c.get("adopted") is None and c.get("field") not in IDENTITY_FIELDS:
            out.append(c.get("field"))
    return out



def as_level(v):
    """把能效等级统一成 1/2/3 的整数。

    踩过的坑：早期采集把等级写成字符串"一级"，而这里只认 int，
    于是 isinstance 判断把 9 条记录的能效整条丢掉 —— 池里
    「有能效等级」只剩 8 台，其余全显示"能效未查、国补不保证"。
    数据明明采到了，只是格式不同。
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v in (1, 2, 3) else None
    if isinstance(v, str):
        s = v.strip().replace("级", "").strip()
        m = {"一": 1, "二": 2, "三": 3, "1": 1, "2": 2, "3": 3}
        return m.get(s)
    return None

def main():
    specs = load("specs_verified.json")["records"]
    prices_doc = load("prices.json")
    rules = prices_doc.get("subsidyRules") or {}
    crawl_rows, dropped = load_crawl()

    # 采集记录按 modelHint 建索引。同名多条时取 capturedAt 最新（已在 load_crawl 去重）
    by_hint = {}
    for r in crawl_rows:
        h = (r.get("modelHint") or "").strip()
        if h:
            by_hint.setdefault(h, []).append(r)

    pool = []
    stat = {"withPrice": 0, "withoutPrice": 0, "structCfg": 0, "blocked": 0, "dupMerged": 0}

    for s in specs:
        m = {
            "id": s["id"],
            "name": s["modelHint"],
            "brand": s.get("brand"),
            "cat": s.get("cat"),
            "cpu": s.get("cpu"),
            "ram": s.get("ram"),
            "storageGB": s.get("storageGB"),
            "gpu": s.get("gpu"),
            "screen": s.get("screen"),
            "batteryWh": s.get("batteryWh"),
            "weightKg": s.get("weightKg"),
            "ports": s.get("ports"),
            "efficiency": s.get("efficiency"),
            # 认证型号/能效网型号是"这台机器是谁"的锚点，能效证据都挂在这上面
            "certModel": s.get("certModel"),
            "energyModel": s.get("energyModel"),
            "efficiencyStatus": s.get("efficiencyStatus"),
            "throttle": s.get("throttle"),  # 目前 0/29 台有值，字段留着否则代码要写特例
            "confidence": s.get("confidence"),
            "dataFrom": {"spec": "specs_verified.json"},
        }
        # 软冲突：同一台机器在几处来源的标注不一致，价格照挂，但结论要标注
        soft = soft_conflicts(s)
        if soft:
            m["softConflicts"] = soft

        # --- 挂价格 ---
        # 先看这台规格能不能挂价格：有未裁决冲突的不能挂，否则会拼出不存在的机器
        block = unattached_reason(s)
        hits = by_hint.get(s["modelHint"]) or []
        # 同名多条时，优先选带 platformSkuId 的 —— 只有它能稳定标识 SKU，
        # 也是后面跨记录去重用的键。缺了它，同一台机器会被算成两台。
        hits = sorted(hits, key=lambda r: (0 if r.get("platformSkuId") else 1,
                                           -(len(json.dumps(r, ensure_ascii=False)))))
        hit = next((r for r in hits if price_from(r)[0]), None) if not block else None
        if hit:
            pr, cap = price_from(hit)
            m["price"] = pr
            m["priceCapturedAt"] = cap
            m["shopType"] = hit.get("shopType")
            m["shopName"] = hit.get("shopName")
            m["url"] = hit.get("url")
            m["dataFrom"]["price"] = "crawl/" + (hit.get("id") or "")
            # 必须把 SKU id 一起带过来。同一台机器在不同日期采集会得到不同的记录 id
            # （...20261107 / ...20261108），只有 platformSkuId 是稳定的身份，
            # 否则 specs 版和采集版会被当成两台机器同时进入推荐列表。
            if hit.get("platformSkuId"):
                m["platformSkuId"] = hit["platformSkuId"]
                m["dataFrom"]["sku"] = "crawl/" + (hit.get("id") or "")
            stat["withPrice"] += 1
        else:
            m["price"] = None
            if block:
                m["priceBlockedBy"] = block
                stat["blocked"] += 1
            stat["withoutPrice"] += 1

        # --- 采集记录里有、specs 没抄到的字段，补进来但要标注 ---
        # 典型是 platformSkuId / energyModel，specs 里只有 1 台有
        if hit:
            for k_src, k_dst in (("energyModel", "energyModel"), ("platformSkuId", "platformSkuId"),
                                 ("certModel", "certModel"), ("energyModel", "energyModel")):
                if hit.get(k_src) and not m.get(k_dst):
                    m[k_dst] = hit[k_src]
                    m["dataFrom"][k_dst] = "crawl/" + (hit.get("id") or "")

            # 规格字段的反向补齐：specs 里解析不全（常见于 spec-parsed-jd-* 这批，
            # 是从京东详情页抓的，抓漏了内存形态与接口），而采集记录里其实有。
            # 真实踩过的坑：18 台机器 ram.form 是 unknown、ports 是空，
            # 覆盖率报「编程组缺 ramType 19 台 / 电气组缺 ports 18 台」，
            # 看起来像要去采集 18 台，实际数据早就在本地 jsonl 里躺着。
            # 补的原则：只填空，不覆盖 —— specs 明确写了就信 specs。
            cfg = hit.get("config")
            if isinstance(cfg, dict) and digest_struct(cfg):
                cr = cfg.get("ram") if isinstance(cfg.get("ram"), dict) else {}
                mr = m.get("ram") if isinstance(m.get("ram"), dict) else {}
                if isinstance(m.get("ram"), dict):
                    filled = []
                    # form: specs 的 "unknown" 等于没填
                    if (mr.get("form") in (None, "unknown")) and cr.get("form"):
                        mr["form"] = cr["form"]
                        filled.append("ram.form")
                    if mr.get("slots") is None and cr.get("slots") is not None:
                        mr["slots"] = cr["slots"]
                        filled.append("ram.slots")
                    if not mr.get("type") and cr.get("type"):
                        mr["type"] = cr["type"]
                        filled.append("ram.type")
                    if mr.get("gb") is None and cr.get("gb") is not None:
                        mr["gb"] = cr["gb"]
                        filled.append("ram.gb")
                    if mr.get("maxGB") is None and cr.get("maxGB") is not None:
                        mr["maxGB"] = cr["maxGB"]
                        filled.append("ram.maxGB")
                    if filled:
                        m["ram"] = mr
                        m.setdefault("backfilled", []).extend(filled)
                if not m.get("ports") and cfg.get("ports"):
                    m["ports"] = cfg["ports"]
                    m.setdefault("backfilled", []).append("ports")
                if m.get("storageGB") is None and cfg.get("storageGB") is not None:
                    m["storageGB"] = cfg["storageGB"]
                    m.setdefault("backfilled", []).append("storageGB")
                if m.get("batteryWh") is None and cfg.get("batteryWh") is not None:
                    m["batteryWh"] = cfg["batteryWh"]
                    m.setdefault("backfilled", []).append("batteryWh")
                if m.get("weightKg") is None and cfg.get("weightKg") is not None:
                    m["weightKg"] = cfg["weightKg"]
                    m.setdefault("backfilled", []).append("weightKg")
                # 显存 / TGP 常常只有采集记录里有（specs 只抄了型号）
                cg = cfg.get("gpu") if isinstance(cfg.get("gpu"), dict) else {}
                mg = m.get("gpu") if isinstance(m.get("gpu"), dict) else {}
                if isinstance(m.get("gpu"), dict):
                    gf = []
                    for k in ("vramGB", "tgpBaseW", "tgpMaxW", "type"):
                        if mg.get(k) is None and cg.get(k) is not None:
                            mg[k] = cg[k]
                            gf.append("gpu." + k)
                    if gf:
                        m["gpu"] = mg
                        m.setdefault("backfilled", []).extend(gf)
                cs = cfg.get("screen") if isinstance(cfg.get("screen"), dict) else {}
                ms = m.get("screen") if isinstance(m.get("screen"), dict) else {}
                if isinstance(m.get("screen"), dict):
                    sf = []
                    for k in ("colorGamut", "brightnessNit", "panel", "sizeInch",
                              "refreshHz", "widthPx", "heightPx"):
                        if ms.get(k) is None and cs.get(k) is not None:
                            ms[k] = cs[k]
                            sf.append("screen." + k)
                    if sf:
                        m["screen"] = ms
                        m.setdefault("backfilled", []).extend(sf)
                if m.get("efficiency") is None and as_level(cfg.get("efficiency")) is not None:
                    m["efficiency"] = as_level(cfg.get("efficiency"))
                    m.setdefault("backfilled", []).append("efficiency")
                if m.get("backfilled"):
                    m["dataFrom"]["backfill"] = "crawl/" + (hit.get("id") or "") + "（只填空，不覆盖 specs 已有值）"

        pool.append(m)

    # --- specs 没覆盖、但采集里有结构化配置的机型 ---
    # 这些机器规格没经过交叉核实，confidence 标 "unverified"，单独计数，
    # 目的是让工具能回答"能买什么"，同时在输出里明确区分可信度。
    # 真实踩过的坑（2026-11-09）：早先要求"必须有价格才入池"，结果当天新采的
    # 华硕天选6 32G/1T 因为价格未核实（宁缺勿错留空）被整台漏掉 ——
    # 而它恰恰是视频/三维组最缺的中端独显机。无价格照样能回答"够不够用"，
    # 只是进不了预算比较，与 specs 分支的行为应保持一致。
    spec_names = {s["modelHint"] for s in specs}
    extra = []
    for r in crawl_rows:
        h = (r.get("modelHint") or "").strip()
        if not h or h in spec_names:
            continue
        cfg = r.get("config")
        if not isinstance(cfg, dict) or not digest_struct(cfg):
            continue
        pr, cap = price_from(r)
        if not pr:
            # 无价格也入池：price 置 None，basisNote 里的待核事项跟过去
            pr = None
        m = {
            "id": r.get("id"),
            "name": h,
            "brand": r.get("brand"),
            "cat": guess_cat(cfg, h),
            "cpu": cfg.get("cpu"),
            "ram": cfg.get("ram"),
            "storageGB": cfg.get("storageGB"),
            "gpu": cfg.get("gpu"),
            "screen": cfg.get("screen"),
            "batteryWh": cfg.get("batteryWh"),
            "weightKg": cfg.get("weightKg"),
            "ports": cfg.get("ports"),
            "efficiency": as_level(cfg.get("efficiency")),
            # 平台：Windows 机器占绝大多数，默认 windows；Mac 必须显式标 macos，
            # 否则"装不了软件"这类硬伤会被当成"性能够不够"来判
            "devicePlatform": r.get("devicePlatform") or "windows",
            "fanless": bool(r.get("fanless")),
            "eduPrice": r.get("eduPrice"),
            "throttle": r.get("throttle"),
            "efficiencyStatus": cfg.get("efficiencyStatus"),
            "confidence": "unverified",
            "price": pr,
            "priceCapturedAt": cap,
            "shopType": r.get("shopType"),
            "shopName": r.get("shopName"),
            "url": r.get("url"),
            "energyModel": r.get("energyModel"),
            "platformSkuId": r.get("platformSkuId"),
            "dataFrom": {"spec": "crawl/" + str(r.get("id")) + "（结构化配置，未经交叉核实）",
                         "price": "crawl/" + str(r.get("id"))},
        }
        extra.append(m)
        stat["structCfg"] += 1
        if pr:
            stat["withPrice"] += 1
        else:
            stat["withoutPrice"] += 1

    pool.extend(extra)

    # --- 去重 ---
    # 同一个 SKU 可能同时以「specs 记录」和「采集记录」两种身份入池：
    #   spec-legion-y7000p-2026        （规格经过核实）
    #   jd-lenovo-legion-y7000p-...    （采集原样，未核实）
    # 两条配置与价格完全一样，不去重会让推荐列表里同一台机器出现两次。
    # 保留可信度高的那条（specs 有 sources / evidenceTier；unverified 只有平台页面）。
    RANK = {"high": 3, "medium": 2, "low": 1, "unverified": 0}
    deduped, seen_keys = [], {}
    for m in pool:
        # 价格来源 id 就是 SKU 身份：specs 记录的 dataFrom.price 指向采集记录 id
        key = m.get("platformSkuId") or (m.get("dataFrom", {}).get("price") or "").replace("crawl/", "")
        key = key or m["id"]
        rank = RANK.get(m.get("confidence"), 0)
        if key in seen_keys:
            old = seen_keys[key]
            if rank > RANK.get(old.get("confidence"), 0):
                deduped[deduped.index(old)] = m
                seen_keys[key] = m
                stat["dupMerged"] += 1
            else:
                stat["dupMerged"] += 1
            continue
        seen_keys[key] = m
        deduped.append(m)
    removed = len(pool) - len(deduped)
    pool = deduped

    # --- 统一补充推算字段 ---
    for m in pool:
        net = (m.get("price") or {}).get("net")
        m["subsidyTheoretical"] = subsidy_of(net, rules)
        m["subsidyEligible"] = (m.get("efficiency") == 1)  # 家电线要求 1 级能效

    doc = {
        "meta": {
            "version": "0.1",
            "updated": "2026-11-09",
            "purpose": "统一机型池：把规格（可追溯）与价格（平台实采）合成一条记录，供推荐工具使用",
            "joinKey": "modelHint 精确相等，不做模糊匹配",
            "why": "踩过的坑：`小新 Pro 14` 的 'Pro' 会命中所有 `小新 Pro 27`，模糊匹配 12/29 全是假命中。宁可少连，不可错连。",
            "confidence": {
                "medium/high": "来自 specs_verified.json，每个字段有 sources 与 evidenceTier",
                "unverified": "只来自平台采集，配置是结构化但未做交叉核实",
            },
            "priceFields": "list=划线价 / discounts=可叠券 / net=实付（算涨幅只能用这个） / subsidy=页面补贴 / final=到手价",
            "subsidyNote": "subsidyTheoretical 是按 15%、封顶 1500 算的理论值；subsidyEligible 要求 efficiency==1（家电线须 1 级能效）。两者都成立才真能补。",
            "dropped": dropped,
        },
        "stats": {
            "total": len(pool),
            "fromSpecs": len(specs),
            "fromCrawlOnly": len(extra),
            "withPrice": stat["withPrice"],
            "withoutPrice": stat["withoutPrice"],
            "withEfficiency": sum(1 for m in pool if m.get("efficiency")),
            "withThrottle": sum(1 for m in pool if m.get("throttle")),
            "priceBlocked": stat["blocked"],
        },
        "machines": pool,
    }

    out = DATA / "machine_pool.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 70)
    print(f"机型池   {len(pool)} 台")
    print(f"  · 来自 specs（可追溯）      {len(specs)} 台")
    print(f"  · 仅来自采集（未核实）      {len(extra)} 台")
    print(f"  · 有价格                   {stat['withPrice']} 台")
    print(f"  · 无价格                   {stat['withoutPrice']} 台（进不了推荐，只能答「够不够用」）")
    print(f"  · 有能效等级               {doc['stats']['withEfficiency']} 台")
    print(f"  · 有持续性能数据           {doc['stats']['withThrottle']} 台  ← 0 说明视频/引擎组必然卡住")
    print(f"  · 因未裁决冲突未挂价格      {stat['blocked']} 台  ← 挂了就会拼出不存在的机器")
    print(f"  · 丢弃占位记录 {dropped['placeholder']} 条 / 重复快照 {dropped['dup']} 条")
    print("=" * 70)
    print(f"\nwrote {out}")
    return 0


def guess_cat(cfg, name):
    """判断品类。只看结构化配置，不看名字里的关键词。

    踩过的坑：`R7000P 2026` 在 specs 里被标成 thin，但它带 RTX5060 —— 名字判断会错。
    """
    g = cfg.get("gpu") or {}
    t = (g.get("type") or "").lower()
    if t == "discrete" or g.get("vramGB"):
        return "gaming"
    nm = (g.get("model") or "").lower()
    if any(k in nm for k in ("rtx", "gtx", "radeon rx")):
        return "gaming"
    return "thin"


if __name__ == "__main__":
    sys.exit(main())