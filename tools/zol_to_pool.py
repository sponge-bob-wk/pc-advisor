# -*- coding: utf-8 -*-
"""把 ZOL 采集结果转成 machine_pool 的字段结构（2026-11-16）

## 为什么要单独一步，而不是采集时直接写成池格式

`machine_pool.json` 是**下游已经在用**的结构（`core.py` 读它做推荐），
字段是嵌套的（`cpu.model` / `screen.widthPx` / `price.final`）。
而 ZOL 采回来的是**扁平**的原始参数（`cpu` / `screenRes` / `priceJD`）。

如果让采集器直接产出池格式，会有两个问题：
  ① 采集器一改（比如 ZOL 改版），下游结构跟着震 —— 两边耦死
  ② 采到的原始参数被压扁后就丢了，以后补字段得重新爬一遍

所以分成两步：
    `zol_fetch.py`  → 原始参数（保真，扁平，一行一台）
    `zol_to_pool.py` → 池格式（转换，可反复重跑，不重新爬）

## 去重策略（这里最容易出事，写清楚）

现有池 80 台的名称是人工整理的（"拯救者 Y9000P 2026"），
ZOL 的名称是页面标题（"惠普HyperX 暗影精灵 Pro"），**格式不同**，
直接字符串比会全部判成"不重复"，于是同一台机器进池两次。

做法分三层：
  ① 规范化完全相等 → 判重（去掉空格/大小写/年份/后缀词）
  ② 品牌相同 + 规范化名称互相包含 → 判重，但**记为"疑似"**，列出来给人看
  ③ 其余 → 新增

🔴 只对 ① 做自动合并；② 只报告不合并。
   理由：误合并会**丢掉一台真实的机器**，而多留一台只是数据冗余。
   这个项目的红线是"宁可弃权不许自信地做错"，套在这里就是"宁可留着不许误删"。

## 用法

    python zol_to_pool.py                          # 统计，不写文件
    python zol_to_pool.py --write                  # 写 data/zol/pool_additions.json
    python zol_to_pool.py --write --apply          # 直接并进 machine_pool.json（会先备份）
"""
import argparse
import glob
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ZOL_DIR = os.path.join(ROOT, "data", "zol")
POOL = os.path.join(ROOT, "data", "machine_pool.json")

# 名称规范化：去掉这些以后才比
_DROP = re.compile(
    r'(20\d{2}\s*款?|酷睿版|锐龙版|旗舰版|超竞版|至尊版|游戏本|轻薄本|笔记本|'
    r'参数|报价|英寸?|"|"|\s+|\(|\)|（|）|【|】)', re.I)


