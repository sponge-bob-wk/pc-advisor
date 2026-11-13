# -*- coding: utf-8 -*-
"""数据自洽性体检。

由来：2026-11-11 对抗测试发现 Redmi Book Pro 14 的 upgrade=1，
但它自己的 cons 写着「内存板载，32G 是上限」。
后果不是"文案矛盾"这么轻 —— 「不要封装内存」是硬约束，
upgrade=1 会让这台机器筛不掉，系统一边说"你不要封装我也不推封装"，
一边把它推出来。用户当时报的"前后矛盾"就是这个，我当时只改了措辞，没查数据。

规矩：数据里的字段和文本互相矛盾时，以文本里的决定性事实为准，
或者改字段、或者删文本，不许两套说法并存。
"""
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core

DATA = core.DATA

# upgrade 语义：0=封装/焊死，1=有一个槽可换，2=多槽
# 文本里出现这些词，说明是封装机
SEALED_WORDS = ["内存板载", "板载内存", "封装内存", "焊死", "内存焊", "不可升级",
                "无法升级", "32G 是上限", "是上限", "不能加内存"]
# 但要排除「不建议焊死机器」这类泛指，以及显卡虚焊
FALSE_POS = ["虚焊", "焊死内存的机器根本用不上", "别买焊死"]


def check_upgrade(d):
    bad = []
    for o in d["offers"]:
        if o.get("cat") not in core.ALLOWED_CATS:
            continue
        up = o.get("upgrade")
        cons = " ".join(o.get("cons") or [])
        hits = []
        for w in SEALED_WORDS:
            if w in cons:
                # 排除误报语境
                if any(f in cons for f in FALSE_POS if f != w):
                    continue
                hits.append(w)
        if hits and up and up > 0:
            bad.append({
                "id": o["id"], "name": o["name"], "upgrade": up,
                "cons_says": hits,
                "why": "upgrade=%s 表示可升级，但 cons 写的是封装机；"
                       "「不要封装内存」的硬约束筛不掉它" % up,
            })
    return bad


def check_price_consistency(d):
    """spec 里括号标的首销价 vs price 字段，差得离谱要报。"""
    import re
    bad = []
    for o in d["offers"]:
        if o.get("cat") not in core.ALLOWED_CATS:
            continue
        spec = o.get("spec") or ""
        m = re.search(r"首销\s*(\d{4})", spec) or re.search(r"原价\s*(\d{4})", spec)
        if not m:
            continue
        orig = int(m.group(1))
        price = o.get("price") or 0
        if price >= orig:
            bad.append({"id": o["id"], "name": o["name"],
                        "why": "到手价 %s ≥ 标注首销/原价 %s，说不通" % (price, orig)})
    return bad


def check_missing_core_fields(d):
    """排序要用的字段不能缺，缺了要说清楚影响哪个侧重。
    内存固态类目（cat=upgrade）不参与整机排序，perf 本来就该是空的，豁免。"""
    bad = []
    need = {
        "perf": "主维度分，缺了 daily/game 侧重退化",
        "upgrade": "升级性，缺了「不要封装」判断失效",
        "portable": "便携，缺了 carry 侧重失效",
        "price": "价格，缺了预算过滤失效",
        "cat": "品类，缺了根本无法排序",
    }
    EXEMPT = {"upgrade": ["perf", "portable"]}   # 内存固态没有这俩，正常
    for o in d["offers"]:
        if o.get("cat") not in core.ALLOWED_CATS:
            continue
        exempt = EXEMPT.get(o.get("cat"), [])
        miss = [k for k in need if o.get(k) is None and k not in exempt]
        if miss:
            bad.append({"id": o["id"], "name": o["name"],
                        "missing": miss,
                        "why": "；".join(need[k] for k in miss)})
    return bad


