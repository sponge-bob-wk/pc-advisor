"""确定性决策内核。纯函数，不依赖任何模型。
按 PRD §3.1 分工：排序、加权、国补、必问判断全部在这里，模型无权修改。
"""
import json
import os
import re

# ── 数据路径（2026-11-13 改成相对仓库根）──────────────────────────
# 原来写死 E:\电脑涨价分析\data\...：别人 clone 到别的目录就跑不起来，
# GitHub Actions 上直接 FileNotFoundError（CI 第一次跑就红了）。
# 从本文件位置往上推三级：tools/agent/core.py → tools/agent → tools → 仓库根
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("PC_DATA_DIR") or os.path.join(REPO_ROOT, "data")

DATA = os.path.join(DATA_DIR, "prices.json")
MAJORS = os.path.join(DATA_DIR, "majors.json")


# ---- 专业要求表（2026-11-12 接入）------------------------------------
# 这份数据一直在库里，但**从来没有人读过它**（grep 不到任何引用）。
# 代价实测：用户说「人工智能专业大一新生，预算 7000」，
# 系统只抽出 purpose=dev，按跑分和价格在轻薄本里推了 MateBook 14（16G / 核显），
# 而这份表里人工智能属于「编程数据」组：
#   ramMin 16 / ramRec 32 / 显卡不限 / hardGates: upgrade>=2（内存焊死等于四年没后路）
# 也就是说：这台**刚好卡在底线**，而且不可加装 —— 表里明确写了这一组最不能接受什么。
def majors_data():
    try:
        with open(MAJORS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_MAJOR_INDEX = {}


def _major_index(d):
    """归一化索引：别名/全名 → (组, 规范专业名)。只建一次。"""
    if _MAJOR_INDEX:
        return _MAJOR_INDEX
    idx = {}
    for g in (d or {}).get("groups") or []:
        for m in g.get("majors") or []:
            name = m.get("name") if isinstance(m, dict) else m
            if not name:
                continue
            idx[name] = (g, name)
    for alias, full in ((d or {}).get("majorAliases") or {}).items():
        hit = idx.get(full)
        if hit and alias not in idx:
            idx[alias] = hit
    # 长名优先：先匹配"人工智能技术应用"，再匹配"人工智能"
    _MAJOR_INDEX.update(idx)
    return _MAJOR_INDEX


def match_major(text):
    """从用户原话里认出专业。认不出返回 (None, None)。

    ⚠️ 只认**表里有的**专业名/别名，不做相似度猜测 ——
    猜错专业等于把人家的四年配置搞错，比不认更糟（宪法第 3 条：宁可弃权）。
    """
    t = text or ""
    if not t:
        return None, None
    idx = _major_index(majors_data())
    for key in sorted(idx, key=len, reverse=True):
        if len(key) >= 2 and key in t:
            g, full = idx[key]
            return g, full
    return None, None

CAT_NAMES = {"thin": "轻薄本", "gaming": "游戏本", "desktop": "台式整机", "upgrade": "内存固态"}
ALLOWED_CATS = set(CAT_NAMES)          # v0.3 明确不做手机
REQUIRED_SLOTS = ("cat", "budget", "purpose", "years", "carry")

# ---- 多数据源注册表（2026-11-11 新增）--------------------------------
# 为什么要分开存：淘宝是别人爬的，机型名是标题式长串、价格口径有 5 种、
# 品类是推断的、内存列被屏幕尺寸污染过。它和京东自建的库**可信度不在一个层级**，
# 混在一张表里，出了问题分不清是谁的锅。
#
# 设计原则：**默认行为完全不变**（load() 仍然只读京东），
# 双源只在显式调用 load_all() 时合并。这样现有 100+ 项测试一条都不会被影响。
SOURCES = {
    "jd":     {"path": DATA,                                              "label": "京东"},
    "taobao": {"path": os.path.join(DATA_DIR, "taobao.json"),                "label": "淘宝"},
}
ACTIVE_SOURCE = "jd"          # load() 读哪个源


def data_as_of(d=None, src=None):
    """取数据截止日期，两个源的 meta 结构不一样，这里统一。

    实测 2026-11-11：淘宝那份 meta 里没有 `updated` 字段（只有 collectedAt / builtAt），
    切到淘宝源时 build_recommendation 直接 KeyError: 'updated' 崩掉。
    数据源可以换，字段名不能各写一套 —— 出口统一成这里。
    """
    d = d if d is not None else load(src)
    m = (d or {}).get("meta") or {}
    return (m.get("updated") or m.get("collectedAt")
            or m.get("builtAt") or "未知")


def load(src=None):
    src = src or ACTIVE_SOURCE
    with open(SOURCES[src]["path"], encoding="utf-8") as f:
        return json.load(f)


def load_all(price_ok_only=True):
    """合并两个源，给每个 offer 打上 origin 标记。

    price_ok_only=True 时，淘宝那批「券后价/卡片价」的条目不参与（145 条里只有 41 条口径可信）。
    这条开关是必须的 —— 券后价含未兑现优惠，当到手价用会直接误导用户。
    """
    out = []
    jd = load("jd")
    for o in jd.get("offers", []):
        o = dict(o)
        o.setdefault("origin", "jd")
        out.append(o)

    try:
        tb = load("taobao")
    except FileNotFoundError:
        tb = None
    if tb:
        for o in tb.get("offers", []):
            if price_ok_only and not o.get("priceOk"):
                continue
            out.append(dict(o))
    return out


# ---- 候选池扩充（2026-11-11 用户选定：淘宝用于扩充候选池）-------------------
# 用途：京东池太窄 —— gaming 在 ¥8000 以下 **0 台**，desktop 在 ¥3000 以下 0 台，
# thin 在 ¥4000 以下只有 2 台。淘宝那批机型能补上这些空档。
#
# 但**不能直接倒进去**，实测有三类必须先剔：
#   ① 二手 / 展示机 —— 价格可信的 41 条里有 4 条是这种：
#        tb-122 ¥1948「**二手商品**」
#        tb-123 ¥2398 评价写「成色不错，外观基本没有磨损」（成色是二手机专用说法）
#        tb-124 ¥3600 保修只剩 1 个月，卖家答「**展示机**」
#        tb 官翻 ThinkBook ¥6399
#      把二手当新机推给用户，比"少一台候选"严重得多。
#   ② 同系列多 SKU 且无法确定配置 —— 49% 的标题是这种，
#      价格对不上某一确定配置，参与排序就是拿假数据比。
#   ③ 没有跑分 —— 没有 perf 就排不了序，只能垫底。
#
# 结论：**只补「单 SKU + 全新 + 配置齐全」的那一小撮**，而且必须带标记，
# 让上层能说清「这台来自淘宝，没有得到京东那样的核验」。
USED_WORDS = ("二手", "成色", "翻新", "官翻", "展示机", "样机", "准新",
              "9成新", "八成新", "拆机", "库存机", "开箱", "拆封",
              "七天无理由退", "无理由退换", "店保", "店铺三包", "租")

# 分辨力对照（2026-11-11）：第一版把「关键词出现」当命中，京东侧误判 6/34。
# 误判全是因为**评价性表述**里有这些词，不是在说商品本身的状态：
#   「品牌售后弱，二手**折价快**」      ← 在评价保值率
#   「二手**保值率低**」                ← 同上
#   「低于 700 的基本是**拆机件**」     ← 在说配件来源，而且这句是 cons 里的提醒
#   「**拆机**有风险，卡扣容易断」      ← 在说拆机这个动作
# 判据要抓的是「这件商品的**状态定性**」，不是「文本里出现过这个词」。
#
# 所以：词出现后，看它后面跟的是不是「状态断言」；
# 跟的是「折价 / 保值 / 风险 / 件 / 价」这类评价词，就不算。
USED_NEG_TAIL = re.compile(
    r"二手\s*(折价|保值|价格|价|市场|行情|成色差|更便宜)"
    r"|拆机\s*(件|有风险|风险|工具|教程|视频|后|时)"
    r"|翻新\s*(件|价|成本|率)")


def is_used_or_demo(o):
    """是不是二手/展示/翻新/租赁。返回命中的词，没命中返回空列表。

    只看有据可查的字段（name/caution/note/tag/pros/cons），不猜。
    命中后还要过一道「是不是在说状态」的判定，见 USED_NEG_TAIL 的注释。
    """
    blob = " ".join(str(o.get(k) or "") for k in
                    ("name", "caution", "note", "tag", "pros", "cons"))
    hits = []
    for w in USED_WORDS:
        start = 0
        while True:
            i = blob.find(w, start)
            if i < 0:
                break
            seg = blob[i:i + 14]
            # 后面跟的是评价词 → 不是状态定性，跳过这一处
            if not USED_NEG_TAIL.search(seg):
                if w not in hits:
                    hits.append(w)
                break
            start = i + len(w)
    return hits


def is_single_sku(o):
    """能不能确定这条记录对应**一个**确定的配置。

    判据（宁严勿宽）：
      ① 标题里不能出现「/」或「+」分隔的多个型号
      ② note / caution / spec 里不能出现「按 SKU」「未选定 SKU」「两档」「冲突」这类说法
      ③ 内存、硬盘两个关键配置都得有值 —— 缺一个就没法确认说的是哪台
      ④ **标题里写的容量必须和 ram/disk 字段一致**

    ②③④ 都是被真实数据打回来之后补的：
      tb-076「小新Air14 2026 酷睿Ultra5」标题没斜杠，但 note 写着
        「内存 16GB 与 32GB 两档 / 硬盘 512GB 与 1TB 两档（均并列，未选定 SKU）」→ 漏
      tb-139「拯救者 Y7000P 2025」note 自述「两处冲突：参数区型号写 Y9000P，
        与标题不符…SKU 挂了 5 个型号」→ 漏
      tb-006「MagicBook Pro 14 酷睿Ultra5-125H **32GB**」标题写 32GB，
        但 ram=16 / spec=16G → 标题和字段打架，信谁都是猜 → 漏
    第一版判据只看标题有没有斜杠，三条全放过去了。
    """
    nm = str(o.get("name") or "")
    if "/" in nm or "+" in nm:
        return False
    if re.search(r"\d+\s*G\s*/\s*\d+\s*G", nm):
        return False
    blob = " ".join(str(o.get(k) or "") for k in ("note", "caution", "spec"))
    if MULTI_SKU_HINT.search(blob):
        return False
    if o.get("ram") is None or o.get("disk") is None:
        return False
    # ④ 标题里的容量断言必须和字段对得上
    for m in re.finditer(r"(\d{1,4})\s*(?:GB|G)\b", nm, re.I):
        n = int(m.group(1))
        if n in (8, 16, 32, 64, 128) and n != o.get("ram"):
            return False
        if n in (128, 256, 512, 1024, 2048) and n != o.get("disk"):
            # 128G 既可能是内存也可能是硬盘，两个都对不上才算矛盾
            if n != o.get("ram"):
                return False
    return True


# 「这条其实是多 SKU / 自相矛盾」的自述式证据。出现在 note/caution/spec 里就一律不算单 SKU。
MULTI_SKU_HINT = re.compile(
    r"未选定\s*SKU|按\s*SKU|多\s*SKU|两档|三档|多档"
    r"|(\d+\s*GB?\s*与\s*\d+\s*GB?)|(\d+\s*G\s*与\s*\d+\s*G)"
    r"|均并列|并列出现|以下配置|任选"
    # 显卡给多个选项（「RTX5060/5070」）—— 同上，不知道说的是哪张卡
    r"|(RTX|GTX|RX)\s*\d+\s*/\s*(RTX|GTX|RX)?\s*\d+"
    # 采集方**自己**标了冲突 / 不符 / 挂多个 SKU。
    # 实测 tb-139 拯救者 Y7000P 的 note 原文：「两处冲突：① 参数区型号写拯救者 Y9000P，
    # 与标题 Y7000P 不符；② 参数区内存 16GB，套餐名却写 32G内存+1TB固态…SKU 挂了 5 个型号」
    # 这种是采集方已经读过一遍页面的结论，比我们从标题猜可靠 —— 它说有冲突就是有冲突。
    r"|冲突|不符|不一致|挂\s*了\s*\d+\s*个|挂了\s*几个|套餐名|无法确定")


def extra_candidates(cat=None):
    """京东池之外，可以补进候选池的淘宝机型。

    返回的每条都带 extSrc 标记和 extWhy 说明，**上层必须如实告知来源**。
    一道都不过滤的空池是正常的 —— 宁缺勿滥。
    """
    try:
        tb = load("taobao")
    except FileNotFoundError:
        return []
    out = []
    for o in tb.get("offers", []):
        if not o.get("priceOk"):
            continue
        if cat and o.get("cat") != cat:
            continue
        used = is_used_or_demo(o)
        if used:
            o = dict(o, extSkip="二手/展示：" + "/".join(used))
            continue
        if not is_single_sku(o):
            continue
        o = dict(o)
        o["extSrc"] = "taobao"
        o["extWhy"] = "来自淘宝，只有价格和基本配置，没有实测跑分"
        out.append(o)
    return out


def extra_stats():
    """扩充候选池各道过滤掉了多少，给测试和报表用。"""
    try:
        tb = load("taobao")["offers"]
    except FileNotFoundError:
        return {"exists": False}
    ok = [o for o in tb if o.get("priceOk")]
    used = [o for o in ok if is_used_or_demo(o)]
    rest = [o for o in ok if not is_used_or_demo(o)]
    single = [o for o in rest if is_single_sku(o)]
    return {
        "exists": True,
        "total": len(tb),
        "priceOk": len(ok),
        "dropUsed": len(used),
        "dropMultiSku": len(rest) - len(single),
        "usable": len(single),
        "dropUsedNames": [o["name"] for o in used],
    }


def source_stats():
    """两个源各自多少条、多少条价格可信。给测试和报表用。"""
    st = {}
    for k in SOURCES:
        try:
            d = load(k)
        except FileNotFoundError:
            st[k] = {"label": SOURCES[k]["label"], "exists": False}
            continue
        offers = d.get("offers", [])
        st[k] = {
            "label": SOURCES[k]["label"],
            "exists": True,
            "total": len(offers),
            "priceOk": sum(1 for o in offers if o.get("priceOk", True)),
            "catInferred": bool(d.get("meta", {}).get("catInferred")),
        }
    return st


# ---- 跨源比价：明确不可行，代码层面也要堵死 -------------------------------
# 2026-11-11 实测结论（证据见 跨源比价-可行性结论.md）：
#   淘宝 145 条的标题 49% 本身含多个 SKU（「MagicBook Pro 14/16」「ROG 枪神 U9/RTX5090」），
#   内存/硬盘字段只有 34%/46% 可用，价格口径只有 41/145 可信；
#   而京东侧每台是**单一确定配置**（spec 形如 "R7 H260 · 16G + 512G · RTX 5050"）。
#   两者粒度不对等 → 同系列匹配出的差价是「顶配对基础款」的假差价：
#     淘宝「天选7 Pro 酷睿Ultra HX」¥15252  vs  京东「华硕天选 7」¥6799  → 差价 +8453
#   这个 +8453 拿去给用户看，比"没有数据"危险得多。
#
# 所以：**不提供跨源差价计算**。淘宝数据只用于单源检索，不做渠道比价。
# 这个函数永远返回 None 并给出理由 —— 留着接口是为了让上层能明确拒绝，
# 而不是让某天有人"顺手"写个减法。
COMPARE_ACROSS_SOURCES_OK = False


def cross_source_delta(name_or_id, cat=None):
    """跨源差价。**恒返回可解释的拒绝**，不是没实现。

    为什么不做（依据是实测，不是偷懒）：
      1. 粒度不对等 —— 京东一台一个确定配置，淘宝一个标题挂多个 SKU
      2. 价格口径不齐 —— 淘宝 145 条里只有 41 条是「到手价/净价」
      3. 匹配率太低 —— 品牌隔离 + 系列锚点后只剩 23/145 (16%)，其中价格可信 5 条
      4. 剩下的这 5 条仍会出错（同系列不同配置 / 淘宝价恰是京东原价）
    宁可说「算不了」，也不能给一个看着像真的假差价。
    """
    return {
        "ok": False,
        "reason": ("跨平台差价算不出来。京东这边每台是单一确定配置，"
                   "淘宝那边的标题经常一个链接挂好几个 SKU（内存/硬盘不同），"
                   "两边粒度对不上 —— 硬算出来的差价会把顶配和基础款比在一起，"
                   "偏差能到几千块。要横向比价，请把两边的具体配置和价格给我，"
                   "我照着配置逐项比。"),
        "matched": None,
        "stats": source_stats(),
    }


def usable_offers(d):
    """剔除手机。返回电脑 + 配件。

    ⚠️ 这是**人工核定层**（prices.json）的过滤，不是完整推荐池。
    要拿"库里一共有哪些机器"，用 merged_offers()。
    这两个函数分开是故意的：合并池会把 80 台采集机也算进来，
    而有些地方（比如判断"这个品牌库里有没有"）只该看人工核定过的那层。
    """
    return [o for o in d["offers"] if o.get("cat") in ALLOWED_CATS]


def _norm_name(s):
    """机型名归一化，只为**去重**用（判断"这两条是不是同一台机器"）。

    ⚠️ 只做书写形式统一，不做模糊匹配 —— 同名判定错了会把两台不同的机器合成一台，
    比重复更糟。所以这里不做「包含」判断，必须全等才算同一台。

    实测 2026-11-12 的两类问题：
      ① 品牌前缀不一致：「机械革命 苍龙 18 Pro」vs「苍龙 18 Pro」
         → 同一台机器在两层里各存一条，名字只差品牌前缀，按全名比就漏了。
         ⇒ 去掉开头重复的品牌名后再比。
      ② 同名但**不同 SKU**：「苍龙 18 Pro」有 RTX 5070 和 RTX 5060 两个版本，
         价格差 1225 元。这两条**不能合并**，它们确实是两台不同配置的机器。
         ⇒ 去重键必须带上 spec，spec 不同就不算同一台。
    """
    import unicodedata
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[\s\-_/（）()·,，。]+", "", s)
    return s


def _gpu_key(o):
    """显卡型号归一（只到型号级：RTX 5070 / RTX 5060 / 核显）。

    ⚠️ 不取显存、不取 TGP、不取完整 spec —— 只用来区分"是不是同一个配置版本"。
    取太细会把同一台机器拆成两条（实测：采集层 spec 是代码拼的、
    人工层是手写的，格式本来就对不上）。
    """
    spec = o.get("spec") or ""
    m = re.search(r"\b(RTX|GTX|RX)\s*(\d{4})\s*(Ti|Super|GRE)?", spec, re.I)
    if m:
        return (m.group(1) + m.group(2) + (m.group(3) or "")).lower()
    gpu = ((o.get("gpu") or {}).get("model") or "") if isinstance(o.get("gpu"), dict) else ""
    if gpu:
        m = re.search(r"\b(RTX|GTX|RX)\s*(\d{4})", gpu, re.I)
        if m:
            return (m.group(1) + m.group(2)).lower()
    if o.get("cat") == "gaming":
        return ""      # 游戏本没写清楚显卡 → 空键，宁可不去重也不合错
    return "igpu"      # 轻薄/台式无独显


def _name_core(o):
    """机型名里**只留型号本体**，剥掉括号里那段配置说明。

    🔴🔴 为什么必须剥（2026-11-12 实测，v3 去重键的漏洞）：
      采集层的名字长这样：
        「天选7 2026（锐龙7 H260/RTX5050）」
        「MateBook D 16（i5-13420H/16G/1T，店铺预装 Win 未激活）」
      人工层同款只写：
        「华硕天选 7」 「华为 MateBook D16」
      `_norm_name` 把括号里的字也归一进去了，于是键永远对不上：
        '华硕天选7|rtx5050'  vs  '天选72026锐龙7h260rtx5050|rtx5050'
      同一台机器在池里出现两次，双双进推荐列表 —— 用户看到两台一模一样的机器，
      而且这两条会互相抢"第一名"。

    ⚠️ 剥的是**括号里的内容**，不是整串模糊匹配：
      括号里放的是配置说明（CPU/内存/备注），型号本体在括号外。
      括号外的年份（「天选7 2026」的 2026）**不能剥** ——
      2026 版和 2025 版是不同机器。所以去掉年份这件事交给调用方决定，
      这里只负责剥括号。
    """
    s = (o.get("name") or "") if isinstance(o, dict) else str(o or "")
    import unicodedata
    s = unicodedata.normalize("NFKC", s)
    # 中文全角括号已被 NFKC 转成半角，这里一并处理
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    return _norm_name(s)


def _dedup_key(o):
    r"""去重键 = 归一化型号名（剥括号配置说明 + 剥冗余品牌前缀）+ 显卡型号。

    🔴 演进过程（都是实测打回来的，别退回去）：
      v1 只按全名 —— 「机械革命 苍龙 18 Pro」和「苍龙 18 Pro」去不成重，重复 1 条
      v2 名字 + **完整 spec** —— 失败。采集层的 spec 是代码拼的、人工层是手写的，
         格式对不上：同一台暗影精灵 Pro 15 去不成重（重复变 2 条），
         而苍龙 18 Pro 的 5070/5060 两版反而被当成两台正确留下了。
      v3 名字 + **显卡型号**（用户 2026-11-12 选定）—— 基本正确。
         只差内存/硬盘的（暗影 Pro 15 的 512G vs 1T）算同一台 → 去重；
         差显卡的（苍龙 18 Pro 5070 vs 5060）是两台不同配置 → 都留着。
      v3.1（2026-11-12 二次实测）—— 名字必须先剥**括号**（见 _name_core）。
         采集层把配置说明塞在型号后面的括号里，v3 的 `_norm_name` 把它全带进键，
         于是「天选7 2026（锐龙7 H260/RTX5050）」和人工层「华硕天选 7」合成不了，
         同一台机器在推荐列表出现两次。同理踩中 MateBook D16 / Redmi Book Pro 14。

    🔴 原则：**合错比留重复严重**。合错是把 A 的价格/评价安到 B 头上，
    用户查证时在库里能找到，反而更信 —— 和串台是同一类错误的两种形态。
    但"留重复"也不是没成本：重复的两条会互相抢排名，用户会以为库里有货。

    ⚠️ 两处细节（2026-11-12 第三次实测）：
      ① **品牌前缀必须剥干净**。原来写 `len(name) > len(brand) + 3`，
         「华硕天选 7」剥完品牌只剩 4 个字，被这道守卫挡回来，于是
         '华硕天选7' 和 '天选7' 永远对不上。守卫改成"剥完至少留 3 个字"。
      ② **年份要归一**。人工层写「华硕天选 7」（默认当今年款），
         采集层写「天选7 2026」—— 年份一有一无，键就对不上。
         2026 / 2025 这种**四位年份**只是标注当年款，不改变型号本身；
         它写在**括号里**的（「（2026 款）」）已被 _name_core 剥掉，
         写在名字里的在这里统一剥掉。
         ⚠️ 只剥 `20[12]\d` 四位年份，**不剥「天选7」的 7** ——
            7 是型号代数，6 代和 7 代是两台不同机器（这条是硬边界）。
    """
    name = _name_core(o)
    brand = _norm_name(o.get("brand"))
    if brand and name.startswith(brand) and len(name) - len(brand) >= 3:
        name = name[len(brand):]
    name = re.sub(r"20[12]\d", "", name) or name
    return name + "|" + _gpu_key(o)


def merged_offers(d=None, with_pool=None):
    """推荐池 = 人工核定层（prices.json 22 台）+ 京东采集池（80 台）。

    🔴🔴 为什么必须合并（2026-11-12 用户提出「界面还是22台不是要求全用吗」）：
      之前推荐池只有 prices.json 那 22 台 —— 它们是**人工逐台写过优缺点**的一层。
      采集层另有 80 台（轻薄 43 / 游戏 37），参数齐全、价格有完整推导链，
      但 pros/cons 是空的（爬虫爬不到评价），于是整批被挡在门外。
      用户看到的"库里只有 22 台"就是从这个过滤来的。

    🔴 合并顺序与冲突处理：
      ① 人工层在前 —— 它有评价，排序时同分优先
      ② 采集层里与人工层**同名**的剔除（否则同一台机器出现两次，
         "换一台"会把它推两遍，用户以为库里有两台）
      ③ 重名但**价格不一致** → 不偷偷选一个，记进 `_priceConflicts` 让上层能说
         （实测唯一一例：暗影精灵 Pro 15，人工层 ¥8698 来自 smzdm，
           采集层 ¥9999 是 11-11 15:00 京东实抓。）

    🔴 采集层的机器 `hasReview=False`，上层必须如实说「只有参数，没人写过评价」。
       绝不许把参数换个说法当优点念出来 —— 那是编造。

    ⚠️ `with_pool`（2026-11-12 修回归）：
      `d` 传了自定义数据时（测试切到淘宝源），**不能**再偷偷混进京东采集池 ——
      那等于"我让你只用淘宝，你掺了 78 台京东进来"，测试也就测不出真东西了。
      默认行为：d 为 None（正常走 load()）时并入采集池；d 显式传入时不并入。
      要强制指定，传 with_pool=True/False。
    """
    if d is None:
        d = load()
        if with_pool is None:
            with_pool = True
    elif with_pool is None:
        with_pool = False
    base = [dict(o) for o in usable_offers(d)]
    seeded = {}
    for o in base:
        o.setdefault("hasReview", bool(o.get("pros") or o.get("cons")))
        o.setdefault("origin", "jd")
        seeded[_dedup_key(o)] = o

    if not with_pool:
        _LAST_MERGE.clear()
        _LAST_MERGE.update({"base": len(base), "poolAdded": 0, "total": len(base),
                            "priceConflicts": [],
                            "poolNoReview": sum(1 for o in base
                                                if not o.get("hasReview"))})
        return base

    conflicts = []
    added = 0
    # 🔴🔴 只有**人工层的键**参与"撞键即合并"判定 —— 这是 2026-11-12 二次踩的坑。
    #   原写法是 `if k in seeded` 且 `seeded[k] = o` 在循环里继续加，
    #   于是**采集池内部也互相去重**了。而采集池那 80 条本来就是不同 SKU：
    #     小新 Pro 14 2026（锐龙版）      ¥6629
    #     小新 Pro 14 2026（酷睿 Ultra 7 版） ¥8329
    #   剥掉括号后同名、同为核显 → 键相同 → 被当成一台，后一条直接丢掉。
    #   后果不是"少一台"，是**丢了一整台机器**，而且价格冲突表从 2 条涨到 8 条，
    #   看上去像"发现了很多价格矛盾"，其实全是我自己造的。
    #   ⇒ 只有 base 层（人工核定过的那 22 台）的键有权"吃掉"采集层条目。
    #
    # ⚠️ 但采集层**自身内部完全同名同配**的两条，确实是重复，必须记下来上报。
    #   实测一例（2026-11-12）：荣耀 MagicBook Pro 16 2026（Ultra5-338H/32G/1T）
    #   在 machine_pool.json 里存了两条，¥8455.02 和 ¥6969.15，名字一字不差。
    #   🔴 数据文件我没有写权限（用户明确要求），所以**不合并、不偷选一个**，
    #      两条都留在池里，把这对记进 priceConflicts 让上层如实报出来。
    #      多一条列表项是可见的瑕疵；偷偷删掉一条或挑个价，才是不可见的那种错。
    base_keys = {_dedup_key(o) for o in base}
    seen_raw = {}
    for o in pool_offers():
        k = _dedup_key(o)
        if k in base_keys:
            old = seeded[k]
            if old.get("price") and o.get("price") and abs(old["price"] - o["price"]) > 1:
                conflicts.append({
                    "name": o.get("name"),
                    "reviewPrice": old.get("price"),
                    "poolPrice": o.get("price"),
                    "kept": "review",
                })
            continue
        # 采集层之间的重名**一律不去重** —— 名字撞了不代表是同一台。
        # 例如「战66 八代 14 英寸」和「战66 八代 15.6 英寸」剥括号后都叫战66八代，
        # 合了就把 14 寸的价安到 15 寸上。宁可列表里多一条，也不能让价格张冠李戴。
        #
        # 但"完全同名且同价不同"的两条要记进 conflicts（见上面那段注释）：
        # 这是数据本身的重复，需要人工裁决，不该由代码替它决定留哪条。
        # ⚠️ 判据必须用**原始全名**（剥括号之前），不能用 _dedup_key：
        #   键相同只说明"型号本体+显卡一样"，不代表是同一条记录 ——
        #   「小新 Pro 14 2026（锐龙版）」和「小新 Pro 14 2026（酷睿 Ultra 7 版）」
        #   键撞、价差 1700，但它们是**两台不同的机器**，报成重复就是误报。
        #   真正的重复长这样：荣耀 MagicBook Pro 16 2026（Ultra5-338H/32G/1T）
        #   在池里存了两条，名字一字不差，价 ¥8455.02 / ¥6969.15。
        raw = re.sub(r"\s+", "", (o.get("name") or "")).lower()
        if raw and raw in seen_raw:
            prev = seen_raw[raw]
            if (prev.get("price") and o.get("price")
                    and abs(prev["price"] - o["price"]) > 1):
                conflicts.append({
                    "name": o.get("name"),
                    "reviewPrice": prev.get("price"),
                    "poolPrice": o.get("price"),
                    "kept": "pool-duplicate",   # 两条都留着，标记为池内重复
                })
        seen_raw.setdefault(raw, o)
        seeded[k] = o
        base.append(o)
        added += 1

    _LAST_MERGE.clear()
    _LAST_MERGE.update({"base": len(base) - added, "poolAdded": added,
                        "total": len(base),
                        "priceConflicts": conflicts,
                        "poolNoReview": sum(1 for o in base if not o.get("hasReview"))})
    return base


_LAST_MERGE = {}


def merge_stats():
    """最近一次合并的统计。调用过 merged_offers 之后才有值。"""
    return dict(_LAST_MERGE)


# ==== 京东采集池（machine_pool.json，80 台）接入推荐池 ======================
# 背景（2026-11-12 用户要求「界面还是22台不是要求全用吗」）：
#   之前只有 prices.json 的 22 台进推荐池，而那 22 台是**人工写过优缺点**的一层。
#   采集层另有 80 台（轻薄 43 / 游戏 37），参数齐全、价格有完整推导链，
#   但 pros/cons 全空 —— 于是被排除在外，用户看到的就是"库里只有 22 台"。
#
# 为什么现在能接：参数层要的信息它都有（CPU/GPU/内存/硬盘/屏幕/重量/电池/接口），
# 而推荐引擎排序靠的是 perf / upgrade / portable 三个分数，这三个都能从参数**算**出来
# （perf 走 bench.json 的 CPU 跑分表，见 _perf_of_pool）。
#
# 🔴 铁律：**算不出来的就是 None，绝不用 0 或拍脑袋的数值填充。**
#   原因见 main_score 的注释 —— 缺分被当成 0 分会把整个池子的分位数压塌。
#   80 台里只有 32 台能在 bench 表里查到 CPU 分，其余 48 台必须显式缺分。
#
# 🔴 pros/cons 不生成、不补齐、不摘要。
#   这两栏是**人工判断**，爬虫爬不到，模型也不许编。
#   上层拿到这类机型必须说清「这台只有参数，没人写过评价」，
#   而不是把参数换个说法当成优点念出来。

POOL_PATH = os.path.join(DATA_DIR, "machine_pool.json")
BENCH_PATH = os.path.join(DATA_DIR, "bench.json")


def _cpu_key(name):
    """CPU 型号归一化 —— 同一颗 CPU 在不同数据里有好几种写法，不归一就对不上。

    实测 2026-11-12（接入 80 台时暴露）：采集池写 `Ryzen 7 H255`，
    跑分表写 `R7 H255`；采集池写 `Core Ultra 5 338H`，跑分表写 `Ultra 5 338H`。
    直接字符串相等就只有 32/80 能匹配上，其余全部被当成"无跑分"垫底 ——
    而它们其实是有分的，只是名字写法不一样。

    实测这批变体（全部来自真实数据，不是设想）：
      Ryzen 7 H255 / Ryzen7  / R7 H255 / 锐龙7 H 260    → r7 ...（AMD 三种写法）
      Core Ultra 5 338H / Ultra5-338H / Ultra 5 338H    → ultra 5 338h
      Core5-320 / Core 5 320                            → core5 320
      Ultra7-251HX / Ultra 7 251HX                      → ultra 7 251hx

    归一规则只做**书写形式统一**，不做型号替换、不做模糊匹配：
    差一个数字就是不同芯片（H255 vs H260 是两颗），模糊匹配等于编数据。
    """
    s = (name or "").strip().lower()
    if not s:
        return ""
    s = s.replace("（第1代）", "").replace("(第1代)", "")
    # 中文型号名（跑分表里用的是「酷睿5 320」这种写法）
    s = re.sub(r"^酷睿\s*", "core", s)                # 酷睿5 320 → core5 320
    # AMD：ryzen / 锐龙 → r；「r7 h 260」和「r7 h260」是同一颗，去掉字母数字间空格
    s = re.sub(r"^(ryzen|锐龙)\s*ai\s*", "ai ", s)     # 锐龙AI 7 H 350 → ai 7 h 350
    s = re.sub(r"^(ryzen|锐龙)\s*", "r", s)           # ryzen 7 / 锐龙7 → r7
    s = re.sub(r"\br(\d)\s*-\s*", r"r\1 ", s)         # r9-9955hx → r9 9955hx
    s = re.sub(r"^core\s+ultra\s+", "ultra ", s)      # core ultra 5 338h → ultra 5 338h
    s = re.sub(r"^core\s*(\d)", r"core\1", s)         # core 5 → core5（与 core5-320 统一）
    # 型号内部的分隔符差异：ultra5-338h / ultra 5 338h / ultra5 338h 是同一颗
    s = re.sub(r"(ultra|core)(\d)", r"\1 \2", s)      # ultra5 → ultra 5，core5 → core 5
    s = re.sub(r"(ultra\s+\d)\s*-\s*", r"\1 ", s)     # ultra 5-338h → ultra 5 338h
    s = re.sub(r"(core\s+\d)\s*-\s*", r"\1 ", s)      # core 5-320 → core 5 320
    # 末尾「h 260」→「h260」：AMD 的 H 系列在两边写法不一致
    s = re.sub(r"\bh\s+(\d)", r"h\1", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _bench_map():
    """CPU 型号 → perf 分（0-100，基于 Cinebench R23，与 prices.json 同标尺）。

    只在 bench.json 里取 `perfCurrent`，没有就返回空 —— 不去用多核分现算，
    那需要一套换算系数，等于自己发明标准。
    """
    try:
        with open(BENCH_PATH, encoding="utf-8") as f:
            b = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    out = {}
    for c in b.get("cpus") or []:
        p = c.get("perfCurrent")
        k = _cpu_key(c.get("name"))
        if p is not None and k:
            out[k] = p
    return out


def _screen_text(m):
    """屏幕描述，拼成人类读得懂的短串：「14.2 寸 2.8K OLED 120Hz 100% DCI-P3」。

    🔴 为什么必须补这段（2026-11-12 实测）：
      原来 spec 只拼 CPU/内存/硬盘，屏幕信息**完全不出现**。
      后果：「预算 7000 学设计」推了 MateBook 14 2026（2.8K OLED / 100% DCI-P3），
      这是对的 —— 但它在这批候选里屏幕最好，回答却只说「适合带去教室办公」，
      一个字没提屏幕。学设计的人最关心的恰恰是屏幕，
      而我们的数据**明明有**这个参数（screen 对象里全景记录），只是没传给模型。

    ⚠️ 只搬运已有字段，缺哪个就不写哪个，不做任何补全或推断。
    """
    sc = m.get("screen") or {}
    parts = []
    inch = sc.get("sizeInch")
    if inch:
        parts.append(("%g 寸" % inch) if isinstance(inch, (int, float)) else str(inch))
    w, h = sc.get("widthPx"), sc.get("heightPx")
    if w and h:
        # 2.8K 这类叫法是行业习惯，但不同厂商标法不一，所以直接写像素更硬
        parts.append("%d×%d" % (w, h))
    panel = sc.get("panel")
    if panel:
        parts.append(str(panel))
    hz = sc.get("refreshHz")
    if hz:
        parts.append("%dHz" % hz)
    gamut = sc.get("colorGamut") or {}
    if isinstance(gamut, dict):
        for k, label in (("dciP3", "DCI-P3"), ("srgb", "sRGB"), ("adobeRGB", "AdobeRGB")):
            v = gamut.get(k)
            if v:
                parts.append("%d%% %s" % (v, label))
    return " ".join(parts)


def _spec_text(m):
    """把结构化参数拼成 prices.json 那种 spec 串：「R7 H255 · 16G + 512G · RTX 5050」。

    ⚠️ 只搬运已有字段，不补全。
    实测 2026-11-12：80 台里 15 台的 cpu.model 是空串，
    如果这时写「未知 CPU」就会被下游当成一个型号名去匹配 bench 表 —— 干脆不写这段。
    """
    parts = []
    cpu = ((m.get("cpu") or {}).get("model") or "").strip()
    if cpu:
        parts.append(cpu)
    ram = (m.get("ram") or {}).get("gb")
    disk = m.get("storageGB")
    if ram and disk:
        gb = disk // 1024 if disk >= 1024 else disk
        unit = "T" if disk >= 1024 else "G"
        parts.append("%dG + %d%s" % (ram, gb, unit))
    elif ram:
        parts.append("%dG" % ram)
    gpu = ((m.get("gpu") or {}).get("model") or "").strip()
    if gpu and (m.get("gpu") or {}).get("type") == "discrete":
        parts.append(gpu)
    # 屏幕接在最后。它不属于"核心配置"那一组的语法，所以用空格而不是「·」分隔，
    # 读起来像「Ultra 7 255H · 16G + 512G 14.2 寸 2880×1920 OLED 120Hz」。
    scr = _screen_text(m)
    if scr:
        parts.append(scr)
    return " · ".join(parts[:3]) + (" " + parts[3] if len(parts) > 3 else "")


def _upgrade_of_pool(m):
    """可升级性 0-3 分，判据只认**结构字段**（内存形态 + 插槽数 + 有没有硬盘位）。

    这条打分是本项目里唯一允许"从参数推结论"的地方，因为它是**硬件结构事实**：
    板载内存就是不能加，有双插槽就是能加 —— 不需要评测，看规格就能定。
    但仍然是三档粗分，不是精确值；拿不准的一律返回 None。
    """
    ram = m.get("ram") or {}
    form = (ram.get("form") or "").lower()
    slots = ram.get("slots")
    if not form and slots is None:
        return None
    # 板载（sodimm 是插槽式，onboard/soldered 是焊死）
    if form in ("onboard", "soldered") or slots == 0:
        return 0
    if slots == 2:
        return 3
    if slots == 1:
        return 2
    # 形态是插槽但没写槽数 → 至少能换，给中档
    if form == "sodimm":
        return 2
    return None


# ==== 重量：单一来源（2026-11-13）============================================
# 🔴 真实事故，不是假想。2026-11-13 公网实跑「5000块写代码，轻一点」，
#    同一份回答里出现两句互相打脸的话：
#       正文：  「虽然它只有 62.5% sRGB 色域，且 1.48kg 的重量在 14 寸里偏重」
#       字段卡：「重量  库里没记实测重量」
#       内核：  weightKg = null
#    两句都来自库、都没编造 —— 但同一份回答对同一个事实说了两种话。
#    用户不知道该信哪句，这比"少说一句"严重。
#
# 根因：**同一个事实在库里有两个存放处**
#    ① 自由文本 cons/pros —— 「1.48kg 在 14 寸里偏重」
#    ② 结构化字段 weightKg —— 空
#    模型读得到 ①、读不到 ②（② 被 `if not w` 判成"没记"）。
#    叠加因素：这台机器在池里有两条记录（thin-zhan66 有人工评价无重量 /
#    spec-hp-66g8-amd 有重量无评价），去重键对不上（"锐龙版" vs "14 英寸"），
#    于是不管挑中哪条，都只有一半的事实。
#
# 🔴 为什么**不**把两条合并：合并是把 A 的价格/评价安到 B 头上（见 _dedup_key
#    注释「合错比留重复严重」）。而"名称里用不同维度描述同一台"（锐龙版 vs
#    14 英寸）无法用字符串判定 —— 硬合一定会在别的机型上合错。
#    ⇒ 正确做法是让**重量这一个事实**在两条记录上一致，而不是让两条记录变成一条。
#
# 🔴 抽取必须带分辨力（宪法第 4 条）。下面这几种**不许**抽：
#      「比 1.4kg 的竞品重」        → 那是别家的重量
#      「同价位有 1.3kg 的机器更轻」  → 那是别家的重量
#      「比上一代轻了 0.2kg」       → 是差值，不是绝对重量
#      「背包承重 5kg」            → 不是本机重量
#      「1.4-1.6kg 视配置」        → 两个数，不猜
#    判据：**一句话里只能有一个 kg 数，且句子不能带"比/对比/同级/竞品/代"这类
#    指向别处的词。** 两者任一不满足就弃权 —— 弃权只是"没记"，抽错是给错数字。

# 出现这些词，说明句里的 kg 数可能指的是**别的机器**而不是本机
_KG_ELSEWHERE = re.compile(
    r"比|对比|相较|竞品|同级|同价位|同类|同尺寸|上代|上一代|前代|别的|其他|其余|"
    r"友商|对手|其他品牌|竞对|同级产品|背包|适配器|电源|充电器|扩展坞|承重|限重"
)

# 一个 kg 数：前面不能紧贴数字（避免 1.4-1.6 只抓到后半段）
_KG_ONE = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)\s*(?:kg|KG|Kg|公斤|千克)")

# 区间写法：1.4-1.6kg / 1.4~1.6kg / 1.4 到 1.6kg
# 🔴 这是**两个**重量，代表"视配置而定"—— 任取一个都是把区间说成确定值。
_KG_RANGE = re.compile(
    r"\d{1,2}(?:\.\d{1,2})?\s*(?:-|–|—|~|～|到|至)\s*\d{1,2}(?:\.\d{1,2})?\s*kg",
    re.I
)


def weight_from_review_text(o):
    """从 pros/cons 里抽这台机器**自己**的重量（kg）。抽不到返回 None。

    判据（有分辨力，见上面那段反例清单）：
      ① 整个 pros+cons 里，能匹配到的 kg 数必须**恰好只有一个**
         —— 多个就弃权，因为分不清哪个是本机的
      ② 那个数所在的那一句，不能带指向别处的比较词
      ③ 数值必须在 0.5–5.0 kg 之间（笔记本/整机的物理区间，超出必是别的语义）
    """
    if not isinstance(o, dict):
        return None
    texts = []
    for k in ("cons", "pros"):
        v = o.get(k)
        if isinstance(v, list):
            texts.extend(x for x in v if isinstance(x, str))
        elif isinstance(v, str):
            texts.append(v)
    if not texts:
        return None

    hits = []          # [(值, 句子)]
    for t in texts:
        # 🔴 区间写法先排除：一句话里出现 "1.4-1.6kg" 就是两个重量，
        #    代表"视配置而定"。抽其中任何一个都是把区间说成确定值。
        if _KG_RANGE.search(t):
            continue
        for m in _KG_ONE.finditer(t):
            try:
                hits.append((float(m.group(1)), t))
            except ValueError:
                continue
    # ① 只能有一个候选
    if len(hits) != 1:
        return None
    val, sent = hits[0]
    # ② 句子不能指向别处
    if _KG_ELSEWHERE.search(sent):
        return None
    # ③ 物理区间
    if not (0.5 <= val <= 5.0):
        return None
    return val


def effective_weight(o):
    """这台机器的重量（kg）：**结构化字段优先，文本兜底**。

    🔴 优先级不能反。weightKg 是人核对过的结构化数据；cons 里的数字是
    写评价时顺手记的。两者冲突时（理论上不该有），以结构化字段为准，
    并且**不覆盖原字段** —— 只在读取侧兜底，数据文件保持原样。
    """
    if not isinstance(o, dict):
        return None
    w = o.get("weightKg")
    if w is not None:
        return w
    return weight_from_review_text(o)


def _portable_of_pool(m):
    """便携性 0-3 分，判据只有重量（kg）。分档边界是人定的，写死在这里便于复核。"""
    w = effective_weight(m)
    if w is None:
        return None
    if w <= 1.4:
        return 3
    if w <= 1.8:
        return 2
    if w <= 2.4:
        return 1
    return 0


# ---- 品类红线：库里只做 Windows 笔记本 / 台式整机 / 内存固态 ----------------
# 🔴 2026-11-13 实跑抓到：`machine_pool.json` 里有 4 台苹果机（cat=thin），
#   于是「预算 6000 左右，轻薄本，上课用」的首推变成了
#   **MacBook Air 13 M4（¥6161.65）**。
#   这与两处写死的事实直接矛盾：
#     ① PRD §6「手机 / 平板 / 工作站 / Mac —— 明确拒答」
#     ② 页面首屏「只限 Windows 笔记本 / 台式整机 / 内存固态」
#   更要命的是它的出口审计已经报了 `库外机型:['MacBook']`，但兜底模板
#   `template_reply(first)` 把同一个机名又念了一遍 —— 检出了，还是发出去了。
#
# 为什么在**取数口子**挡，而不是在出口挡：
#   出口挡是「说了再删」，机器已经占掉一个推荐位，第二名被挤下去，
#   候选清单和「换一台」也会跟着错位。范围外的机型根本不该进排序池。
#
# ⚠️ 只改代码、不改数据（`machine_pool.json` 零写权限）：
#   那 4 条记录原样留在文件里，是"采集到了但不该推荐"，不是"数据错了"。
OUT_OF_SCOPE_BRANDS = ("apple", "苹果")
OUT_OF_SCOPE_NAME_KW = ("macbook", "imac", "mac mini", "mac studio", "mac pro")


def is_out_of_scope_offer(o):
    """这台机器是否在产品覆盖范围之外（Mac / 苹果整机）。

    判据只写这一处：采集池取数、测试、上层追问都调它。
    品牌为空时**不能**当成范围外 —— 宁可漏挡，不许因为字段缺失就删机器
    （人工层那 22 台有些 brand 写着中文简称）。
    """
    brand = (o.get("brand") or "").strip().lower()
    if brand in OUT_OF_SCOPE_BRANDS:
        return True
    name = (o.get("name") or "").lower()
    if any(k in name for k in OUT_OF_SCOPE_NAME_KW):
        return True
    chip = (o.get("chip") or "").lower()
    return chip.startswith("apple")


def _pool_to_offer(m, bench):
    """把一条采集池记录转成推荐引擎认识的 offer 结构。

    转不过去的（没有价格 / 品类不在范围内）返回 None。
    """
    price = ((m.get("price") or {}).get("final"))
    if not price:
        return None
    cat = m.get("cat")
    if cat not in ALLOWED_CATS:
        return None
    cpu = ((m.get("cpu") or {}).get("model") or "").strip()
    return {
        "id": m.get("id"),
        "name": m.get("name"),
        "cat": cat,
        "spec": _spec_text(m),
        "price": price,
        "perf": bench.get(_cpu_key(cpu)) if cpu else None,   # 查不到就是 None，不补
        "upgrade": _upgrade_of_pool(m),
        "portable": _portable_of_pool(m),
        "brand": m.get("brand"),
        # 🔴 下面三栏**故意留空**：这是人工判断，采集层没有。
        # 留空是为了让上层能看出"这台没评价"，而不是被一句假 tag 糊过去。
        "tag": "",
        "caution": "",
        "pros": [],
        "cons": [],
        "chip": cpu,
        # 🔴 GPU 结构化字段（2026-11-12 接）：采集池每条都带 gpu.type/model，
        #   人工层（prices.json）没有这个字段。
        #   为什么叫 gpuKind 而不是 "gpu"：_gpu_key() 拿 o["gpu"] 算去重键，
        #   一改就会改变 97 台的去重结果（见 §4.3），不能顺手动。
        #   有了它才能区分「数据说这是核显」和「数据里没提显卡」——
        #   前者可以判硬伤，后者只能承认不知道（宪法第 2 条）。
        "gpuModel": (m.get("gpu") or {}).get("model"),
        "gpuKind": (m.get("gpu") or {}).get("type"),
        # 实测重量（kg）。采集池 78/80 有，人工层没有（那边只有 1-3 档 portable 分）。
        # ⚠️ 两套单位不能混：portable=2 不是"2kg"，绝不能拿来比重量。
        # 🔴 用 effective_weight：人工层的结构化字段常为空，但 cons 里写着
        #    「1.48kg 在 14 寸里偏重」—— 不兜底的话同一事实会两处说法打架。
        "weightKg": effective_weight(m),
        # 价格推导链原样带上，供「这个价怎么来的」追溯用
        "priceBasis": (m.get("price") or {}).get("basisNote"),
        "origin": "jd-pool",
        "hasReview": False,
    }


_POOL_CACHE = {}
# 采集到、但按品类红线挡在推荐池外的机型（当前是 4 台苹果机）。
# 留着不是为了推荐，是为了能回答"为什么池子里不是 80 台"。
_SCOPE_DROPPED = []


def pool_offers(cat=None):
    """京东采集池 80 台，转成 offer 结构。按品类过滤。

    结果缓存 —— 这份文件 300KB+，每次排序都重读会把一轮对话拖慢几百毫秒。
    缓存键带文件 mtime，数据更新后自动失效，不会读到旧池。
    """
    try:
        mt = os.path.getmtime(POOL_PATH)
    except OSError:
        return []
    key = ("pool", mt)
    if key not in _POOL_CACHE:
        try:
            with open(POOL_PATH, encoding="utf-8") as f:
                d = json.load(f)
        except (FileNotFoundError, ValueError):
            _POOL_CACHE[key] = []
            return []
        bench = _bench_map()
        out = []
        dropped_scope = []
        for m in d.get("machines") or []:
            o = _pool_to_offer(m, bench)
            if not o:
                continue
            # 范围外（Mac / 苹果整机）不进推荐池 —— 见 is_out_of_scope_offer 上面的说明
            if is_out_of_scope_offer(o):
                dropped_scope.append(o.get("name"))
                continue
            out.append(o)
        _POOL_CACHE.clear()          # 只保留最新一份，别把旧 mtime 攒着
        _POOL_CACHE[key] = out
        _SCOPE_DROPPED[:] = dropped_scope
    out = _POOL_CACHE.get(key) or []
    return [o for o in out if not cat or o.get("cat") == cat]


def pool_stats():
    """采集池接入情况，给界面和测试用。数字必须来自实算，不许写死。"""
    allp = pool_offers()
    by = {}
    for o in allp:
        by[o["cat"]] = by.get(o["cat"], 0) + 1
    return {
        "total": len(allp),
        "withPerf": sum(1 for o in allp if o.get("perf") is not None),
        "noPerf": sum(1 for o in allp if o.get("perf") is None),
        "byCat": by,
        # 采集池原始条数 vs 进池条数：差的这几台是苹果机（范围外，不是数据错）
        "outOfScope": len(_SCOPE_DROPPED),
    }


def mode_of(slots):
    """槽位 → 侧重。映射表人写，模型无权发明。"""
    p = slots.get("purpose") or []
    if isinstance(p, str):
        p = [p]
    # 「越便宜越好」是一个明确的偏好，不是缺信息。
    # 之前模型把它幻觉成 budget=500，于是推 ¥3000 那台还判超预算。
    if slots.get("_cheapest"):
        return "value"
    # 「太贵了便宜点」：预算上限还在，只是这轮按价格优先排。
    # 不能走 _cheapest —— 那是"没有预算"，会把 8000 的机器推成 3000 的。
    if slots.get("_cheaper"):
        return "value"
    if "gaming" in p:
        return "game"
    if "design" in p or "video" in p:
        return "work"
    if "dev" in p:
        return "work"
    if slots.get("carry"):
        return "carry"
    if slots.get("years") == 4:
        return "future"
    return "daily"


def main_score(o, mode):
    """主维度分。数据缺 gpuPerf/cpuBurst，game 侧重只能按品类+perf 退化，此处如实标注。

    🔴 算不出来时返回 None，**不要返回 0.0**。
    实测 2026-11-11（淘宝入库后暴露）：淘宝那 41 条 perf/portable/upgrade 全空，
    原来 `(o.get("portable") or 0) * 33.3` 把它们全算成 0 分。后果不只是淘宝条目垫底 ——
    rank_offers 拿整个池子算 p33/p66 分位数，41 个 0 分把阈值压塌：
        gaming 的 p66 从 77.0 掉到 0.0  → 所有机型都进最高档，三档分级彻底失效
        thin 的 p33   从 66.6 掉到 0.0
    也就是说「没数据的机器」把「有数据的机器」的档位也带歪了。
    按项目红线：**「没有数据」不能读成「性能为零」**，缺分就该显式标缺失。
    """
    perf = o.get("perf")
    price = o.get("price") or 1
    if mode == "carry":
        v = o.get("portable")
        return None if v is None else v * 33.3
    if mode == "future":
        v = o.get("upgrade")
        return None if v is None else v * 50.0
    if mode == "value":
        if perf is None:
            return None
        return round(perf / (price / 1000.0) * 10, 2)
    if mode == "game":
        if perf is None:
            return None
        return perf * (1.0 if o["cat"] == "gaming" else 0.5)
    return None if perf is None else float(perf)


def _tier(v, p33, p66):
    if v >= p66:
        return 2
    if v >= p33:
        return 1
    return 0


# 平局时用的加权总分（PRD §7.1 第 4 级）。
# 权重是人定的，按侧重区分；budget 是「预算利用率」——
# 不加这个维度会出现「预算 7000 却推 3000」的情况：预算只当上限，不当目标。
WEIGHTS = {
    "value":  {"perf": 0.30, "upgrade": 0.10, "portable": 0.05, "budget": 0.05, "ratio": 0.50},
    "daily":  {"perf": 0.38, "upgrade": 0.10, "portable": 0.07, "budget": 0.45, "ratio": 0.00},
    "work":   {"perf": 0.42, "upgrade": 0.13, "portable": 0.05, "budget": 0.40, "ratio": 0.00},
    "game":   {"perf": 0.45, "upgrade": 0.08, "portable": 0.05, "budget": 0.42, "ratio": 0.00},
    "carry":  {"perf": 0.18, "upgrade": 0.08, "portable": 0.42, "budget": 0.32, "ratio": 0.00},
    "future": {"perf": 0.24, "upgrade": 0.32, "portable": 0.04, "budget": 0.40, "ratio": 0.00},
}


def weighted_score(o, mode, budget):
    """平局时的加权总分。**缺项按比例重新归一，不判 0 分。**

    这是 prices.json 的 meta 里写死的口径：
      「内存条与固态没有 CPU，此项存 null、显示「不涉及」，并从加权里摘除后
        按比例重新归一，不判 0 分。」
    原来这里是 `(o.get("perf") or 0)`，等于把「没有数据」算成「性能为零」，
    和上面那句口径直接矛盾。实测 2026-11-11：淘宝 41 条 perf 全空，
    加权总分里 perf 那 0.38~0.45 的权重全被钉死成 0，它们永远不可能排上来 ——
    不是"数据不支持它排上来"，是"代码造假把它按死了"，两者性质完全不同。
    """
    w = WEIGHTS.get(mode, WEIGHTS["daily"])
    price = o.get("price") or 1
    perf_v = o.get("perf")
    up_v = o.get("upgrade")
    por_v = o.get("portable")

    parts = []          # (值, 权重) —— 只收真拿到数据的维度
    if perf_v is not None:
        per = perf_v / 100.0
        parts.append((per, w["perf"]))
        # 性价比：每千元买到的性能分，20 分/千元视为上限
        parts.append((min((perf_v / (price / 1000.0)) / 20.0, 1.0), w["ratio"]))
    if up_v is not None:
        parts.append((up_v / 2.0, w["upgrade"]))
    if por_v is not None:
        parts.append((por_v / 3.0, w["portable"]))
    if budget:
        # 预算利用率：离预算越近越高，但绝不奖励超预算（超预算已在第一级垫底）
        parts.append((min(price / budget, 1.0), w["budget"]))

    tw = sum(x[1] for x in parts)
    if not tw:                     # 一个维度都没有，加权总分无意义
        return 0.0
    return sum(v * wt for v, wt in parts) / tw


# 用途硬伤：数据里**明确写出来的**短板，按用途判是否致命。
#
# 为什么要有这个（2026-11-11 演示前实测）：
#   Q「学设计的，预算 7000，要带去教室」
#   → 首位推荐惠普战 66，而它自己的 cons 写着「屏幕只有 62.5% sRGB，色域是明显短板」。
#   数据早就说了这是短板，排序却只看 perf 和价格，把设计专业最要命的项当没看见。
#   演示时这是最容易被一眼看穿的一类错。
#
# 🔴 判据只认**文案里明说的负面表述**。「查不到色域」= 不知道，不是「色域差」——
#   没有数据的机型不能因此被扣分（这条和 main_score 返回 None 是同一个口径）。
#
# 🔴 v2 加了两级证据（2026-11-11 演示走查）：
#   「色域是明显短板」和「屏幕素质在同价位不算突出」不是一回事。
#   前者是数据明说这块不行，后者是"不突出但能用"。
#   如果把后者也当硬伤降权，等于替用户判了"这机器不能做设计"——
#   但库里写的是"不算突出"，不是"不能用"。
#   ⇒ 分两级：
#       hard   → 参与排序降权 + 必须先说
#       soft   → **不影响排序**，但必须写进回答（让用户知道这块不强）
#   这两级都不能靠"没写"来推断 —— 没写就是不知道。
BLOCKER_RULES = {
    "design": {
        "pat": re.compile(
            r"色域\s*(?:是|偏|只有|较)?[^，。；]{0,6}(?:短板|不足|一般|窄|差)"
            r"|(?:低|窄)色域"
            r"|色域[^，。；]{0,8}(?:发灰|偏淡|不够)"
            r"|只有\s*\d{2,3}(?:\.\d)?%\s*sRGB"
            r"|色准[^，。；]{0,6}(?:差|偏|不准)"),
        "why": "屏幕色域/色准是短板，做设计调色会偏",
        # 弱证据：说了"这块不强"，但没说"不能用"。不算硬伤，但要提。
        "soft": re.compile(
            r"屏幕[^，。；]{0,8}(?:素质|观感|表现)[^，。；]{0,8}"
            r"(?:不算突出|一般|普通|平庸|偏弱|不出彩)"
            r"|(?:屏幕|色域)[^，。；]{0,8}(?:不算|不是很)(?:好|强|出彩)"),
        "softWhy": "屏幕素质在同价位不算突出，做设计调色够用但不精细",
    },
    "video": {
        "pat": re.compile(
            r"色域\s*(?:是|偏|只有|较)?[^，。；]{0,6}(?:短板|不足|一般|窄|差)"
            r"|(?:低|窄)色域"
            r"|只有\s*\d{2,3}(?:\.\d)?%\s*sRGB"),
        "why": "剪片调色靠屏幕，色域窄会让成片颜色在别人机器上不对",
        "soft": re.compile(
            r"屏幕[^，。；]{0,8}(?:素质|观感|表现)[^，。；]{0,8}"
            r"(?:不算突出|一般|普通|平庸|偏弱|不出彩)"),
        "softWhy": "屏幕素质在同价位不算突出，调色要留意",
    },
    "gaming": {
        "pat": re.compile(r"(?:没有|无|不带)\s*独立显卡|纯核显|核显配置"),
        "why": "没有独显，游戏性能上不去",
    },
}


def purpose_blockers(o, purpose, include_soft=False, gpu_sort=True):
    """这台机器在**这个用途**下有没有数据明写的硬伤。

    只用于排序降权 + 如实告知，**不删机型** —— 用户预算只够这台时，
    它仍然该出现，只是必须把短板说在前面。删掉等于替他做了「宁可不买」的决定。

    include_soft=True 时把"弱证据"也算进来，标 level="soft"。
    弱证据**不参与排序**（那会把"不突出"当成"不能用"），只用于回答里必须提到。
    """
    p = purpose if isinstance(purpose, list) else ([purpose] if purpose else [])
    blob = " ".join([o.get("spec") or "", o.get("caution") or ""]
                    + list(o.get("pros") or []) + list(o.get("cons") or []))
    out = []
    for key in p:
        rule = BLOCKER_RULES.get(key)
        if not rule:
            continue
        m = rule["pat"].search(blob)
        if m:
            out.append({"purpose": key, "why": rule["why"],
                        "evidence": m.group(0), "level": "hard"})
            continue
        # 🔴 2026-11-12：结构化字段说这是核显 → 打游戏这条是硬伤。
        #   为什么不靠文案：MateBook 14 2026 的 spec 只写「Ultra 7 255H · 16G + 512G」，
        #   一个字没提显卡，但采集数据里明写 gpu.type="integrated"（Intel Arc 140T）。
        #   实测 2026-11-12 用户反馈：反复强调"要设计也要打游戏"，
        #   系统仍把 MateBook 14 推成首选，还说"这台不适合打游戏" —— 自相矛盾。
        #   注意这不违反"没写≠没有"：这里用的是**数据明确标注**的类型，不是缺席推断。
        if key == "gaming" and gpu_igpu(o):
            gm = o.get("gpuModel") or "核显"
            # ``sort`` 决定这条要不要参与降权，和"要不要说出口"是两件事：
            #   整个品类里**一台独显都没有**时，按"已知核显"降权等于惩罚数据更全的机型 ——
            #   人工层 22 台没写显卡字段，会因此排在核显机前面，这是拿缺失数据占便宜。
            #   这种情况只陈述、不排序，靠 _gaming_conflict_note 把话说在前面。
            out.append({"purpose": key,
                        "why": "显卡是 %s，核显，没有独立显卡，游戏性能上不去" % gm,
                        "evidence": "%s（采集标注：integrated）" % gm,
                        "level": "hard", "sort": bool(gpu_sort)})
            continue
        if include_soft and rule.get("soft"):
            m2 = rule["soft"].search(blob)
            if m2:
                out.append({"purpose": key, "why": rule["softWhy"],
                            "evidence": m2.group(0), "level": "soft"})
    return out


def rank_offers(offers, cat, budget, mode="daily", limit=5, purpose=None):
    """PRD §7.1 四级排序：超预算垫底 → 用途硬伤靠后 → 主维度三档 → 原始值 → 加权/id 兜底。

    缺主维度分的机型（main_score 返回 None）**不参与分位数计算**，单独排在最后，
    并打上 `_nodata` 标记供上层如实告知「这台查不到跑分，是按价格排进来的」。
    见 main_score 的注释：让缺分机器参与分位数会把有分机器的档位一起带歪。
    """
    pool = [dict(o) for o in offers if o["cat"] == cat]
    if not pool:
        return []
    # 这个预算内、这个品类里有没有"能打游戏"的机型。
    # 有 → 核显机该降权，让带独显的排前面；
    # 没有 → 降权只会让"数据没写显卡"的机型占便宜（人工层没这个字段），
    #        而且排出来的顺序对用户没有意义 —— 只陈述，不排序。
    cat_has_dgpu = any(has_dgpu(x) and (x.get("price") or 0) <= budget * 1.05
                       for x in pool)
    for o in pool:
        o["_main"] = main_score(o, mode)
    # 只有真拿到分的才进分位数。一台都没有时，分档没有意义，全部按 tier 0 处理，
    # 靠加权总分和价格兜底 —— 这时也绝不能把"算不出来"当成"最差"。
    scored = sorted(o["_main"] for o in pool if o["_main"] is not None)
    n = len(scored)
    if n:
        p33 = scored[max(0, int(n * 0.33) - (1 if n > 3 else 0))]
        p66 = scored[min(n - 1, int(n * 0.66))]
    else:
        p33 = p66 = 0.0
    for o in pool:
        o["_nodata"] = 1 if o["_main"] is None else 0
        o["_tier"] = 0 if o["_main"] is None else _tier(o["_main"], p33, p66)
        o["_over"] = 1 if (o.get("price") or 0) > budget * 1.05 else 0
        o["_weight"] = weighted_score(o, mode, budget) if budget else 0.0
        # 用途硬伤：数据明写的短板 → 靠后，但仍留在候选里（见 purpose_blockers 注释）
        # 这里要 include_soft=True，把"屏幕素质不算突出"这类弱证据也拿回来 ——
        # 但排序只看 level=="hard" 的，软证据不降权（见下面的 _block）。
        o["_blockers"] = purpose_blockers(o, purpose, include_soft=True,
                                          gpu_sort=cat_has_dgpu) if purpose else []
        # 只有 hard 才参与降权。soft 是"不强但能用"，拿它降权等于替用户判了不能用。
        # sort=False 的 hard 只负责"必须说出口"，不动排序（见 purpose_blockers 里核显那条）。
        o["_block"] = 1 if any(b.get("level") == "hard" and b.get("sort", True)
                               for b in o["_blockers"]) else 0
    # 五级排序，按 PRD §7.1 扩展：超预算垫底 → 用途硬伤靠后 → 主维度三档 →
    # 主维度原始值 → 加权总分 → id 兜底。
    # `_nodata` 放在第一位：查不到跑分的机型哪怕在预算内，也不能挤掉有据可查的那台。
    # `_block` 放在 `_over` 之后：超预算的事实优先于用途短板 ——
    # 预算不够时该说的是「这台超预算」，而不是「这台色域差」。
    pool.sort(key=lambda o: (o["_nodata"], o["_over"], o["_block"], -o["_tier"],
                             -(o["_main"] if o["_main"] is not None else 0),
                             -o["_weight"], o["id"]))
    return pool[:limit]


GPU_WORDS = ["RTX", "GTX", "RX 6", "RX 7", "RX 9", "MX5", "Arc A", "独显"]


def ram_of(o):
    """从 spec 里取内存容量（GB）。spec 形如 "U5-338H · 32G + 1TB（首销 7999）"。
    取不到返回 0，让硬约束把它筛掉而不是猜。"""
    import re
    m = re.search(r"(\d{1,3})\s*G\s*\+", o.get("spec") or "")
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{1,3})\s*G\b", o.get("spec") or "")
    return int(m.group(1)) if m else 0


