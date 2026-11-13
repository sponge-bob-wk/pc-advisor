#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
add_pick_pitfalls.py —— 把「做推荐工具时踩到的坑」补进陷阱库

这几条都是这一轮真踩到的，不是设想出来的：
  · 同一台机器被采两次，推荐列表里出现两遍
  · 规格与在售 SKU 不是一个版本，拼出一台不存在的机器
  · 统计缺口时把「核显没显存」算成「缺数据」
  · 一句话里把同一件事说三遍

脚本幂等：按 id 判断，重复运行不会写重复条目。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "data" / "pitfalls.json"

NEW = [
    {
        "id": "join-duplicate-snapshot",
        "category": "data-quality",
        "severity": "high",
        "automated": True,
        "title": "同一台机器被采两次，推荐列表里同一款出现两遍",
        "symptom": "推荐结果里「拯救者 Y7000P 2026」重复出现，价格一模一样，用户第一眼就怀疑数据",
        "cause": "同一 SKU 跨日采集会生成不同记录 id（...20261107 / ...20261108）；"
                 "且早期记录没有 platformSkuId。用记录 id 或时间戳去重都不成立",
        "detection": "去重键用「型号 + 实付价」，而不是记录 id；"
                     "合并时优先保留带 platformSkuId、字段更全的那条",
        "fix": "build_pool.load_crawl 以 modelHint+net 为键去重；"
               "第二条按 platformSkuId、energyModel 完整度择优保留",
        "where": "tools/build_pool.py",
        "evidence": "实际数据：jd-lenovo-legion-y7000p2026-...-20261107 与 ...-20261108 两条，"
                    "net 同为 14999，前者无 platformSkuId",
        "affects": ["推荐列表", "用户信任"],
    },
    {
        "id": "join-identity-conflict",
        "category": "data-quality",
        "severity": "blocker",
        "automated": True,
        "title": "把两个不同 SKU 的规格与价格拼成一台不存在的机器",
        "symptom": "列表里出现「暗影精灵 11（Ultra7-251HX）」但参数写着 R9-8945HX + 32G + 2T，"
                   "这个配置在京东买不到",
        "cause": "specs 记录用的是京东参数区的另一个 SKU（R9 版），采集记录是 Ultra7 版，"
                 "两者 modelHint 相同。只按型号名挂价格就会串",
        "detection": "specs 的 conflicts 里有 adopted=null 且字段属于身份类"
                     "（cpu.model / gpu.model / cpu.cores / storageGB）时，禁止挂价格",
        "fix": "build_pool.unattached_reason 拦下身份冲突的记录，"
               "lib_judge.judge 把它判为 VIOLATE 并说明「规格与在售 SKU 不是同一台」",
        "where": "tools/build_pool.py + tools/lib_judge.py",
        "evidence": "specs_verified.json 的 spec-omen11-2026 已记录该冲突，adopted 为 null",
        "affects": ["推荐结论", "价格"],
    },
    {
        "id": "coverage-igpu-as-missing",
        "category": "method",
        "severity": "high",
        "automated": True,
        "title": "把「核显没有显存」统计成「缺显存数据」",
        "symptom": "缺口报告显示「显存容量缺 27 台，卡住 4 个专业组」，"
                   "据此会安排一次大范围采集，但采回来仍然是空",
        "cause": "核显共享系统内存，物理上没有独立显存。它不是「没查到」，"
                 "而是「确定不满足」。两者在统计时混在一起，缺口被放大 13 倍",
        "detection": "统计缺口时逐台跑一次判定，只统计 verdict 真的为 UNKNOWN 的机器；"
                     "VIOLATE 是一句结论，不是缺口",
        "fix": "gaps.py 在算每个字段的缺口前先调 judge()，把 VIOLATE / OK 排除",
        "where": "tools/gaps.py",
        "evidence": "显存缺口修正前 27 台 → 修正后 2 台",
        "affects": ["采集优先级", "缺口估算"],
    },
    {
        "id": "reason-duplicate-same-fact",
        "category": "ux",
        "severity": "medium",
        "automated": True,
        "title": "同一个事实在理由里说三遍，看起来像错了三处",
        "symptom": "「核显没有独立显存；要求独立显卡，这台是核显；硬门槛要求独立显卡，这台是核显」",
        "cause": "vramMin 检查、req.gpu 检查、hardGates 检查各自独立追加理由，"
                 "三条规则描述的是同一件事",
        "detection": "看单台机器的 reasons 长度；同一根因出现 2 次以上即合并",
        "fix": "lib_judge.merge_reasons 按根因（核显 / 内存 / 存储 / 焊死）归并",
        "where": "tools/lib_judge.py",
        "evidence": "广播电视编导组，28 台全被判 VIOLATE，改前每台 5-6 条理由",
        "affects": ["可读性", "严重程度判断"],
    },
    {
        "id": "judge-two-implementations",
        "category": "method",
        "severity": "blocker",
        "automated": False,
        "title": "判定逻辑写两份，覆盖率与推荐结论会分叉",
        "symptom": "覆盖率报「能推荐 89 台」，推荐工具却给出另一批机器，且无法判断哪个对",
        "cause": "coverage.py 里有一套 judge()，pick.py 又要用同一套判断，各自实现必然漂移",
        "detection": "搜索项目里出现两个以上 judge( 定义",
        "fix": "判定逻辑收敛到 lib_judge.judge，coverage 与 pick 都 import 它",
        "where": "tools/lib_judge.py",
        "evidence": "本轮 coverage.py 已改为转发 lib_judge.judge",
        "affects": ["结论一致性"],
    },
    {
        "id": "vram-threshold-vs-official",
        "category": "method",
        "severity": "high",
        "automated": False,
        "title": "组门槛比官方要求更严，把整档机型误杀",
        "symptom": "平面视觉组 24 台被判「不够用」，其中 23 台仅因「核显没有独立显存」；"
                   "而该组 trap 原文写的是「平面设计不吃独显」",
        "cause": "graphic.req.vramMin=2 一票否决核显，但唯一依据 Photoshop 的 "
                 "gpuNeed 是 any、vramMin 1.5。req.gpu 又写 either，三条规则互相架空",
        "detection": "把每组的 req 与 derivedFrom 软件的官方值逐项对照，看是否有项比官方更严",
        "fix": "待裁决：确认该组是否会用到 AE/C4D，再决定 vramMin 取 0 还是提到 6",
        "where": "data/majors.json",
        "evidence": "software.ps.note：「4G 显存只在 4K 及以上显示器时才是推荐值」",
        "affects": ["专业适配结论", "能推荐数量"],
    },
    {
        "id": "ports-as-r-without-need",
        "category": "method",
        "severity": "medium",
        "automated": False,
        "title": "接口被设为 R 级，但专业需求里没写清「需要哪种口」",
        "symptom": "音乐音频组 13 台、电气组 18 台卡在「缺接口」，"
                   "可这些组的 req 里完全没提接口要求",
        "cause": "field_matrix 把 ports 标为 audio/electric 的 R，"
                 "但需求实质是「能不能接声卡 / 开发板」，不是「有几个 USB-A」",
        "detection": "对比字段矩阵的 R 级与 majors.req/hardGates 里是否真有对应约束",
        "fix": "待裁决：ports 要么降为 H，要么把门槛改成结构化判断（是否有雷电/全功能 Type-C）",
        "where": "data/field_matrix.json",
        "evidence": "audio.req 无 ports 相关字段；gates 只有 storage",
        "affects": ["覆盖率", "采集优先级"],
    },
]


def main():
    doc = json.loads(P.read_text(encoding="utf-8"))
    recs = doc["pitfalls"] if isinstance(doc.get("pitfalls"), list) else doc["records"]
    have = {r.get("id") for r in recs}
    added = 0
    for item in NEW:
        if item["id"] in have:
            continue
        recs.append(item)
        added += 1

    doc["meta"]["count"] = len(recs)
    doc["meta"]["updated"] = "2026-11-09"
    P.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    by_sev = {}
    for r in recs:
        by_sev[r.get("severity")] = by_sev.get(r.get("severity"), 0) + 1
    print(f"陷阱库 {len(recs)} 条（新增 {added} 条）")
    print("  " + " · ".join(f"{k} {v}" for k, v in sorted(by_sev.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())