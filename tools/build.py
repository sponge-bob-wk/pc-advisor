#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 data/prices.json 注入 tools/template.html，生成单文件 HTML。

用法：
    python tools/build.py

产出：
    电脑涨价趋势.html   —— 双击就能打开，不依赖网络和任何外部文件
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "prices.json"
TPL = ROOT / "tools" / "template.html"
OUT = ROOT / "电脑涨价趋势.html"
PLACEHOLDER = "/*__DATA__*/"


def fail(msg):
    print("[build] 失败：" + msg, file=sys.stderr)
    sys.exit(1)


def check(data):
    """入库前先卡一遍，避免把坏数据打进页面。"""
    need = ["meta", "sources", "categories", "brands", "offers", "crossSell",
            "upgradeGuide", "trapsByKind", "partsChange", "categoryChange",
            "sameModel", "dramQoQ", "outlook", "budgetBands", "decision",
            "subsidyRules", "subsidyFacts", "dimensions", "weights", "defects"]
    for k in need:
        if k not in data:
            fail("prices.json 缺少字段：" + k)

    ids = {s["id"] for s in data["sources"]}
    if len(ids) != len(data["sources"]):
        fail("sources 里有重复的 id")

    # 每个品类引用的来源必须真实存在，否则页面上会出现「依据：（空）」
    catkeys = set()
    catkind = {}
    for c in data["categories"]:
        catkeys.add(c["key"])
        catkind[c["key"]] = c.get("kind") or "pc"
        for sid in c.get("sources", []):
            if sid not in ids:
                fail("品类 %s 引用了不存在的来源 %s" % (c["key"], sid))
        if not c.get("reasons"):
            fail("品类 %s 没有 reasons" % c["key"])
        if not c.get("action"):
            fail("品类 %s 没有 action" % c["key"])

    # kind 必须是已知的两类，否则模板取不到维度会静默降级
    for c in data["categories"]:
        k = c.get("kind") or "pc"
        if k not in data["dimensions"]:
            fail("品类 %s 的 kind=%s 在 dimensions 里没有定义" % (c["key"], k))
        if k not in data["weights"]:
            fail("品类 %s 的 kind=%s 在 weights 里没有定义" % (c["key"], k))

    for sid in data["outlook"].get("sources", []):
        if sid not in ids:
            fail("outlook 引用了不存在的来源 " + sid)

    # 每个数字都得能追到来源，否则就是编造
    for m in data["sameModel"]:
        calc = round((m["after"] - m["before"]) / m["before"] * 100)
        if abs(calc - m["change"]) > 2:
            fail("sameModel「%s」涨幅对不上：%d → %d 实为 %+d%%，但写的是 %+d%%"
                 % (m["name"], m["before"], m["after"], calc, m["change"]))
        if m.get("kind") and m["kind"] not in data["dimensions"]:
            fail("sameModel「%s」的 kind=%s 不认识" % (m["name"], m["kind"]))

    # ---- 排序相关 ----
    # 品牌名会跨大类重复（华为/小米/荣耀都同时做电脑和手机），
    # 所以唯一键必须是「名字 + scope」，不能用名字。
    brands = {}
    for b in data["brands"]:
        sc = b.get("scope") or "pc"
        if sc not in data["dimensions"]:
            fail("品牌「%s」的 scope=%s 不认识" % (b["name"], sc))
        if (b["name"], sc) in brands:
            fail("品牌「%s」在 %s 类里重复登记" % (b["name"], sc))
        brands[(b["name"], sc)] = b

    # 有品牌的机型才有价格可排；noOffer 的品牌只做定性说明，不参与排序
    for (bname, bsc), b in brands.items():
        has_offer = any(o["brand"] == bname and catkind[o["cat"]] == bsc for o in data["offers"])
        if not has_offer and not b.get("noOffer"):
            fail("品牌「%s（%s 类）」没有任何机型，也没标 noOffer —— 榜单里会出现空品牌" % (bname, bsc))
        if has_offer and b.get("noOffer"):
            fail("品牌「%s（%s 类）」标了 noOffer 但又有对应机型，自相矛盾" % (bname, bsc))

    oids = set()
    for o in data["offers"]:
        if o["id"] in oids:
            fail("offers 里有重复的 id：" + o["id"])
        oids.add(o["id"])
        if o["cat"] not in catkeys:
            fail("机型「%s」的品类 %s 不存在" % (o["name"], o["cat"]))
        okind = catkind[o["cat"]]
        if (o["brand"], okind) not in brands:
            fail("机型「%s」引用了没登记的牌子「%s」（%s 类）" % (o["name"], o["brand"], okind))
        for sid in o.get("sources", []):
            if sid not in ids:
                fail("机型「%s」引用了不存在的来源 %s" % (o["name"], sid))
        if o["price"] <= 0:
            fail("机型「%s」价格不合法" % o["name"])

        # 内存条 / 固态没有 CPU，「性能分」这一项对它不适用。
        # 允许 perf=null，但必须显式写 null —— 漏写字段和「确实不适用」是两回事。
        perf_na = o["cat"] == "upgrade"
        if perf_na:
            if o.get("perf") is not None:
                fail("机型「%s」属于内存/固态品类，没有 CPU，perf 必须是 null（不能编一个分数）"
                     % o["name"])
        else:
            if not isinstance(o.get("perf"), int) or not 0 <= o["perf"] <= 100:
                fail("机型「%s」的 perf 要在 0-100" % o["name"])

        # 优缺点必填：这次的核心要求就是「推荐必须把好处和坏处都说清」
        for f in ("pros", "cons"):
            if not isinstance(o.get(f), list) or not o[f]:
                fail("机型「%s」缺 %s —— 只夸不贬的推荐不算推荐" % (o["name"], f))
            for x in o[f]:
                if not str(x).strip():
                    fail("机型「%s」的 %s 里有空条目" % (o["name"], f))

        # 按品类校验各自的维度字段
        if okind == "phone":
            # battery 存的是额定容量 mAh，不是 0-100 的主观分。
            # 直接压成 0-100 会让「92 / 98」这种数字失去出处，而容量是能从官网核实的。
            if not isinstance(o.get("battery"), int) or not 3000 <= o["battery"] <= 12000:
                fail("手机「%s」的 battery 要填额定容量 mAh（3000-12000），不是 0-100 的分"
                     % o["name"])
            if not isinstance(o.get("camera"), (int, float)) or not 0 <= o["camera"] <= 100:
                fail("手机「%s」的 camera 要在 0-100" % o["name"])
            for f in ("chip", "gb6"):
                if f not in o:
                    fail("手机「%s」缺 %s —— 性能分必须能追到实测，不能凭印象" % (o["name"], f))
            if not (isinstance(o["gb6"], list) and len(o["gb6"]) == 2
                    and all(isinstance(x, int) and x > 0 for x in o["gb6"])):
                fail("手机「%s」的 gb6 要是 [单核, 多核] 两个正整数" % o["name"])
            # perf 必须能由跑分推出来，否则「分数」和「依据」会各说各的。
            # 手改 perf 而不改 gb6（或反过来）在这一步就会被拦下。
            want = round(100 * (0.5 * o["gb6"][0] / 4000.0 + 0.5 * o["gb6"][1] / 12000.0))
            if abs(want - o["perf"]) > 1:
                fail("手机「%s」的 perf 应为 %d（按 GB6 %d/%d 换算），写的是 %d"
                     % (o["name"], want, o["gb6"][0], o["gb6"][1], o["perf"]))
            if "upgrade" in o or "portable" in o:
                fail("手机「%s」不该有 upgrade/portable —— 手机内存不可换，这两项无意义" % o["name"])
        else:
            if o.get("upgrade") not in (0, 1, 2):
                fail("机型「%s」的 upgrade 只能是 0/1/2" % o["name"])
            if o.get("portable") not in (0, 1, 2, 3):
                fail("机型「%s」的 portable 只能是 0-3" % o["name"])

            # 电脑也必须能追到实测跑分。这一条和手机 GB6 是同一个道理：
            # 原来「酷睿5 320」和「R7 H255」都被拍成 28 分，而 R23 多核 7540 vs 15475
            # 差了近一倍 —— 这不是排序问题，是数据本身失真。
            if perf_na:
                continue
            for f in ("chip", "cb23"):
                if f not in o:
                    fail("电脑「%s」缺 %s —— 性能分必须能追到实测，不能凭印象" % (o["name"], f))
            if not (isinstance(o["cb23"], list) and len(o["cb23"]) == 2
                    and all(isinstance(x, int) and x > 0 for x in o["cb23"])):
                fail("电脑「%s」的 cb23 要是 [单核, 多核] 两个正整数" % o["name"])
            # R23 与 Cinebench 2024 的分数不可比：渲染引擎从 Cinema 4D 换成 Redshift，
            # 场景也换了，量级差约 15 倍（R23 多核 1e4~4e4，2024 只有 800~2000）。
            # 把 2024 的数填进 cb23，perf 会被算成个位数——而下面「perf 能由 cb23 推出」
            # 那条校验发现不了，因为它是内部自洽的。所以这里单独卡物理范围。
            _s, _m = o["cb23"][0], o["cb23"][1]
            if not (800 <= _s <= 2600):
                fail("电脑「%s」的 R23 单核 %d 超出合理范围 800-2600。"
                     "若这是 Cinebench 2024 的分数，不要填进来——两版不可比，2024 单核约为 R23 的 1/17"
                     % (o["name"], _s))
            if not (3000 <= _m <= 45000):
                fail("电脑「%s」的 R23 多核 %d 超出合理范围 3000-45000。"
                     "若来自 Cinebench 2024，量级差约 15 倍，不能混用" % (o["name"], _m))
            if _m <= _s:
                fail("电脑「%s」的多核分 %d 不该低于单核分 %d" % (o["name"], _m, _s))
            want = round(100 * (0.5 * o["cb23"][0] / 2400.0 + 0.5 * o["cb23"][1] / 40000.0))
            if abs(want - o["perf"]) > 1:
                fail("电脑「%s」的 perf 应为 %d（按 Cinebench R23 %d/%d 换算），写的是 %d"
                     % (o["name"], want, o["cb23"][0], o["cb23"][1], o["perf"]))

    # 每个品类至少要有机型，否则榜单是空的
    for k in catkeys:
        if not any(o["cat"] == k for o in data["offers"]):
            fail("品类 %s 没有任何机型，榜单会空白" % k)

    # 交叉推荐和升级指南引用的来源
    for k, v in data["crossSell"].items():
        if k not in catkeys:
            fail("crossSell 里的品类 %s 不存在" % k)
        for sid in v.get("sources", []):
            if sid not in ids:
                fail("crossSell[%s] 引用了不存在的来源 %s" % (k, sid))
    for sid in data["upgradeGuide"].get("sources", []):
        if sid not in ids:
            fail("upgradeGuide 引用了不存在的来源 " + sid)

    # 预算档必须是递增的，否则档位匹配会错
    maxs = [b["max"] for b in data["budgetBands"]]
    if maxs != sorted(maxs):
        fail("budgetBands 的 max 必须递增，当前是 " + str(maxs))
    for c in data["categories"]:
        if "budgetBands" in c:
            cm = [b["max"] for b in c["budgetBands"]]
            if cm != sorted(cm):
                fail("品类 %s 的 budgetBands max 必须递增" % c["key"])

    # ---- 权重 ----
    for kind, modes in data["weights"].items():
        dl = {d["key"] for d in data["dimensions"][kind]}
        seen_primary = set()
        for mode, cfg in modes.items():
            s = sum(cfg["w"].values())
            if abs(s - 1.0) > 1e-6:
                fail("权重 %s.%s 的和是 %.4f，必须为 1" % (kind, mode, s))
            for k in cfg["w"]:
                if k not in dl:
                    fail("权重 %s.%s 用了维度 %s，但该品类没有这个维度" % (kind, mode, k))
            if set(cfg["w"].keys()) != dl:
                fail("权重 %s.%s 没有覆盖全部维度（缺 %s）"
                     % (kind, mode, dl - set(cfg["w"].keys())))

            # primary 决定排序时谁是硬门槛。必须显式声明（可以为 null），
            # 漏写会让排序静默退回纯加权 —— 那正是用户质疑的那种「数据被污染」。
            if "primary" not in cfg:
                fail("权重 %s.%s 缺 primary 声明。综合型侧重写 null，别省略" % (kind, mode))
            p = cfg["primary"]
            if p is not None:
                if p not in dl:
                    fail("权重 %s.%s 的 primary=%s 不是该品类的维度" % (kind, mode, p))
                # 主维度自己权重必须最高，否则「主维度是门槛」和「权重表」自相矛盾
                if cfg["w"][p] < max(cfg["w"].values()) - 1e-9:
                    fail("权重 %s.%s 的 primary=%s 权重 %.2f 不是最高，名不副实"
                         % (kind, mode, p, cfg["w"][p]))
                if p in seen_primary:
                    fail("权重 %s 里 %s 被两个侧重同时当主维度，排序会分不出谁优先" % (kind, p))
                seen_primary.add(p)
            # 「省钱优先」是综合权衡，给它设主维度会退化成单指标排序
            if mode == "value" and p is not None:
                fail("权重 %s.value 是综合侧重，primary 必须为 null，当前是 %s" % (kind, p))

    # ---- 国补 ----
    for key in ("pc", "phone"):
        r = data["subsidyRules"].get(key)
        if not r:
            fail("subsidyRules 缺 " + key)
        for f in ("rate", "cap", "threshold", "saturate", "note", "bucket"):
            if f not in r:
                fail("subsidyRules.%s 缺字段 %s" % (key, f))
        # 吃满点必须能从规则推导出来，否则页面上的「吃满要实付」会和实际算的对不上
        want = min(r["cap"] / (r["rate"] / 100.0), r["threshold"] or 1e9)
        if abs(want - r["saturate"]) > 1:
            fail("subsidyRules.%s 的 saturate 应为 %.0f，写的是 %d"
                 % (key, want, r["saturate"]))
    if data["subsidyRules"]["phone"]["threshold"] is None:
        fail("手机国补必须有 6000 元门槛 —— 这是这一轮最大的单一差价")
    if data["subsidyRules"]["pc"]["threshold"] is not None:
        fail("电脑归家电类，没有价格门槛；threshold 应为 null")

    # 手机机型如果超过 6000，必须在 caution 里说明补贴全无
    for o in data["offers"]:
        if catkind[o["cat"]] == "phone" and o["price"] > data["subsidyRules"]["phone"]["threshold"]:
            if "国补" not in (o.get("caution") or "") and "补贴" not in (o.get("caution") or ""):
                fail("手机「%s」标价 %d 已超 6000，caution 里必须提醒补贴全无"
                     % (o["name"], o["price"]))

    # ---- 其他块 ----
    for kind, items in data["trapsByKind"].items():
        if kind not in data["dimensions"]:
            fail("trapsByKind 的 kind=%s 不认识" % kind)
        if not items:
            fail("trapsByKind.%s 是空的" % kind)

    for sid in data["defects"].get("sources", []):
        if sid not in ids:
            fail("defects 引用了不存在的来源 " + sid)
    if len(data["defects"].get("items", [])) < 3:
        fail("defects 至少要有 3 条，否则这一块撑不起来")


def main():
    if not DATA.exists():
        fail("找不到 " + str(DATA))
    if not TPL.exists():
        fail("找不到 " + str(TPL))

    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail("prices.json 不是合法 JSON：%s（第 %d 行）" % (e.msg, e.lineno))

    check(data)

    tpl = TPL.read_text(encoding="utf-8")
    if PLACEHOLDER not in tpl:
        fail("模板里找不到占位符 " + PLACEHOLDER)

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # 防止数据里的 </script> 提前闭合脚本块
    payload = payload.replace("</", "<\\/")

    OUT.write_text(tpl.replace(PLACEHOLDER, payload), encoding="utf-8")
    size = OUT.stat().st_size / 1024
    print("[build] 已生成 %s（%.1f KB，含 %d 条来源 / %d 个品类）"
          % (OUT.name, size, len(data["sources"]), len(data["categories"])))
    print("[build] 数据截止 %s" % data["meta"]["updated"])


if __name__ == "__main__":
    main()