def has_dgpu(o):
    # gpuKind 来自采集池的结构化字段；spec 里的型号是第二道判据（人工层没有 gpu 字段）
    if o.get("gpuKind") == "discrete":
        return True
    return any(w in (o.get("spec") or "") for w in GPU_WORDS)


def gpu_igpu(o):
    """数据**明确写着**这是核显。

    ⚠️ 没这个字段（人工层 22 台）返回 False —— 不能把"没写"当"没有"。
    这是 test_attack8 里专门立过对照的原则：京东页面漏标显卡很常见，
    "spec 里没 RTX" 不等于"没有独显"。
    """
    return o.get("gpuKind") == "integrated"


def fit_notes(o, purpose):
    """机型与用途是否匹配。只陈述库里真实有的字段。
    数据不支撑的部分（帧数、GPU 跑分）明确说"没有"，不用常识补 ——
    这是 2026-11-11 用户抓到"编造保修信息"的同一条红线。
    返回 (verdict, notes)：verdict 是结论，notes 是依据。
    verdict: "match" 匹配 / "partial" 部分匹配 / "mismatch" 不匹配 / None 判断不了"""
    p = purpose if isinstance(purpose, list) else [purpose]
    p = p or []
    notes = []
    verdict = None

    if "gaming" in p:
        if o.get("cat") == "gaming":
            verdict = "match"
            notes.append("这台本身就是游戏本，按游戏配的机器")
        elif has_dgpu(o):
            verdict = "partial"
            notes.append("有独立显卡，但不是游戏本的散热规格")
        elif gpu_igpu(o):
            # 数据明确标注核显 → 这句话有出处，不是从"没写 RTX"推出来的
            verdict = "mismatch"
            notes.append("显卡是 %s（核显），没有独立显卡，这是轻薄本的配置，不是游戏本"
                         % (o.get("gpuModel") or "核显"))
        else:
            verdict = "mismatch"
            notes.append("规格里没有独立显卡，这是纯轻薄本的配置，不是游戏本")
        notes.append("库里没有游戏帧数数据，具体能跑什么帧数我说不准")

    if "design" in p:
        blob = " ".join((o.get("pros") or []) + (o.get("cons") or []) + [o.get("spec") or ""])
        has_screen = any(w in blob for w in ["色准", "色域", "sRGB", "2.5K", "3.2K", "1080p", "屏"])
        if has_screen and verdict != "mismatch":
            verdict = verdict or "partial"
            notes.append("屏幕参数库里有一点，好坏都写在优点缺点里")
        elif not has_screen:
            notes.append("库里没有这台的屏幕色域数据")

    if "video" in p or "dev" in p:
        # 剪视频/写代码真正吃的是 CPU 和内存，不是"能不能加装"。
        # 之前只说了句"内存可加装"，对「能跑得动 PR 吗」等于没答。
        blob = " ".join((o.get("pros") or []) + (o.get("cons") or []) + [o.get("spec") or ""])
        if "video" in p:
            if re.search(r"[iR]\s*[579]\s*-?\s*\d{4}", o.get("spec") or "") or \
               re.search(r"(Ultra|HX|H\d{3})", o.get("spec") or ""):
                verdict = verdict or "partial"
                notes.append("CPU 型号属于能跑剪辑的那一档，但库里没有实际导出速度数据")
            else:
                notes.append("库里没有这台的视频编码性能数据")
            if (o.get("upgrade") or 0) >= 1:
                notes.append("内存可以后期加装，素材多的时候有用")
        if "dev" in p:
            if (o.get("upgrade") or 0) >= 1:
                notes.append("内存或硬盘可以后期加装，跑虚拟机和容器够灵活")
            else:
                notes.append("内存焊死，跑虚拟机要一次买够")
    return verdict, notes


