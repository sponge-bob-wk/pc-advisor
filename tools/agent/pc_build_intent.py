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
    r"整机多少钱|一共多少钱)", re.I)

# 排除：说"不要组装"、"别装机"
NEG_PAT = re.compile(r"(不要组装|别装机|不组装|不用装机|不要散件)")


def wants_build(text):
    """用户是不是想自己组装台式机。"""
    t = text or ""
    if NEG_PAT.search(t):
        return False
    return bool(BUILD_PAT.search(t))


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


def render_plan(plan):
    """把装机方案渲染成 markdown（对话里直接能看）。"""
    if not plan:
        return ""

    out = []
    out.append("### %s · %s" % (plan["tierName"], plan["use"]))
    out.append("")
    out.append("| 配件 | 型号 | 参考价 |")
    out.append("|---|---|---:|")
    for r in plan["rows"]:
        out.append("| %s | %s | ¥%s |"
                   % (r["label"], r["name"],
                      format(int(round(r["price"])), ",")))
    out.append("| **合计** | | **¥%s** |"
               % format(int(round(plan["total"])), ","))
    out.append("")

    if plan.get("issues"):
        out.append("**需要确认**：")
        for x in plan["issues"]:
            out.append("- ⚠️ %s" % x)
        out.append("")

    out.append("> %s" % plan.get("note", ""))
    return "\n".join(out)