# ── 品牌识别 ──────────────────────────────────────────────────────
# 🔴 2026-11-16：首版用「产品名前两个字」当品牌，直接产出 `ROG→RO`、
#    `ThinkPad→Th`、`msi→ms` 这种垃圾值。后果不只是显示难看 ——
#    `match_named_model` 的否决② 是「句子里提到了另一个库内品牌、却没提这台品牌
#    就弃权」。句子里 `redmibookpro14` 含子串 `ro`，于是被当成"提到了品牌 RO"，
#    而目标那台 brand 是「小米」，不在句子里 → **弃权**。
#    用户问「Redmi Book Pro 14 好吗」，系统答"不知道你说哪台"。
#    实测代价：test_named 从 124/124 掉到 120/124。
#
#    判据：品牌必须来自**词典匹配**，不许靠截字符串。
#    子品牌要归到母公司（ROG→华硕、ThinkPad→联想、Redmi→小米），
#    因为用户说"ROG"和说"华硕"指的是同一家，不该判成两个品牌互相否决。
#    顺序很重要：长的放前面，否则「机械」会先匹配掉「机械革命」。
_BRAND_DICT = [
    ("机械革命", "机械革命"), ("清华同方", "清华同方"), ("同方", "清华同方"),
    ("ThinkPad", "联想"), ("ThinkBook", "联想"), ("Legion", "联想"),
    ("拯救者", "联想"), ("小新", "联想"), ("YOGA", "联想"), ("联想", "联想"),
    ("Lenovo", "联想"),
    ("ROG", "华硕"), ("玩家国度", "华硕"), ("天选", "华硕"), ("无畏", "华硕"),
    ("灵耀", "华硕"), ("华硕", "华硕"), ("ASUS", "华硕"),
    ("暗影精灵", "惠普"), ("HyperX", "惠普"), ("星Book", "惠普"), ("战66", "惠普"),
    ("惠普", "惠普"), ("HP", "惠普"),
    ("Inspiron", "戴尔"), ("XPS", "戴尔"), ("灵越", "戴尔"), ("外星人", "戴尔"),
    ("Alienware", "戴尔"), ("戴尔", "戴尔"), ("Dell", "戴尔"),
    ("MateBook", "华为"), ("华为", "华为"), ("HUAWEI", "华为"),
    ("MagicBook", "荣耀"), ("荣耀", "荣耀"), ("HONOR", "荣耀"),
    ("Redmi", "小米"), ("红米", "小米"), ("Xiaomi", "小米"), ("小米", "小米"),
    ("掠夺者", "宏碁"), ("宏碁", "宏碁"), ("Acer", "宏碁"),
    ("微星", "微星"), ("MSI", "微星"),
    ("神舟", "神舟"), ("雷神", "雷神"), ("七彩虹", "七彩虹"),
    ("MacBook", "苹果"), ("苹果", "苹果"), ("Apple", "苹果"),
    ("VAIO", "VAIO"), ("富士通", "富士通"), ("松下", "松下"),
    ("中柏", "中柏"), ("攀升", "攀升"), ("宁美", "宁美"),
    ("watch", "其他"), ("笔记本", "其他"),
]


def brand_of(name, fallback=None):
    """从产品名里认品牌。认不出返回 fallback（通常是 None，不猜）。"""
    n = name or ""
    low = n.lower()
    for key, brand in _BRAND_DICT:
        if key.lower() in low:
            return brand
    # 词典认不出就**不猜** —— 宁可 brand=None，也不要"前两个字"这种垃圾
    return fallback


def norm(name):
    """把机型名压成可比的形式。"""
    return _DROP.sub("", name or "").lower().strip()


def vendor_of_cpu(cpu):
    """从 CPU 型号推断厂商。推不出返回 None —— 不猜。"""
    c = (cpu or "").lower()
    if not c:
        return None
    if re.search(r'ultra|core|celeron|pentium|i[3579][- ]?\d|\bcpu\b', c) or c.startswith("intel"):
        return "Intel"
    if re.search(r'ryzen|\br[3579][- ]?\d|athlon|amd', c):
        return "AMD"
    if re.search(r'\bm[1-9]\b|apple', c):
        return "Apple"
    if re.search(r'snapdragon|骁龙|qualcomm', c):
        return "Qualcomm"
    return None


def guess_cat(rec):
    """判品类。**只按能验证的证据判**，不按名字里的字眼猜。

    证据优先级：
      ① 有独立显卡型号（RTX/GTX/RX/Arc 数字）→ gaming
      ② 产品定位里明说「游戏本」→ gaming
      ③ 产品定位里明说「轻薄/商务/家用」→ thin
      ④ 都没有 → None（交给下游按未知处理，不许硬塞）
    """
    gpu = (rec.get("gpu") or "").upper()
    if re.search(r'RTX|GTX|\bRX\s?\d|ARC\s?[A-Z]?\d', gpu):
        return "gaming"
    pos = rec.get("positioning") or ""
    if "游戏" in pos:
        return "gaming"
    if any(w in pos for w in ("轻薄", "商务", "家用", "影音", "学生")):
        return "thin"
    if gpu in ("核显", "集显"):
        return "thin"
    return None