def get_offer(oid, data=None):
    """按 id 取单台机器。焦点机制用。

    🔴🔴 2026-11-12：必须查**合并池**，不能只查 load() 那 22 台。
      实测事故（端到端彩排）：
        推荐 ⇒「MateBook 14 2026」→ focus = 'add-matebook14-2026'
        追问 ⇒「它的屏幕怎么样」
        系统 ⇒ `get_offer()` 在 22 台里找不到这个 id，返回 None
             ⇒ 后面所有判据拿到的是 m=None
             ⇒ praise / foreign / stretch 全部失效
             ⇒ 模型对着空数据自由发挥，输出了
               「最大的亮点是那块 OLED，分辨率高达 2880×1920，
                 看着非常细腻通透」「定位就是高端商务本」「保值率也就中等」
      这不是"少答一句"，是**整套防线在 focus 上全部失效**。
      focus 是采集池那 75 台中的任意一台时，追问都会落到这个坑里。

    ⚠️ data 显式传入时（测试切源）保持原行为：只查那一份。
    """
    if not oid:
        return None
    if data is not None:
        for o in data.get("offers") or []:
            if o.get("id") == oid:
                return o
        return None
    for o in merged_offers():
        if o.get("id") == oid:
            return o
    return None


def brand_info(o):
    """按品牌取品牌级信息。

    🔴 必须按 scope 过滤（2026-11-11 演示前实测发现的真 bug）：
    `brands` 里同名品牌会出现两次，一次 scope="pc"，一次 scope="phone"：
        华为  (7, pc) / (10, phone)
        红米  (8, pc) / (12, phone)
        荣耀  (6, pc) / (13, phone)
        苹果  (9, pc) / (20, phone)
    原来写成 `{x["name"]: x for x in brands}` 的字典推导 —— 后出现的手机版
    把 PC 版覆盖掉了。后果：用户问「华为 MateBook D16 怎么样」，
    回答里冒出「Mate 90 Pro 同配置比上代高 1500」—— 那是**手机**的备注。
    演示时这是最尴尬的一类：说得头头是道，但说的不是这台东西。

    取不到 pc 版时退回第一条同名记录，并如实返回 None 让上层知道"没这份资料"。
    """
    brand = (o or {}).get("brand")
    if not brand:
        return {}
    d = load()
    cands = [b for b in (d.get("brands") or []) if b.get("name") == brand]
    if not cands:
        return {}
    for b in cands:
        if b.get("scope") == "pc":
            return b
    return cands[0]


