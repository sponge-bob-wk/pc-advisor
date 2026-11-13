# -*- coding: utf-8 -*-
"""双数据源专项测试（2026-11-11 淘宝数据入库后新增）。

测四件事：
  ① 数据隔离 —— 淘宝数据不会污染京东的默认推荐
  ② 缺字段不判 0 分 —— 没跑分的机器不参与分位数，也不能被当成"性能为零"
  ③ 渠道比价必须拒绝 —— 库里只有京东价，问别家多少不许编
  ④ meta 结构差异 —— 换数据源不能崩（dataAsOf 统一出口）

跑法：python tools/agent/test_source.py
"""
import sys
import io

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, r"E:\电脑涨价分析\tools\agent")
import core          # noqa: E402
import agent         # noqa: E402

PASS, FAIL = [], []


def ck(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK " if cond else "ERR", name,
                           ("  <- " + detail) if (detail and not cond) else ""))


# ---------------- ① 数据隔离 ----------------
print("\n=== ① 默认只读京东，淘宝不混进来 ===")
jd = core.load()
ck("load() 只返回京东条目",
   all(o.get("origin", "jd") == "jd" for o in jd["offers"]),
   str({o.get("origin") for o in jd["offers"]}))
ck("ACTIVE_SOURCE 仍是 jd", core.ACTIVE_SOURCE == "jd")

merged = core.load_all(price_ok_only=True)
bad = [o for o in merged if o.get("origin") == "taobao" and not o.get("priceOk")]
ck("load_all 不含价格不可信的淘宝条目", len(bad) == 0, "混入 %d 条" % len(bad))
ck("load_all 条数 = 京东 + 淘宝可信",
   len(merged) == len(jd["offers"]) + sum(1 for o in core.load("taobao")["offers"]
                                          if o.get("priceOk")))

# ---------------- ② 缺字段不判 0 分 ----------------
print("\n=== ② 缺跑分的机器不参与分位数、不判 0 分 ===")
ck("main_score 缺数据返回 None（不是 0.0）",
   core.main_score({"perf": None, "cat": "thin", "price": 5000}, "daily") is None)
ck("main_score 有数据时照常出分",
   core.main_score({"perf": 70, "cat": "thin", "price": 5000}, "daily") == 70.0)

# 分位数不能被无分项压塌
for cat, mode in (("gaming", "game"), ("thin", "carry")):
    a = sorted(x for x in (core.main_score(o, mode)
                           for o in jd["offers"] if o["cat"] == cat) if x is not None)
    b = sorted(x for x in (core.main_score(o, mode)
                           for o in merged if o["cat"] == cat) if x is not None)
    ck("%s/%s 分位数不受淘宝影响" % (cat, mode), a == b,
       "京东 %s vs 合并 %s" % (a[:3], b[:3]))

# 双源合并时，有据可查的机型必须排在无数据的机型前面
s = {"cat": "gaming", "budget": 9000, "purpose": ["gaming"], "years": 4, "carry": False}
r = core.build_recommendation(s, data={"meta": {}, "offers": merged})
tops = r["ranked"][:3]
ck("合并时前 3 名都是有跑分的机型",
   all(not o.get("noPerfData") for o in tops),
   str([o["name"] for o in tops]))
ck("noPerfData 标记透传到出口", all("noPerfData" in o for o in r["ranked"]))
ck("淘宝条目带 origin=taobao", any(o.get("origin") == "taobao"
                                   for o in merged))

# ---------------- ③ 渠道比价必须拒绝 ----------------
print("\n=== ③ 问别家价格必须明说没有，不许编 ===")
SRC_HIT = ["淘宝上是不是更便宜", "这个在淘宝卖多少", "淘宝和京东哪个便宜",
           "这个比淘宝贵吗", "拼多多上有更便宜的吗", "两个平台差多少钱",
           "百亿补贴有这个吗", "天猫旗舰店多少钱", "哪个平台买划算",
           "全网最低价是多少", "京东那边什么价", "两边差价多少"]
for q in SRC_HIT:
    ck("拦住「%s」" % q, bool(agent.SRC_ASK.search(q)))

SRC_MISS = ["多少钱", "贵不贵", "便宜点的", "差多少性能", "比它强多少",
            "这个价位有什么", "太贵了便宜点", "换一台", "打游戏够用吗",
            "预算能再加点吗", "买哪个配置好", "它多重"]
for q in SRC_MISS:
    ck("不误杀「%s」" % q, not agent.SRC_ASK.search(q))

# 端到端：真的走到 compare 且不出现编造
K = {"cat": "thin", "budget": 8000, "purpose": ["office"], "years": 4, "carry": True}
BAD_PHRASES = ["通常能买到", "往往比", "在淘宝到手价", "淘宝上买更划算"]
for q in ["淘宝上是不是更便宜", "这个在淘宝卖多少"]:
    res = agent.run(q, known=K, focus="thin-air14")
    txt = res.get("reply", "")
    ck("「%s」走 compare 阶段" % q, res.get("stage") == "compare",
       str(res.get("stage")))
    ck("「%s」回复无编造措辞" % q,
       not any(b in txt for b in BAD_PHRASES), txt[:60])
    ck("「%s」明确说了库里没有别家价" % q,
       ("没有别的平台" in txt) or ("只存了京东" in txt), txt[:60])