def gamut_of(rec):
    """把 ZOL 采集的色域字段归一到 `screen.colorGamut` 的结构。

    🔴 `lib_judge.field_value("colorGamut")` 只判断 `bool(v)`，
       所以结构是什么样都行；但**键名必须是 colorGamut**。
       历史错位（写成 `gamut`）让 86 台色域数据在库里却判不出来。

    采集层只有一个 `gamut` 字符串（"100%"），它来自哪个色域标准要看 `_raw`：
      色域 = sRGB：100%  → {srgb: 100}
      DCI-P3色域 = 100%  → {dciP3: 100}
      sRGB色域 = 100%    → {srgb: 100}
    取不到标准时**不猜**，返回 None —— 宁可缺，不许错标（项目纪律 §9.2）。
    """
    raw = rec.get("_raw") or {}
    out = {}
    for key, dst in (("sRGB色域", "srgb"), ("DCI-P3色域", "dciP3"), ("NTSC色域", "ntsc")):
        n = num(raw.get(key), int, 30, 200)
        if n is not None:
            out[dst] = n
    if not out:
        g = rec.get("gamut")
        label = raw.get("色域") or ""
        n = num(g, int, 30, 200) if g else None
        if n is not None:
            low = str(label).lower()
            if "p3" in low:
                out["dciP3"] = n
            elif "srgb" in low:
                out["srgb"] = n
            elif "ntsc" in low:
                out["ntsc"] = n
            # 无标签 → 不填，交给下一轮重爬（zol_recrawl 能拿到带标签的值）
    return out or None


def num(v, cast=float, lo=None, hi=None):
    """把采集层拿到的字符串数字转成数值。转不了/超范围返回 None。

    🔴 必须转，不能直接塞进去：
       `zol_fetch` 里的 `_num()` 返回的是**正则捕获组，也就是字符串**。
       直接写进池里，下游 `_portable_of_pool()` 会做 `w <= 1.4`，
       而 `'2.48' <= 1.4` 在 Python 3 里直接 TypeError。
       实测 2026-11-16 就是这么崩的（166 台一台都进不去池）。
       ⇒ 采集层保持原始字符串（保真、可追溯），**类型转换一律在转换层做**。
    """
    if v in (None, ""):
        return None
    try:
        f = cast(v)
    except (TypeError, ValueError):
        return None
    if lo is not None and f < lo:
        return None
    if hi is not None and f > hi:
        return None
    return f