def brief(o, purpose=None):
    """单台的完整视图，供"追问某一台"使用。数字全部原样，不做运算。"""
    if not o:
        return None
    sub = subsidy_of(o.get("price"))
    b = brand_info(o)
    verdict, notes = fit_notes(o, purpose) if purpose else (None, [])
    return {
        "id": o["id"], "name": o["name"], "price": o.get("price"),
        "spec": o.get("spec"), "cat": o.get("cat"),
        "tag": o.get("tag"), "caution": o.get("caution"),
        "pros": o.get("pros", []), "cons": o.get("cons", []),
        "brand": o.get("brand"),
        "brandService": b.get("service"),
        "brandResale": b.get("resale"),
        "brandNote": b.get("note"),
        "subsidy": sub,
        "fitVerdict": verdict,
        "fit": notes,
        "perf": o.get("perf"), "upgrade": o.get("upgrade"),
        "portable": o.get("portable"),
        # 🔴 2026-11-13 补：这条路径原来**完全不带重量**，于是追问「这台多重」时
        #    右栏拿到的是"查不到"，而正文那边可能正说着「1.48kg 偏重」。
        #    同一事实在三条出口（brief / build_recommendation / answer_card）
        #    必须同源，否则就是四个地方各写一遍判据的老毛病。
        "weightKg": effective_weight(o),
        # 这三项为 null 时，上层要说明「查不到」，不能沉默 —— 沉默会让用户
        # 以为这台跑分低，实际上是没有数据（2026-11-11 淘宝入库暴露的同类问题）。
        "noPerfData": o.get("perf") is None,
        "origin": o.get("origin", "jd"),
    }