def check_spec_cons_contradiction(d):
    """spec 说 32G 但 cons 说 16G 上限，这类硬矛盾。"""
    import re
    bad = []
    for o in d["offers"]:
        if o.get("cat") not in core.ALLOWED_CATS:
            continue
        spec = o.get("spec") or ""
        cons = " ".join(o.get("cons") or [])
        m = re.search(r"(\d{1,3})\s*G\s*\+", spec)
        if not m:
            continue
        cap = int(m.group(1))
        m2 = re.search(r"(\d{1,3})\s*G\s*是上限", cons)
        if m2 and int(m2.group(1)) != cap:
            bad.append({"id": o["id"], "name": o["name"],
                        "why": "spec 是 %sG，cons 说 %sG 是上限" % (cap, m2.group(1))})
        # 封装机声称可升级
        if "是上限" in cons and (o.get("upgrade") or 0) > 0:
            pass  # 已被 check_upgrade 抓到
    return bad


# 无出处断言：库里没有对应数据支撑，却被写成了事实。
# 实测 2026-11-11「500 的轻薄本」：tag 写「最便宜的能开机方案」、pros 写
# 「这个价位唯一还能开机的正经机器」—— 库里根本没有开机/合格性检测数据，
# 这句话被模型原样说给用户，用户会当成"这机器验证过能开机"。
UNSUPPORTED = {
    "能开机": "库里没有开机/合格性检测数据，不能断言机器能开机",
    "开机方案": "库里没有开机/合格性检测数据",
    "能买到": "库里只有自家报价，没有「能不能买到」的检测，也没有全市场比价",
    "买得到": "同上",
    "地板价": "库里没有全市场比价数据，不能说是地板价",
    "正经机器": "没有合格性判定依据",
    "闭眼": "没有可靠性数据，不能劝人闭眼买",
    "放心买": "没有故障率/品控数据，不能让人放心买",
}
# 「这一千」「省这几百」这类，要求系统算出两台机器的差价。
# 库里只有榜首一台的完整价格，算差价属于模型越权写数字。
PRICE_GAP = ["这一千", "省这", "别为省", "差这几百", "多花这几百"]
# 条件句豁免：「老机器还能开机的话」是前置假设，不是断言
COND = ["的话", "如果", "若"]


def check_unsupported_claim(d):
    bad = []
    for o in d["offers"]:
        if o.get("cat") not in core.ALLOWED_CATS:
            continue
        texts = []
        for f in ("tag", "caution"):
            v = o.get(f)
            if isinstance(v, str):
                texts.append((f, v))
        for k in ("pros", "cons"):
            for v in (o.get(k) or []):
                texts.append((k, v))
        for field, v in texts:
            if any(c in v for c in COND):
                continue
            hits = [w for w in UNSUPPORTED if w in v]
            gaps = [w for w in PRICE_GAP if w in v]
            if hits:
                bad.append({"id": o["id"], "name": o["name"],
                            "why": "[%s] 无出处断言「%s」——%s：%s"
                                   % (field, "、".join(hits), UNSUPPORTED[hits[0]], v[:46])})
            elif gaps:
                bad.append({"id": o["id"], "name": o["name"],
                            "why": "[%s] 写进了差价金额「%s」，库里没有两台完整价格，"
                                   "这属于越权算数字：%s" % (field, "、".join(gaps), v[:46])})
    return bad


CHECKS = [
    ("upgrade 与 cons 矛盾", check_upgrade),
    ("到手价 ≥ 标注原价", check_price_consistency),
    ("核心字段缺失", check_missing_core_fields),
    ("容量自相矛盾", check_spec_cons_contradiction),
    ("文案里的无出处断言", check_unsupported_claim),
]


def main():
    with open(DATA, encoding="utf-8") as f:
        d = json.load(f)
    print("数据自洽性体检：%s" % DATA)
    print("在库条目 %d（已剔除手机）\n" % len(core.usable_offers(d)))
    total = 0
    for name, fn in CHECKS:
        bad = fn(d)
        total += len(bad)
        print("[%s] %s" % ("FAIL" if bad else " OK ", name))
        for b in bad:
            print("   - %-28s %s" % (b.get("name"), b.get("why")))
    print("\n" + "=" * 64)
    print("问题合计 %d 条" % total)
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())