def split_res(res):
    """'2560×1600' → (2560, 1600)。解析不了返回 (None, None)。"""
    m = re.search(r'(\d{3,4})\s*[x×]\s*(\d{3,4})', res or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def sku_suffix(rec):
    """配置后缀，用来区分同名机型的不同 SKU。

    🔴 这是必须的，不是美化：ZOL 每个**配置**是独立页面，所以
       「华硕天选6 Pro 酷睿版」会有 4 条（U7-255HX/16G/1TB、U5-235HX/16G/512G…）。
       全部同名进池，用户看到的是一堆看起来一样的机器，没法选也没法比价。
       而它们**确实是不同的商品**（价格能差两千），不能合并成一条。

       做法：名称保持原样（可读），另给一个 `skuLabel` 写清配置差异。
       ⚠️ 不去改 `name` —— 下游按 name 做匹配/展示，改了会破坏现有逻辑。
    """
    bits = []
    if rec.get("cpu"):
        bits.append(str(rec["cpu"]))
    if rec.get("ramGB"):
        bits.append("%sG" % rec["ramGB"])
    if rec.get("storageGB"):
        g = rec["storageGB"]
        bits.append(("%dT" % (g // 1024)) if g >= 1024 else ("%dG" % g))
    if rec.get("gpu") and rec["gpu"] not in ("核显", "集显"):
        bits.append(str(rec["gpu"]))
    return "/".join(bits) or None


def config_key(rec):
    """配置指纹，用来识别**采集中重复**（ZOL 列表页里不同 id 指向同一台）。

    ⚠️ 入参是 **to_pool 之后**的池格式记录，不是采集原始记录 ——
       `ram` 是对象（`{"gb": 16}`）不是 `ramGB`。
       首版照采集格式读 `rec.get("ramGB")`，永远拿到 None，
       于是这个去重**一次都没生效过**（输出"采集内部重复 0 台"就是它的症状，
       而实际独立统计是 8 组重复）。

    🔴 只在配置**全部有值**时才返回指纹，有一个是 None 就返回 None。

       为什么这么严：实测 133 台里「ThinkPad」出现 3 次，三条的 cpu 都是
       「英特尔 酷睿i5 10代系列」、内存硬盘全是 None —— 它们很可能是
       **三台完全不同的 ThinkPad**，只是名称和配置都没解析出来。
       如果用 `(name, cpu, None, None)` 当指纹，会把三台不同的机器合成一台，
       用户就永远看不到其中两台 —— 这是**丢数据**，比留着冗余严重得多。

       ⇒ 判据：能确认是同一台才合并；确认不了就都留着。
         项目红线「宁可弃权不许自信地做错」在这里就是「宁可冗余不许误删」。
    """
    bits = [rec.get("name"), cpu_sig(((rec.get("cpu") or {}).get("model"))),
            (rec.get("ram") or {}).get("gb"), rec.get("storageGB"),
            ((rec.get("gpu") or {}).get("model"))]
    if any(b in (None, "") for b in bits):
        return None
    return tuple(bits)


def cpu_sig(cpu):
    """CPU 的"数字指纹"，只取型号里的 3~5 位数字。

    `i7 14650HX` / `i7-14650HX` / `Core i7-14650HX` → 都是 `14650`
    `Ultra7 255HX` / `Ultra 7 255HX`                → 都是 `255`
    取不到返回 None（**不参与判重**，见 same_config）。

    ⚠️ 首版写成 `\\d{4,5}`，于是 `Ultra7 255HX`（型号只有 3 位）返回 None，
       判重直接失效（输出"采集内部重复 0 台"，而实际独立统计有 8 组）。
       3~5 位是实测出来的宽度：Intel 的 14650/13420 是 5 位，
       AMD/Ultra 的 255/350/226 是 3 位。"i7" 的 7 只有 1 位，自然被跳过。
    """
    m = re.search(r'\d{3,5}', cpu or "")
    return m.group(0) if m else None


def same_config(a, b):
    """两条记录是不是**同一台机器的同一配置**。

    用于「疑似重复」的进一步判定：
      疑似重复（同品牌名互含）但配置完全相同 → 真的是同一台，丢掉 ZOL 那条
      疑似重复但配置不同 → 是不同 SKU，都要保留

    🔴 只在**四项全部有值且相同**时才判同配置。任何一项是 None 都返回 False。
       理由和 config_key 一样：确认不了就是两台，宁可留冗余不可误删。
       实测场景（2026-11-16）：JD 的「七彩虹 隐星P16 Pro 星风版 2026（U7-251HX/…）」
       和 ZOL 的「七彩虹隐星P16 Pro」是同型号，若不判重，池里两台同名，
       `match_named_model` 并列 → 弃权，用户问「隐星P16怎么样」系统答不上来。
    """
    def sig(x):
        cpu = cpu_sig(((x.get("cpu") or {}).get("model")))
        ram = (x.get("ram") or {}).get("gb")
        st = x.get("storageGB")
        gpu = re.sub(r'[\s\-]', '', ((x.get("gpu") or {}).get("model") or "").lower())
        return cpu, ram, st, gpu or None

    sa, sb = sig(a), sig(b)
    if any(v is None for v in sa) or any(v is None for v in sb):
        return False
    return sa == sb


def to_pool(rec):
    """ZOL 扁平记录 → machine_pool 嵌套记录。"""
    w, h = split_res(rec.get("screenRes"))
    cpu = rec.get("cpu")
    gpu = rec.get("gpu")
    # 价格：有京东价优先用京东（本项目的主渠道），没有再用天猫，并标清来源
    price_val = rec.get("priceJD") or rec.get("priceTmall")
    price_src = "京东" if rec.get("priceJD") else ("天猫" if rec.get("priceTmall") else None)

    out = {
        "id": "zol-" + str(rec.get("sourceId")),
        "name": rec.get("name"),
        "skuLabel": sku_suffix(rec),
        # 🔴 必须带 source：`core._pool_to_offer` 用 `m.get("source") == "zol"`
        #    决定 origin 字段。漏了它，166 台全被标成 `jd-pool`，
        #    以后想统计"这批数据从哪来的、可信度如何"就分不开（2026-11-16 踩过）。
        "source": "zol",
        "brand": brand_of(rec.get("name")),
        "cat": guess_cat(rec),
        "cpu": {"model": cpu, "vendor": vendor_of_cpu(cpu)} if cpu else None,
        "ram": ({"gb": rec.get("ramGB"), "type": rec.get("ramType")}
                if rec.get("ramGB") else None),
        "storageGB": rec.get("storageGB"),
        "gpu": {"model": gpu} if gpu else None,
        "screen": ({
            "sizeInch": num(rec.get("screenSize"), float, 8, 40),
            "widthPx": w, "heightPx": h,
            "refreshHz": num(rec.get("screenHz"), int, 30, 600),
            "panel": rec.get("screenPanel"),
            # 🔴 键名必须与 lib_judge.field_value 对齐：它读 screen.colorGamut /
            #    screen.brightnessNit。这里曾写成 gamut / brightness，
            #    结果 86 台色域、96 台亮度**数据在库里但判定引擎看不见**，
            #    平面视觉组一直报「缺 colorGamut」——2026-11-16 修。
            "colorGamut": gamut_of(rec),
            "brightnessNit": num(rec.get("brightness"), int, 100, 3000),
        } if any([rec.get("screenSize"), w, rec.get("screenHz"),
                  rec.get("screenPanel")]) else None),
        "batteryWh": num(rec.get("batteryWh"), int, 10, 200),
        "weightKg": num(rec.get("weightKg"), float, 0.3, 10),
        "thicknessMm": num(rec.get("thicknessMm"), float, 5, 60),
        "keyboardBacklit": rec.get("keyboardBacklit"),
        "faceId": rec.get("faceId"),
        "milSpec": rec.get("milSpec"),
        "ports": [x for x in [rec.get("portsVideo"), rec.get("portsAudio")] if x] or None,
        "os": rec.get("os"),
        "launchDate": rec.get("launchDate"),
        "positioning": rec.get("positioning"),
        # 🔴 价格口径必须跟着值一起走，不许只剩一个数。
        #    `price.list` = 平台标价，`price.final` = 我们这里**没有**
        #    到手价（不含券/国补），所以 final 不填，避免下游误当到手价用。
        "price": ({
            "list": price_val,
            "final": None,
            "basis": "zol快照·%s" % price_src,
            "note": "平台标价快照，不含国补/券，不等于到手价",
        } if price_val else None),
        "priceCapturedAt": rec.get("capturedAt"),
        "url": rec.get("sourceUrl"),
        "dataFrom": {"spec": "zol", "price": "zol" if price_val else None},
        "confidence": "medium",
        "backfilled": [],
        "_zolRaw": rec.get("_raw"),
    }
    return out


def load_zol():
    rows = []
    for fp in sorted(glob.glob(os.path.join(ZOL_DIR, "*.jsonl"))):
        for line in open(fp, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--apply", action="store_true", help="并入 machine_pool.json（先备份）")
    args = ap.parse_args()

    zol = load_zol()
    if not zol:
        print("没有 ZOL 数据。先跑 zol_fetch.py")
        return
    print("ZOL 原始记录: %d 台" % len(zol))

    pool = json.load(open(POOL, encoding="utf-8"))
    machines = pool.get("machines") or []
    print("现有采集池: %d 台" % len(machines))

    exist_norm = {}
    for m in machines:
        exist_norm.setdefault(norm(m.get("name")), m)

    adds, dup, suspect = [], [], []
    seen_cfg = {}
    self_dup = 0
    for r in zol:
        p = to_pool(r)
        if not p.get("name"):
            continue
        n = norm(p["name"])
        if not n:
            continue
        # ① 本次采集内部去重：配置指纹完全相同才算同一台（见 config_key）
        ck = config_key(p)
        if ck and ck in seen_cfg:
            self_dup += 1
            continue
        if ck:
            seen_cfg[ck] = p
        # ② 与现有池规范化后完全相同 → 真重复
        if n in exist_norm:
            dup.append((p["name"], exist_norm[n].get("name"), "规范化后完全相同"))
            continue
        # 疑似：同品牌且互相包含。**先比配置**：
        #   配置完全相同 → 同一台机器，丢掉 ZOL 这条（JD 的有到手价和人工评价，更好）
        #   配置不同（或任一项缺失）→ 不同 SKU，保留
        #
        # 🔴 2026-11-16 首版写错过两次，都记在这里：
        #    ① 原来是 `suspect.append(...); continue` —— "疑似"被跳过、根本没进 adds，
        #       而注释写的是"保留"。注释和代码相反，结果池子少了一批机器还没人发现。
        #    ② 第二次改成"全部保留"，又过头了：ZOL 采到的机器和 JD 已有的重复，
        #       池里出现两台同名，`match_named_model` 并列弃权，
        #       「隐星P16怎么样」这种明确指名反而答不上来。
        #    ⇒ 正解是按**配置指纹**分：同配置合并，不同配置都留。
        hit = None
        for en, em in exist_norm.items():
            if not en:
                continue
            if p.get("brand") and em.get("brand") == p.get("brand") and (
                    n in en or en in n) and min(len(n), len(en)) >= 6:
                hit = em
                break
        if hit and same_config(p, hit):
            dup.append((p["name"], hit.get("name"), "疑似重复且配置相同"))
            continue
        if hit:
            suspect.append((p["name"], hit.get("name"), p.get("skuLabel")))
        adds.append(p)

    print()
    print("采集内部重复（配置指纹相同）: %d 台（自动去掉）" % self_dup)
    print("与现有池规范化后相同        : %d 台（自动去掉）" % len(dup))
    print("与池内机型疑似重复但配置不同: %d 台（**保留**，是不同 SKU）" % len(suspect))
    print("净新增                      : %d 台" % len(adds))
    print()
    if suspect:
        print("疑似重复清单（前 15，ZOL 的 SKU 与池内那条是否同一台，需要人眼确认）:")
        for a, b, c in suspect[:15]:
            print("   ZOL: %-28s [%s]  池内: %s" % (a[:26], (c or "-")[:18], b))
        print()

    # 新增数据的字段完整度 —— 这是判断"补得值不值"的唯一依据
    def pct(k, f):
        got = sum(1 for x in adds if f(x))
        return "%s %d/%d=%d%%" % (k, got, len(adds), 100 * got // max(1, len(adds)))

    print("新增数据的完整度:")
    print("  " + " | ".join([
        pct("型号", lambda x: x.get("name")),
        pct("CPU", lambda x: x.get("cpu")),
        pct("显卡", lambda x: x.get("gpu")),
        pct("内存", lambda x: x.get("ram")),
        pct("屏幕", lambda x: x.get("screen")),
        pct("重量", lambda x: x.get("weightKg")),
        pct("价格", lambda x: x.get("price")),
    ]))
    cats = {}
    for x in adds:
        cats[x.get("cat") or "未知"] = cats.get(x.get("cat") or "未知", 0) + 1
    print("  品类分布:", cats)

    if not args.write and not args.apply:
        print("\n（只是统计。加 --write 写文件，加 --apply 直接并入池）")
        return

    out_path = os.path.join(ZOL_DIR, "pool_additions.json")
    json.dump(adds, open(out_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n已写出: %s" % out_path)

    if args.apply:
        bak = POOL + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        print("已备份: %s" % os.path.basename(bak))
        pool["machines"] = machines + adds
        st = pool.setdefault("stats", {})
        st["total"] = len(pool["machines"])
        st["fromZol"] = len(adds)
        pool.setdefault("meta", {})["zolMergedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        json.dump(pool, open(POOL, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("已并入 machine_pool.json，现共 %d 台" % len(pool["machines"]))


if __name__ == "__main__":
    main()
