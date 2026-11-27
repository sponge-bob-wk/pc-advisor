# -*- coding: utf-8 -*-
"""pc_build_intent.py —— 判断用户是不是想「自己组装台式机」

## 为什么要单独一个模块

用户的两种台式机诉求，行为完全不同：

  ① 「我要一台台式机」  → 买整机（cat=desktop，库里 2 台）
  ② 「我想自己组装」    → 装机单（用 327 个配件拼）

第 ②种是 2026-11-26 新增的能力。如果混在一起处理，
系统会用「整机推荐」的逻辑回答装机需求，答非所问。

## 判据

**有明确组装意图**才算：
    组装 / 装机 / 自己配 / 攒机 / 配件 / DIY / 散件
    「台式机 + 自己配」这类组合

**只有「台式机」「主机」不算** —— 那可能就是想买整机。

## 为什么不用模型判断

和 `execute_analyze` 一样的理由：这是**确定性分类**，
正则能做的别交给模型（模型会漏、会漂、不能测试）。
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 明确的组装意图
BUILD_PAT = re.compile(
    r"(组装|装机|自己配|自己搭|攒机|攒一台|攒个|配一台|配个|DIY|"
    r"散件|配件清单|买配件|自己装|配下来|"
    r"主板.{0,6}显卡|CPU.{0,6}主板|"
    r"整机多少钱|一共多少钱|"
    # 🔴 2026-11-27 补：**「配」直接跟型号词的紧凑写法**。
    #
    #    事故（v9 评测）：「五千配电脑」「五千预算配电脑主机」
    #    → wants_build=False → 走 clarify 反问，用户觉得没听懂。
    #
    #    原正则只有「配一台 / 配个」这种带量词的，缺了
    #    「配电脑 / 配主机 / 配一套 / 配置单 / 装机单」。
    #    ⚠️ 必须「配 + 型号词」，不能只匹配"配置"二字 ——
    #       「配置怎么样」是在问现有配置，不该走装机（那是 ask_current）。
    r"配[^。！？，,]{0,6}(电脑|主机|整机|机子|配置单|装机单)|"
    r"装机单|配置单|组装一台|组装个)", re.I)

# 排除：说"不要组装"、"别装机"
NEG_PAT = re.compile(r"(不要组装|别装机|不组装|不用装机|不要散件)")

# 🔴 2026-11-26：**台式机一律走装机，不推整机**（用户明确要求）
#
#    「台式机是要按照装机标准，根据用户需求你去推荐的不是说整件」
#
# 为什么整机池不适合推荐：
#   · 只有 2 台（DIY 整机 + 只换板U），根本排不了序
#   · 「整机」是别人配好的固定方案，改不了任何一个部件
#   · 用户要台式机的本质诉求是「按我的需求配」，{品牌}整机反而限制了选择
#
# 所以：只要品类是台式，就进装机分支，用 327 个配件按需求拼。
DESKTOP_PAT = re.compile(
    r"(台式|台式机|桌面机|主机|塔式|DIY\s*机)", re.I)


def wants_build(text, slots=None):
    """用户是不是要「台式机方案」（一律按装机处理）。

    两层判据：
      ① 有明确组装意图词 → 是
      ② 品类被识别成 desktop → 也是（台式机不推整机，只推装机单）

    ⚠️ 例外：明确说「不要组装」「要买整机」→ 不是。
    """
    t = text or ""
    if NEG_PAT.search(t):
        return False

    # ① 明确说组装
    if BUILD_PAT.search(t):
        return True

    # ② 品类是台式 → 默认走装机
    cat = (slots or {}).get("cat")
    if cat == "desktop":
        return True

    # ③ 原话里提到台式（即使槽位还没识别出来）
    if DESKTOP_PAT.search(t):
        return True

    return False


def budget_of(text, slots=None):
    """从一句话里抠预算。抠不到返回 None（交给档位默认）。"""
    # 先看 slots（模型/规则已经解析过的更可信）
    b = (slots or {}).get("budget")
    if b:
        try:
            return float(b)
        except (TypeError, ValueError):
            pass

    t = text or ""
    # 「8000」「8000元」「8千」「一万」「1万2」
    m = re.search(r"(\d{4,6})\s*(?:元|块|rmb)?", t)
    if m:
        v = int(m.group(1))
        if 1000 <= v <= 60000:
            return float(v)
    m = re.search(r"([一二三四五六七八九十两])\s*万\s*(\d)?", t)
    if m:
        cn = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        wan = cn.get(m.group(1), 0)
        rest = int(m.group(2) or 0) * 1000
        v = wan * 10000 + rest
        if 1000 <= v <= 60000:
            return float(v)
    m = re.search(r"(\d(?:\.\d)?)\s*万", t)
    if m:
        v = int(float(m.group(1)) * 10000)
        if 1000 <= v <= 60000:
            return float(v)
    m = re.search(r"([一二三四五六七八九])\s*千", t)
    if m:
        cn = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
        v = cn[m.group(1)] * 1000
        if 1000 <= v <= 60000:
            return float(v)
    return None


# ── 装机单参数列解析（2026-11-27 用户要求「价格参数细细罗列」）──
#
# 配件名里本来就带着关键参数，按类型用正则抠出来。
# 不新增数据源 —— 从型号名解析，保证和名字一致，不会出现
# "参数列和型号列对不上"的错位。


def _spec_of(part, name):
    """从型号名解析关键参数。解析不出就返回空串。"""
    n = name or ""
    if part == "cpu":
        m = re.search(r"(i[3579]|Ryzen\s*[3579]|Ultra\s*[579])[^,，]*", n)
        core = m.group(0).strip() if m else ""
        tags = []
        if re.search(r"\bK\b|KF", n): tags.append("可超频")
        if re.search(r"F\b", n) and "KF" not in n: tags.append("无核显")
        return core + ("（" + "/".join(tags) + "）" if tags else "")
    if part == "cooler":
        if re.search(r"水冷|冷排|AIO|一体式", n, re.I):
            m = re.search(r"(\d{3})", n)
            return "水冷 %smm" % (m.group(1) if m else "240")
        m = re.search(r"(\d)\s*热管", n)
        return "风冷" + (" %s 热管" % m.group(1) if m else "")
    if part == "motherboard":
        m = re.search(r"([BHZXA]\d{3}[M]?|X\d{3}[E]?)", n, re.I)
        chip = m.group(1).upper() if m else ""
        size = "MATX" if re.search(r"M\b", n[len(chip):] if chip else n) else "ATX"
        return "%s · %s" % (chip, size) if chip else ""
    if part == "ram":
        m = re.search(r"(\d+G\s*\(?.*?\)?\s*)?(DDR\d)\s*(\d{3,4})?", n)
        if m:
            cap = re.search(r"(\d+G)", n)
            freq = m.group(3) or ""
            return "%s %s %s" % (cap.group(1) if cap else "",
                                 m.group(2), freq)
        return ""
    if part == "gpu":
        m = re.search(r"(RTX\s*\d{4}\s*(Ti)?|RX\s*\d{4}|GTX\s*\d{3,4})", n, re.I)
        vram = re.search(r"(\d+)\s*[Gg][Bb]", n)
        parts_ = []
        if m: parts_.append(re.sub(r"\s+", "", m.group(0)).upper())
        if "Ti" in n or "TI" in n.upper().replace(" ", ""):
            if parts_ and "TI" not in parts_[0].upper():
                parts_[0] += " Ti"
        return " ".join(parts_)
    if part in ("ssd", "storage"):   # storage=固态主盘（装机单里 ssd 主盘键是 storage）
        m = re.search(r"(\d+\s*[TtGg][Bb])", n)
        cap = m.group(1).upper().replace("TB", "T").replace("GB", "G") if m else ""
        return "%s NVMe" % cap if cap else ""
    if part == "hdd":
        m = re.search(r"(\d+\s*[Tt][Bb])", n)
        cap = m.group(1).upper().replace("TB", "T") if m else ""
        rpm = re.search(r"(\d{4})\s*转", n)
        return "%s %s转" % (cap, rpm.group(1) if rpm else "") if cap else ""
    if part == "psu":
        m = re.search(r"(\d{3,4})\s*W", n, re.I)
        cert = re.search(r"(金牌|银牌|铜牌|白金)", n)
        return "%sW %s" % (m.group(1) if m else "?", cert.group(1) if cert else "")
    if part == "case":
        if re.search(r"MATX|M-ATX|紧凑", n, re.I): return "MATX 机箱"
        if re.search(r"ITX|MINI", n, re.I): return "ITX 机箱"
        return "ATX 机箱"
    return ""


def render_plan(plan):
    """把装机方案渲染成 markdown（对话里直接能看）。"""
    if not plan:
        return ""

    out = []
    out.append("### %s · %s" % (plan["tierName"], plan["use"]))
    out.append("")
    # 2026-11-27：方案特点说明（用户要求「简要说明方案特点」）
    # 由 build_pc._variant_desc 按实际配置生成，不是模型编的
    if plan.get("variantNote"):
        out.append("> %s" % plan["variantNote"])
        out.append("")
    # 🔴 2026-11-27：**表格加参数列 + 占比列**（用户要求「价格参数细细罗列」）
    #    装机动辄上万，用户关心每件钱花在哪、买到什么规格。
    _total = plan["total"] or 1
    out.append("| 配件 | 型号 | 关键参数 | 占预算 | 参考价 |")
    out.append("|---|---|---|---:|---:|")
    for r in plan["rows"]:
        pct = 100.0 * (r["price"] or 0) / _total
        _link = r.get("url")
        _name = ("[%s](%s)" % (r["name"], _link)) if _link else r["name"]
        out.append("| %s | %s | %s | %.0f%% | ¥%s |"
                   % (r["label"], _name,
                      _spec_of(r["part"], r["name"]) or "—",
                      pct,
                      format(int(round(r["price"])), ",")))
    out.append("| **合计** | | | **100%%** | **¥%s** |")
    out[-1] = out[-1] % format(int(round(float(plan["total"]) or 0)), ",")
    out.append("")

    if plan.get("issues"):
        out.append("**需要确认**：")
        for x in plan["issues"]:
            out.append("- ⚠️ %s" % x)
        out.append("")

    out.append("> %s" % plan.get("note", ""))
    return "\n".join(out)