def subsidy_of(price):
    """prices.json 的 price 是到手价(已含国补)，这里反推原价与补贴额。
    电脑归家电类：15% 立减、单台封顶 1500、无价格门槛。"""
    if price is None:
        return None
    if price * 0.15 / 0.85 <= 1500:
        original = price / 0.85
        sub = original - price
    else:
        original = price + 1500
        sub = 1500
    return {"paid": price, "original": round(original), "subsidy": round(sub)}


def ask_expert(slots):
    """必问五问，代码判断，不调模型。顺序即优先级。"""
    qs = []
    if slots.get("cat") == "gaming" and slots.get("dormWatt") is None:
        qs.append({"id": "Q2", "q": "宿舍限电吗？大概多少瓦",
                   "why": "游戏本满载会跳闸，有些学校限 500W"})
    if not slots.get("purpose"):
        qs.append({"id": "Q1", "q": "专业定了吗？要跑什么软件",
                   "why": "决定要独显还是大内存"})
    if slots.get("carry") is None:
        qs.append({"id": "Q3", "q": "要带去教室，或者寒暑假带回家吗",
                   "why": "影响便携和续航的权重"})
    if slots.get("years") is None:
        qs.append({"id": "Q5", "q": "四年内打算换吗",
                   "why": "不换就要优先后期可加装"})
    return qs


