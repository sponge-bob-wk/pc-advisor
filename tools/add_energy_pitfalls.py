#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""add_energy_pitfalls.py —— 把 2026-11-09 能效采集踩到的坑补进陷阱库（幂等）"""
import json
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "data" / "pitfalls.json"

NEW = [
    {
        "id": "source-energy-mktname",
        "category": "source",
        "severity": "high",
        "automated": False,
        "title": "用营销名去能效标识网查能效等级",
        "symptom": "搜「天选」返回「暂无数据」，于是判定该机型没有备案、或不是一级能效",
        "cause": "能效网索引的是厂商备案型号。联想恰好在备案里写了中文（小新、拯救者能查到），华硕惠普只登记字母码（FA608 / TPN-I145A3），中文名一条都搜不到",
        "detection": "查询前先确认手里有「能效网规格型号」字段；只有营销名时一律标 pending，不得判定为不合格",
        "fix": "从商品详情页抄「能效网规格型号」这一行，再用它精确查；拿不到就标 unknown",
        "where": "data/crawl/*.jsonl 的 energyModel / tools/match_energy.py",
        "evidence": "天选→0 条、FA506→0 条；而 FA608→7 条、TPN-I145A3→1 条、S5450A→1 条，全部一级",
        "affects": ["efficiency", "国补金额", "推荐结论"],
    },
    {
        "id": "source-energy-fuzzy-match",
        "category": "source",
        "severity": "blocker",
        "automated": False,
        "title": "用型号片段模糊匹配能效备案",
        "symptom": "12/29 的机型「匹配成功」，覆盖率看起来涨了",
        "cause": "营销名里的 Pro / Air / Plus 会命中整个系列：「小新 Pro 14」的 Pro 命中所有「小新Pro 27」，「小新 Air 15」的 Air 命中所有「小新Air 14」——型号与尺寸全错",
        "detection": "只允许 norm(能效网规格型号) == norm(备案型号) 严格相等才算命中；片段匹配只能进 candidates 供人工核对",
        "fix": "match_energy.py 分 EXACT / SERIES 两级，SERIES 不写 efficiency",
        "where": "tools/match_energy.py",
        "evidence": "模糊匹配 12/29 全是假命中；严格匹配 0/29 才是当时的真相（库内无一机型有 energyModel）",
        "affects": ["efficiency", "结论可信度"],
    },
    {
        "id": "source-energy-type-filter",
        "category": "source",
        "severity": "medium",
        "automated": False,
        "title": "查能效时不限定「产品类型」",
        "symptom": "搜「华硕」出 500 条，前几条是显示器和机架服务器，误以为电脑有几百款",
        "cause": "能效网把显示器、服务器、打印机、投影机、微型计算机放同一个库，靠产品类型区分",
        "detection": "查询必须先选 办公电子 → 微型计算机（2025版 / 2012版）",
        "fix": "限定后华硕微型计算机 306 条；再叠加型号前缀可精确到个位数",
        "where": "能效标识网查询页 / 采集流程",
        "evidence": "未限定时混入 XG32WCMSR 显示器、RS300-E12-PS4 服务器",
        "affects": ["efficiency", "台账质量"],
    },
    {
        "id": "source-energy-regs-number",
        "category": "source",
        "severity": "high",
        "automated": False,
        "title": "只认一种备案号格式，静默丢掉 2012 版老备案",
        "symptom": "页面写「共 11 条」，解析出来只有 8 条，少掉的正好是最老那几条",
        "cause": "备案号两套格式：2025 版 20261012-CEL0272025-445480；2012 版 20220914-301100-678046999313097（无 CEL 段）",
        "detection": "解析后必须比对「页面声明总数」与「实际解析条数」，不等即报警",
        "fix": "正则同时容纳两种格式，并记录 regScheme 区分体系",
        "where": "tools/parse_energy_raw.py",
        "evidence": "拯救者 11 条首解析只出 8 条，漏的 3 条全是 2012 版",
        "affects": ["台账完整性", "efficiency"],
    },
    {
        "id": "source-clipboard-fake-success",
        "category": "source",
        "severity": "blocker",
        "automated": False,
        "title": "把剪贴板旧内容当成复制成功",
        "symptom": "每次「复制」都能读到数据，但内容永远是上一次查询的",
        "cause": "Ctrl+C 没真正执行时（标签在后台、焦点在输入框、误点进别的页面），剪贴板保留旧值，读取照常返回成功",
        "detection": "复制前先写哨兵值，复制后比对；值未变即为失败",
        "fix": "每次复制前写哨兵；确认标签 selected=true；点击必须落在正文而非输入框",
        "where": "采集流程（PowerShell + cua_execute）",
        "evidence": "两次真故障：一次坐标误点进「联系我们」页、一次新标签 selected=false 导致按键全丢；两次都表现为「读到数据但内容还是旧的」",
        "affects": ["采集数据真实性"],
    },
]


def main():
    p = json.loads(P.read_text(encoding="utf-8"))
    have = {x["id"] for x in p["pitfalls"]}
    added = [x for x in NEW if x["id"] not in have]
    if not added:
        print("无需新增，全部已存在")
        return
    p["pitfalls"].extend(added)
    st = p["meta"]["stats"]
    st["total"] = len(p["pitfalls"])
    st["automated"] = sum(1 for x in p["pitfalls"] if x.get("automated"))
    st["manual"] = st["total"] - st["automated"]
    sev = {}
    for x in p["pitfalls"]:
        sev[x["severity"]] = sev.get(x["severity"], 0) + 1
    st["bySeverity"] = sev
    st["note"] = f"自动化的只有 {st['automated']} 条。这个数字本身就是现状：能拦住的很少，大部分坑靠人记。"
    p["meta"]["updated"] = "2026-11-09"
    P.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"新增 {len(added)} 条 -> 总计 {st['total']} 条，自动化 {st['automated']} 条")
    print("严重度:", sev)


if __name__ == "__main__":
    main()