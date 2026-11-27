# -*- coding: utf-8 -*-
"""build_followup.py —— 回答「刚才那套装机单」的追问。

## 为什么需要这个文件

2026-11-27 真实日志（11:16 会话）复现出来的：

    用户：台式机，预算8000，主要写代码      → 出了装机单
    用户：电源够吗                          → **又出了一份新单子**
    用户：这套配置能玩黑神话吗               → **又出了一份新单子**

装机单出完就没有句柄了（focus 一直是 None），下一句只要沾到台式就走
build_plan 重算，用户问的那张单子等于不存在。这就是「上下文状态丢失」。

## 边界（别越界）

只回答**关于这套单子本身**的问题：电源余量、散热选型、配件清单、
已知隐患、总价。**不编性能**——「能玩XX多少帧」库里没有实测，就直说不知道。

数字全部现算：功耗来自 build_pc 的 CPU_WATT / GPU_WATT 表，
电源额定功率从型号名里抠（psu_watt）。没有一处是猜的。
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 指代「刚才那套」
REF = re.compile(r"这套|这单|这个配置|这个单子|这套配置|这个装机|这配|刚才|上面那套|"
                 r"这台|这个机器|这机器")

# 部件词
PART = re.compile(r"电源|散热|风扇|水冷|主板|内存|固态|硬盘|机箱|显卡|CPU|处理器",
                  re.I)

# 「问」的语气词（**不含 换/加/升级** —— 那是动作请求，要重算单子，不是追问）
ASK = re.compile(r"够|行不行|行吗|怎么样|如何|多少|多大|大不|小不|够用|能玩|"
                 r"带得动|跑得动|可以吗|有问题|问题|缺|坑|合理|值|瓶颈|短板")

# 动作请求：要改这张单子 → 交给 build_plan 重算，不要在这里答
ACTION = re.compile(r"换|改|加|升级|去掉|不要|降|提到|预算|再加|减")

# 动作请求里带这些，仍然是在「问」而不是「让改」
ASKING_FORM = re.compile(r"吗|能不能|可不可以|行不行|够不够|要不要")


def is_followup(text, build):
    """这句话是不是在问「刚才那套」。没单子就一律不是。

    🔴 边界（实测踩过）：
      「电源够吗」    → 追问，答余量
      「固态换成1T」   → **不是**追问，是要改单子 → 重算
      「这套有什么问题」→ 追问
      「能玩黑神话吗」 → 追问（上下文只有这一套，就是在问它）
    """
    if not build:
        return False
    t = (text or "").strip()
    if not t:
        return False
    # 动作请求（且不是「能不能换」这种问法）→ 不是追问
    if ACTION.search(t) and not ASKING_FORM.search(t):
        return False
    if REF.search(t) and ASK.search(t):
        return True
    if PART.search(t) and ASK.search(t):
        return True
    if re.search(r"能玩|带得动|跑得动", t):
        return True
    return False


def _rows(build):
    return {r.get("label"): r for r in (build.get("rows") or [])}


def _fmt(v):
    return "¥%s" % format(int(round(v or 0)), ",")


def answer(text, build):
    """返回 markdown 字符串。所有数字现算，算不出来就说算不出来。"""
    import build_pc as bpc

    t = text or ""
    rows = _rows(build)
    cpu = rows.get("CPU 处理器") or rows.get("CPU") or {}
    gpu = rows.get("显卡") or {}
    psu = rows.get("电源") or {}
    cool = rows.get("散热器") or {}
    total = build.get("total") or 0

    # ---- 电源 ----
    if re.search(r"电源|多少瓦|功率", t):
        pw = bpc.psu_watt(psu.get("name") or "")
        if not pw:
            return ("这单子的电源我没读出额定功率（型号里没有瓦数），"
                    "不敢给你算余量。换个写了功率的电源型号我再算。")
        load = bpc.cpu_watt(cpu.get("name") or "") + \
            bpc.gpu_watt(gpu.get("name") or "") + 60
        margin = pw - load
        if margin >= 100:
            verdict = "够，还剩 %d W 余量（后面换显卡也有空间）" % margin
        elif margin >= 0:
            verdict = "刚好够，余量只有 %d W，基本没有升级空间" % margin
        else:
            verdict = "**不够**，满载估算超出 %d W，要换更大的电源" % (-margin)
        return ("这套的电源是 %s（额定 %d W）。\n\n"
                "按 CPU %d W + 显卡 %d W + 主板硬盘风扇约 60 W 粗算，"
                "满载约 %d W。\n\n结论：%s。\n\n"
                "（功耗是按型号规格表估的，不是实测；电源长期贴着上限跑会吵、寿命也短，"
                "建议留 20%% 余量。）"
                % (psu.get("name"), pw,
                   bpc.cpu_watt(cpu.get("name") or ""),
                   bpc.gpu_watt(gpu.get("name") or ""),
                   load, verdict))

    # ---- 散热 ----
    if re.search(r"散热|风扇|水冷|压得住|热", t):
        if not cool:
            return "这单子没配独立散热器（用 CPU 原装/盒装散热），压不住长时间满载。"
        kind = "水冷" if "水冷" in (cool.get("name") or "") else "风冷"
        return ("这套配的是 %s（%s）。CPU 是 %s，功耗约 %d W。\n\n"
                "%s按这个功耗选是常规搭配；但机箱风道和房间温度影响很大，"
                "满载噪音我这边没有实测数据，不敢替你保证安静。"
                % (cool.get("name"), kind, cpu.get("name"),
                   bpc.cpu_watt(cpu.get("name") or ""), kind))

    # ---- 能不能玩 / 够不够用 ----
    if re.search(r"能玩|带得动|跑得动|够用|够吗|行不行|可以吗", t):
        has_dgpu = bool(gpu.get("name")) and "核显" not in (gpu.get("name") or "")
        use = build.get("use") or ""
        head = "这套是按「%s」配的，显卡是 %s。" % (
            use, gpu.get("name") or "核显")
        if not has_dgpu:
            body = ("没有独立显卡，3A 大作和本地跑模型都吃力；"
                    "网游、办公、写代码没问题。")
        else:
            body = ("有独立显卡，主流游戏能跑；但**具体帧数我不报** —— "
                    "库里没有这台机器的实测数据，报了就是编。"
                    "你要哪个游戏，我可以按它的要求重配一版。")
        return head + "\n\n" + body

    # ---- 有没有问题 / 坑 / 短板 ----
    if re.search(r"问题|坑|短板|瓶颈|合理|值不值|缺点", t):
        issues = build.get("issues") or []
        base = "这套合计 %s。\n\n" % _fmt(total)
        if issues:
            # ⚠️ build_plan 的 issues 装的是**配置理由/取舍说明**（"写代码优先 CPU"），
            #    不是缺陷。原来写成「隐患」，用户会以为配置有硬伤，用词要准。
            base += "配置时的取舍（每件为什么这么选）：\n\n"
            for it in issues:
                base += "- %s\n" % it
        else:
            base += ("配件层面没撞到硬冲突（接口/供电/尺寸都过了校验）。"
                     "但这只能说明「装得起来」，不能说明「适合你」——"
                     "适不适合要看你要跑什么。\n")
        base += ("\n要我逐件点评也行，说一声我把每一件为什么选它讲清楚。")
        return base

    # ---- 默认：复述清单 ----
    lines = ["这套是「%s」，合计 **%s**：" % (build.get("use") or build.get("tierName"),
                                            _fmt(total)), ""]
    lines.append("| 配件 | 型号 | 参考价 |")
    lines.append("|---|---|---:|")
    for r in build.get("rows") or []:
        lines.append("| %s | %s | %s |" % (r.get("label"), r.get("name"),
                                           _fmt(r.get("price"))))
    lines.append("")
    lines.append("要改哪一件直接说（比如「固态换 1T」「电源换大点」），我按这套改。")
    return "\n".join(lines)