def missing_required(slots):
    """必备项：品类、预算、用途。缺了就不能给确定性推荐。
    「越便宜越好」不算缺预算 —— 用户的诉求就是"最便宜的那台"，追问反而是没听懂。"""
    miss = []
    if not slots.get("cat"):
        miss.append("品类")
    if not slots.get("budget") and not slots.get("_cheapest"):
        miss.append("预算")
    if not slots.get("purpose"):
        miss.append("用途")
    return miss


def ready_for_recommendation(slots):
    return not missing_required(slots)


def clean_caution(caution, m):
    """「要提醒的坑」里如果讲的是**别的型号**，不能当这台的缺点说出去。

    🔴 2026-11-12 从 agent 迁到内核：原来只在提示词那一层清洗，
      接口仍然把原文透给了前端，卡片上真的出现了
      「小新 16SE 有双 M.2 硬盘位，同价位更值得看」——
      模型没说错，是**数据直接展示**错了。
      清洗必须在出口数据上做一次，前端才没机会看见。

    判据只能认「机型名」，不能认「规格代号」（LPDDR5 / RTX 5050 / DDR4 都是规格）。
    只有"字母+数字"这个块**在库内别的机型名里出现过**，才算在说另一台。
    """
    c = (caution or "").strip()
    if not c:
        return ""
    name = (m or {}).get("name") or ""
    try:
        others_models = set()
        for o in load().get("offers") or []:
            if o.get("id") == (m or {}).get("id"):
                continue
            for x in re.findall(r"[A-Za-z]{1,6}\s?\d{1,4}(?:SE|Pro|Plus|Max|Air)?"
                                r"|\d{2}\s?(?:SE|Pro|Plus)", o.get("name") or ""):
                others_models.add(x.replace(" ", "").lower())
    except Exception:
        others_models = set()
    if not others_models:
        return c

    name_l = name.replace(" ", "").lower()
    keep = []
    for seg in re.split(r"[，。；]", c):
        seg = seg.strip()
        if not seg:
            continue
        hits = [x.replace(" ", "").lower() for x in
                re.findall(r"[A-Za-z]{1,6}\s?\d{1,4}(?:SE|Pro|Plus|Max|Air)?"
                           r"|\d{2}\s?(?:SE|Pro|Plus)", seg)]
        alien = [h for h in hits if h in others_models and h not in name_l]
        if alien and not any(h in name_l for h in hits):
            continue
        if alien and re.search(r"更值得|更划算|更推荐|不如买|推荐买", seg):
            continue
        stripped = seg
        for h in hits:
            stripped = re.sub(re.escape(h), "", stripped, flags=re.I)
        # 剥掉型号后只剩比较语的空壳，也要丢（否则挂在当前机型身上等于自夸）
        if (len(stripped.strip(" ，。；的")) <= 12
                and re.search(r"更值得|更划算|更推荐|不如买|推荐买|更好", seg)):
            continue
        keep.append(seg)
    return "，".join(keep).strip("，。； ")


