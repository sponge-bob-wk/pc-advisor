# -*- coding: utf-8 -*-
"""
跨源机型匹配：把淘宝的标题式长串对到京东的规范机型名上。

为什么必须单独做这一步：
    淘宝的 name 是「MagicBook Pro 14 极客版 酷睿Ultra」这种标题，
    京东的是「Redmi Book Pro 14」。字符串相似度没有意义，
    唯一能对上的锚点是**系列名 + 关键配置**。

匹配原则（宁缺勿滥）：
    1. 只在同一品类内匹配（gaming 不会匹配到 thin）
    2. 系列名必须能对上（去空格/大小写归一后包含关系）
    3. 匹配不上就标 None，**绝不硬凑** —— 硬凑一条错的比少一条严重得多
"""
import re
import json
import core


def norm(s):
    """去空格、去常见修饰词、统一大小写和全角。"""
    s = str(s or "").lower()
    s = s.replace("　", " ")
    s = re.sub(r"\s+", "", s)
    s = s.replace("／", "/").replace("（", "(").replace("）", ")")
    return s


# 京东机型名里的「品牌 + 系列」，用来做锚点
def anchors(name):
    """从机型名里抽锚点。返回一组可能的匹配词。"""
    n = norm(name)
    out = set()
    # 直接全名
    out.add(n)
    # 去掉品牌前缀
    for pre in ("机械革命", "联想", "戴尔", "华硕", "华为", "荣耀", "惠普",
                "redmibook", "redmi", "xiaomi", "小米"):
        if n.startswith(pre):
            out.add(n[len(pre):])
    # 序列号类（14x / p16 / 18 pro）也算锚点
    for m in re.finditer(r"[a-z]+\d+[a-z]*|p\d+|x\d+", n):
        if len(m.group(0)) >= 2:
            out.add(m.group(0))
    return {x for x in out if len(x) >= 3}


# 实测 2026-11-11：第一版锚点太松，出了这种误匹配 ——
#   淘宝「MagicBook Pro 14 极客版」(荣耀) ←→ 京东「Redmi Book Pro 14」(小米)
# 原因：锚点抽出了通用词 "bookpro14"，把两个品牌的产品连在一起。
# 这类误匹配直接拿去比价，会报出 -1002 元的假差价，比"对不上"危险得多。
#
# 修法：抽掉品牌名之后再比对，**品牌不同一律不匹配**；
# 且锚点里含通用词（book/fit/pro/plus/air/轻薄 这类）的，要求系列名整体一致。
BRAND_TOKENS = {
    "荣耀": ("magicbook", "honor", "荣耀"),
    "小米": ("redmi", "xiaomi", "小米"),
    "联想": ("小新", "thinkbook", "thinkpad", "拯救者", "yoga", "拯救者",
             "legion", "联想", "geekpro"),
    "华硕": ("天选", "rog", "无畏", "幻", "枪神", "魔霸", "华硕", "vivobook"),
    "惠普": ("战", "暗影精灵", "光影精灵", "星book", "惠普", "omen", "hp"),
    "机械革命": ("机械革命", "无界", "极光", "耀世", "苍龙", "隐星", "蛟龙"),
    "戴尔": ("灵越", "游匣", "戴尔", "inspiron", "dell", "xps"),
    "华为": ("matebook", "华为"),
    "宏碁": ("非凡", "暗影骑士", "宏碁", "acer"),
    "神舟": ("战神", "神舟"),
    "机械师": ("机械师",),
    "雷神": ("雷神",),
}

GENERIC = ("book", "pro", "plus", "air", "max", "neo", "轻薄", "锐龙", "酷睿",
           "英特尔", "电竞", "游戏")


def brand_of(text):
    """从任意文本里认出品牌，认不出返回 None。"""
    t = norm(text)
    for brand, toks in BRAND_TOKENS.items():
        if any(tok in t for tok in toks):
            return brand
    return None


def match_one(tb_offer, jd_offers):
    """给一条淘宝记录找京东里的对应机型。返回 (jd_offer, 匹配词) 或 (None, None)。"""
    t_name = norm(tb_offer.get("name"))
    t_series = norm(tb_offer.get("series"))
    t_cat = tb_offer.get("cat")
    t_brand = brand_of(tb_offer.get("name")) or brand_of(tb_offer.get("brand"))

    best = None
    for j in jd_offers:
        if j.get("cat") != t_cat:
            continue
        # 品牌必须一致，否则不是同一台机器
        j_brand = brand_of(j.get("name"))
        if t_brand and j_brand and t_brand != j_brand:
            continue
        for a in anchors(j.get("name")):
            if a in t_name or a in t_series:
                # 锚点全是通用词（bookpro14）的，不可信，跳过
                if all(g in a for g in ["book"]) and len(a) < 12:
                    continue
                if best is None or len(a) > len(best[1]):
                    best = (j, a)
    return best if best else (None, None)


def main():
    jd = [o for o in core.load("jd")["offers"] if o.get("cat") in core.ALLOWED_CATS]
    tb = core.load("taobao")["offers"]

    stats = {"total": len(tb), "matched": 0, "no_match": 0, "price_ok": 0}
    rows = []
    for t in tb:
        j, a = match_one(t, jd)
        if j:
            stats["matched"] += 1
            if t.get("priceOk"):
                stats["price_ok"] += 1
            rows.append({
                "tb_id": t["id"], "tb_name": t["name"], "tb_price": t["price"],
                "tb_ok": t.get("priceOk"), "tb_q": t.get("priceWhy") or "",
                "jd_id": j["id"], "jd_name": j["name"], "jd_price": j["price"],
                "anchor": a, "cat": t.get("cat"),
                "diff": (round(t["price"] - j["price"], 2)
                         if t.get("price") and j.get("price") else None),
            })
        else:
            stats["no_match"] += 1

    print("=" * 68)
    print("淘宝总条数        : %d" % stats["total"])
    print("能对上京东机型    : %d  (%.0f%%)" % (
        stats["matched"], 100.0 * stats["matched"] / stats["total"]))
    print("其中价格口径可信  : %d" % stats["price_ok"])
    print("对不上的          : %d" % stats["no_match"])
    print()
    print("--- 匹配样例（按品类）---")
    for cat in ("thin", "gaming", "desktop"):
        sub = [r for r in rows if r["cat"] == cat][:5]
        if not sub:
            continue
        print("[%s]" % cat)
        for r in sub:
            d = "" if r["diff"] is None else (" 差价 %+d" % r["diff"])
            flag = "" if r["tb_ok"] else "  ⚠口径存疑"
            print("  淘宝 %-26s ¥%-8s ←→ 京东 %-20s ¥%-7s%s%s" % (
                r["tb_name"][:24], r["tb_price"], r["jd_name"][:18], r["jd_price"], d, flag))

    print()
    print("--- 对不上的样例（说明为什么对不上）---")
    shown = 0
    for t in tb:
        if any(r["tb_id"] == t["id"] for r in rows):
            continue
        j, _ = match_one(t, jd)
        if j:
            continue
        print("  [%s] %s" % (t.get("cat"), t["name"][:40]))
        shown += 1
        if shown >= 8:
            break

    with open("match_result.json", "w", encoding="utf-8") as f:
        json.dump({"stats": stats, "rows": rows}, f, ensure_ascii=False, indent=2)
    print("\n明细已写 match_result.json")


if __name__ == "__main__":
    main()