# ---------------- ④ 换源不崩 ----------------
print("\n=== ④ 两个源 meta 结构不同，dataAsOf 不能崩 ===")
jd_date = core.data_as_of(core.load("jd"))
tb_date = core.data_as_of(core.load("taobao"))
ck("京东 dataAsOf 有值", bool(jd_date) and jd_date != "未知", str(jd_date))
ck("淘宝 dataAsOf 有值（它没有 updated 字段）",
   bool(tb_date) and tb_date != "未知", str(tb_date))

old = core.ACTIVE_SOURCE
try:
    core.ACTIVE_SOURCE = "taobao"
    tb_only = dict(core.load())
    tb_only["offers"] = [o for o in tb_only["offers"] if o.get("priceOk")]
    try:
        rr = core.build_recommendation(
            {"cat": "thin", "budget": 6000, "purpose": ["office"],
             "years": 4, "carry": True}, data=tb_only)
        ck("切到淘宝源不崩", True)
        ck("淘宝源推荐带得出 dataAsOf", bool(rr.get("dataAsOf")))
        ck("淘宝源里排前面的确实无跑分（它本来就没数据）",
           all(o.get("noPerfData") for o in rr["ranked"][:2]))
    except Exception as e:                                  # noqa: BLE001
        ck("切到淘宝源不崩", False, "%s: %s" % (type(e).__name__, e))
finally:
    core.ACTIVE_SOURCE = old

# ---------------- ⑤ 扩充候选池：三道过滤的分辨力 ----------------
print("\n=== ⑤ 扩充候选池：该拦的拦住，该放的不误杀 ===")
TT = {o["id"]: o for o in core.load("taobao")["offers"]}

# 必须拦住 —— 每一条都是真实数据里踩出来的
MUST_DROP = [
    ("tb-076", "note 自述「内存 16GB 与 32GB 两档…均并列，未选定 SKU」"),
    ("tb-139", "note 自述「两处冲突：参数区型号写 Y9000P，与标题不符…SKU 挂了 5 个型号」"),
    ("tb-006", "标题写 32GB，但 ram=16 / spec=16G，标题与字段打架"),
    ("tb-122", "text 明写「二手商品」"),
    ("tb-123", "买家评价「成色不错」——「成色」是二手机专用说法"),
    ("tb-124", "保修只剩 1 个月，卖家答「展示机」"),
]
for oid, why in MUST_DROP:
    o = TT.get(oid)
    if o is None:
        ck("%s 存在于数据里" % oid, False, "找不到")
        continue
    drop = bool(core.is_used_or_demo(o)) or not core.is_single_sku(o)
    ck("拦住 %s" % oid, drop, why)

# 不能误杀 —— 构造的干净单 SKU 条目
CLEAN = [
    {"name": "MagicBook Pro 14 酷睿Ultra5", "ram": 16, "disk": 512,
     "note": "参数区已读：16GB / 512GB", "spec": "U5 · 16G · 512G"},
    {"name": "小新 Air15 2026", "ram": 16, "disk": 1024, "note": "", "spec": "R7 · 16G · 1T"},
    {"name": "暗影精灵 10", "ram": 32, "disk": 2048, "note": "", "spec": "i9 · 32G · 2T"},
]
for o in CLEAN:
    ck("放行干净条目「%s」" % o["name"], core.is_single_sku(o))
    ck("干净条目不被判二手「%s」" % o["name"], not core.is_used_or_demo(o))

# 二手判据最关键的一条：京东正常机型一个都不能误判。
# 第一版按「关键词出现」判，误判 6/34 —— 全是评价性表述：
#   「二手折价快」「二手保值率低」「低于 700 的基本是拆机件」「拆机有风险」
jd_bad = [o["name"] for o in core.load("jd")["offers"] if core.is_used_or_demo(o)]
ck("京东侧零误判为二手", len(jd_bad) == 0, str(jd_bad[:4]))

st = core.extra_stats()
ck("扩充候选池统计字段齐全",
   all(k in st for k in ("priceOk", "dropUsed", "dropMultiSku", "usable")), str(st))
ck("usable + dropUsed + dropMultiSku = priceOk",
   st["usable"] + st["dropUsed"] + st["dropMultiSku"] == st["priceOk"],
   "%d + %d + %d != %d" % (st["usable"], st["dropUsed"],
                           st["dropMultiSku"], st["priceOk"]))
ck("扩充候选带来源标记",
   all(o.get("extSrc") == "taobao" for o in core.extra_candidates()))

# 扩充候选不能污染默认推荐
s2 = {"cat": "thin", "budget": 8000, "purpose": ["office"], "years": 4, "carry": True}
r2 = core.build_recommendation(s2)
ck("默认推荐不含淘宝机型",
   not any(o.get("origin") == "taobao" for o in r2["ranked"]),
   str([o.get("origin") for o in r2["ranked"]]))
ck("core.load() 仍只读京东", len(core.load()["offers"]) == len(jd["offers"]))

# ---------------- 汇总 ----------------
print("\n" + "=" * 62)
print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("失败：")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("全绿。")