def build_recommendation(slots, data=None, exclude=None):
    """内核对外出口。返回结构化结果，数字全部可追溯。
    exclude: 已推荐过的机型 id，用于「换一台」时不重复给同一台。

    🔴 排序池从 v0.4 起是 **merged_offers()**（人工层 22 + 采集层 78 = 100 台），
    不再是只用人工那一层。见 merged_offers 的注释。

    ⚠️ `data` 显式传入时（测试切源）**不并采集池** ——
    否则「只用淘宝源」的测试里混进 78 台京东机，测的就不是淘宝源了。
    """
    # 必须用 is None 判断，不能写 data or load()：
    # 空 dict 也是"显式传入"，dunder or 会把它换成默认源。
    explicit = data is not None
    d = data if explicit else load()
    offers = merged_offers(d, with_pool=False if explicit else True)
    merge = merge_stats()
    out = {
        "slots": slots,
        "mode": None,
        "ranked": [],
        "missing": missing_required(slots),
        "questions": ask_expert(slots),
        "dataAsOf": data_as_of(d),
        "warnings": [],
        "refuse": None,
        # 池子构成：上层要能说出"这 100 台里 78 台只有参数没有评价"
        "pool": {"total": merge.get("total", len(offers)),
                 "reviewed": merge.get("base"),
                 "noReview": merge.get("poolNoReview"),
                 "priceConflicts": merge.get("priceConflicts") or []},
    }
    cat = slots.get("cat")
    if cat not in ALLOWED_CATS:
        out["refuse"] = "品类不在覆盖范围" if cat else "品类未确定"
        return out

    out["mode"] = mode_of(slots)
    budget = slots.get("budget") or 99999

    # 🔴 要独显是硬约束，不是偏好（2026-11-12 16:34 真实演示彩排抓到的最重错误）：
    #   用户（AI 专业，问过跑本地大模型）说「我想要带独立显卡的」，
    #   模型捏造 purpose=gaming，内核在 thin 里照排，首选仍是无独显的 ThinkBook 16+，
    #   回答还断言「同品类里带独显的只有拯救者 R7000P ¥12999」——
    #   实际游戏本品类 ¥8000 内有 3 台独显机（极光X 6159 / 蛟龙15K 6499 / 暗影精灵11 6687）。
    #   规则：用户要独显 + 当前品类预算内一台独显都没有 + 游戏本里有 →
    #   **说明原因后切游戏本重排**。这不是"悄悄换品类"：原因写在回答第一句（agent 侧生成），
    #   数据支撑明确（独显只存在于游戏本），且是用户主动提出的硬件要求。
    if (slots.get("_wantDgpu") and slots.get("cat") == "thin"):
        _thin_dg = [o for o in offers if o.get("cat") == "thin" and o.get("price")
                    and o["price"] <= budget * 1.05 and has_dgpu(o)]
        if not _thin_dg:
            _gaming_dg = sorted([o for o in offers
                                 if o.get("cat") == "gaming" and o.get("price")
                                 and o["price"] <= budget * 1.05 and has_dgpu(o)],
                                key=lambda o: o["price"] or 0)
            if _gaming_dg:
                slots = dict(slots)
                slots["cat"] = "gaming"
                cat = slots["cat"]
                out["dgpuCatSwitch"] = True
                out["dgpuBudgetModels"] = [
                    {"name": o["name"], "price": o["price"]} for o in _gaming_dg[:3]]
                out["slots"] = slots
                out["mode"] = mode_of(slots)
    ranked = rank_offers(offers, cat, budget, out["mode"], limit=12,
                          purpose=slots.get("purpose"))

    # 换一台：把已经看过的剔掉，剩下的按原排序继续
    if exclude:
        ranked = [o for o in ranked if o["id"] not in exclude]
        out["excluded"] = [o["name"] for o in offers if o.get("id") in exclude]

    # 硬性排除条件：用户明确说「不要封装内存」，就必须把 upgrade=0 的剔掉，
    # 而不是只降权。否则会出现"一边说不要封装，一边推封装机型"。
    mu = slots.get("_minUpgrade")
    if mu is not None:
        before = len(ranked)
        ranked = [o for o in ranked if (o.get("upgrade") or 0) >= mu]
        if before and not ranked:
            out["warnings"].append("这个条件下没有符合「可加装内存」要求的机型，已放宽")

    # 内存容量下限：「要 32G 内存」。spec 里写着 16G + 512G 这种格式。
    mr = slots.get("_minRam")
    if mr:
        before = len(ranked)
        ranked = [o for o in ranked if ram_of(o) >= mr]
        if before and not ranked:
            out["warnings"].append(
                "这个预算下没有 %dG 内存的机型，最接近的是 "
                "%dG，已放宽" % (mr, max((ram_of(x) for x in rank_offers(
                    offers, cat, budget, out["mode"], limit=99,
                    purpose=slots.get("purpose"))), default=0)))

    # 品牌排除：「不要荣耀」「除了荣耀其他都行」
    exb = slots.get("_excludeBrand") or []
    if exb:
        before = len(ranked)
        ranked = [o for o in ranked
                  if not any(b and b in (o.get("brand") or "") for b in exb)]
        if before and not ranked:
            out["warnings"].append("排除 %s 后没有别的机型了，已放宽" % "/".join(exb))

    # 预算内优先：只要有预算内的机型，就不把超预算的混进推荐列表。
    # 预算 3000 时池里只有 1 台符合，此时才允许带出超预算的，并保留 overBudget 标记。
    # 记下缩池原因，兜底文案必须带上条件 —— 否则用户会看到自相矛盾：
    # 实测 2026-11-11「2000 要 32G」，同一品类里明明有 3000 的，
    # 文案却说"轻薄本里一台都买不到，最便宜 6799"，因为 6799 是"筛掉小内存后"的最低价。
    narrowed = []
    if mu is not None:
        narrowed.append("可加装内存")
    if mr:
        narrowed.append("%dG 内存" % mr)
    if exb:
        narrowed.append("排除 " + "/".join(exb))

    fits = [o for o in ranked if not o["_over"]]
    if fits:
        ranked = fits
    elif ranked:
        # 一台都不在预算内 —— 这时绝不能按"性能最强"往外推最贵的那台。
        # 实测 2026-11-11：预算 3000 要游戏本，推出 ¥12124 的苍龙 18 Pro，
        # 还说"轻松跑满 3A"。用户看到的是"我要 3000 的，你给我 12000"。
        # 正确行为：按价格从低到高，给最接近预算的，并明确说清超了多少。
        cheapest = min(o["price"] for o in ranked)
        ranked = [o for o in ranked if o["price"] == cheapest] + \
                 sorted([o for o in ranked if o["price"] != cheapest],
                        key=lambda o: o["price"])
        scope = CAT_NAMES.get(cat, cat)
        if narrowed:
            scope += "（" + "、".join(narrowed) + "）"
        out["warnings"].append(
            "预算 %d 在%s里一台都买不到，最便宜的是 ¥%d，超预算 ¥%d"
            % (budget, scope, cheapest, cheapest - budget))
    ranked = ranked[:3]

    # 🔴 用途 vs 品类冲突（2026-11-12 用户实测反馈，不是措辞问题）：
    #   用户先说「学设计」→ cat=thin，后说「我还想打打游戏」→ purpose 加 gaming，
    #   但 cat 仍是 thin。内核在 thin 里照排，第一名是没有独显的 MateBook 14，
    #   页面一边把它当首选，一边在追问里答「这台不适合打游戏」——
    #   用户的原话是"我强调了我要设计和打游戏，他依旧给我推荐 matebook"。
    #   这里只**把冲突标出来**，不换品类：换品类必须问用户（见 agent 的 16:27 注释）。
    pur = slots.get("purpose") or []
    if "gaming" in pur and cat not in ("gaming", "desktop"):
        in_budget = [o for o in offers
                     if o["cat"] == cat and o.get("price")
                     and o["price"] <= budget * 1.05 and has_dgpu(o)]
        if not in_budget:
            out["noGamingInCat"] = True
            # 给个着落：同品类里最便宜的独显机（可能在预算外），用来回答"那要多少钱"
            all_dg = sorted([o for o in offers
                             if o["cat"] == cat and o.get("price") and has_dgpu(o)],
                            key=lambda o: o["price"])
            if all_dg:
                out["noGamingHint"] = {"name": all_dg[0]["name"],
                                       "price": all_dg[0]["price"]}

    # 不能写成 {b["name"]: b} —— 同名品牌有 pc 和 phone 两份，
    # 后出现的手机版会覆盖 PC 版（见 brand_info 的注释）。
    for o in ranked:
        sub = subsidy_of(o.get("price"))
        b = brand_info(o)
        out["ranked"].append({
            "id": o["id"], "name": o["name"], "price": o.get("price"),
            "spec": o.get("spec"), "perf": o.get("perf"),
            "upgrade": o.get("upgrade"), "portable": o.get("portable"),
            "tag": o.get("tag"),
            # ⚠️ 出口清洗：caution 里讲别的机型的部分不能出现在前端卡片上
            "caution": clean_caution(o.get("caution"), o),
            "pros": o.get("pros", []), "cons": o.get("cons", []),
            "brand": o.get("brand"),
            "brandService": b.get("service"),      # 售后网点分 1-5
            "brandResale": b.get("resale"),        # 保值率分 1-5
            "brandNote": b.get("note"),
            "subsidy": sub,
            "sources": o.get("sources", [])[:2],
            "main": o["_main"], "tier": o["_tier"], "overBudget": bool(o["_over"]),
            # 主维度分是 null 说明库里没有这一项的实测数据。必须透传出去，
            # 上层要如实说「这台查不到跑分」，不能让它悄悄按最低分排进来。
            "noPerfData": bool(o.get("_nodata")),
            # 这个用途下的硬伤（数据明写的短板）。上层必须把它说在推荐理由**前面**，
            # 不能只夸优点 —— 见 purpose_blockers 的注释。
            "blockers": o.get("_blockers") or [],
            # 实测重量（kg），采集池才有；None 表示库里没记，不是"很轻"
            # 🔴 effective_weight：结构化字段优先，pros/cons 里的数字兜底 ——
            #    否则字段卡说"没记"、正文说"1.48kg 偏重"，同一回答两种话。
            "weightKg": effective_weight(o),
            # 内存容量（GB），用来对照专业要求表里的 ramMin / ramRec
            "ram": ram_of(o),
            "origin": o.get("origin", "jd"),
            # 🔴 这台有没有人写过优缺点。False 表示它来自采集池：
            # 参数和价格是真的，但 pros/cons 是空的 —— 上层只能说参数，
            # 绝不许把参数换个说法当优点讲（那是编造，见 merged_offers 注释）。
            "hasReview": bool(o.get("hasReview")),
            # 采集层的价格推导链（划线价→券→国补→到手），供"这价怎么来的"追溯
            "priceBasis": o.get("priceBasis"),
        })

    if cat == "desktop" and len(ranked) < 3:
        out["warnings"].append("台式样本量不足 3 台，只列清单不做排序")

    # 扩充候选（京东池不够时，从淘宝补）。
    # **只作补充提示，不参与排序** —— 淘宝那批没有 perf 分，排不了；
    # 而且它们没有得到京东那样的核验（价格口径、配置一致性都靠采集方的 note）。
    # 严格过滤后现在只剩 1 条（见 extra_stats），基本等于没有 —— 这是数据本身的样子，
    # 不为了"看起来有双源"而放宽标准。
    extra = [o for o in extra_candidates(cat)
             if o.get("price") and (o.get("price") or 0) <= budget * 1.05]
    if extra and len(ranked) < 3:
        out["extras"] = [{
            "id": o["id"], "name": o["name"], "price": o.get("price"),
            "spec": o.get("spec"), "ram": o.get("ram"), "disk": o.get("disk"),
            "extSrc": "taobao",
            "extWhy": o.get("extWhy"),
            "hasPerf": False,
        } for o in extra[:2]]
    known_prices = [o.get("price") or 0 for o in offers if o["cat"] == cat and o.get("price")]
    if known_prices:
        top = max(known_prices)
        if budget != 99999 and budget > top * 1.15:
            out["warnings"].append(
                "%s里在库最贵的一台是 ¥%d，预算还有余量" % (CAT_NAMES[cat], top))
    if slots.get("cat") == "gaming" and slots.get("dormWatt") and slots["dormWatt"] < 600:
        out["warnings"].append("宿舍限电 %dW，游戏本满载可能跳闸" % slots["dormWatt"])
    if not ranked:
        out["refuse"] = "该品类下没有符合条件的机型"
    return out
