# -*- coding: utf-8 -*-
"""analyze.py —— 结构化数据分析引擎

## 为什么需要这个模块（毕设题目要求）

题目是「私有部署蒸馏微调的**数据分析**大模型」，
但系统原来**完全没有数据分析能力**：
问「有多少台有独显」「各品牌售后评分对比」「8000以内平均多重」，
全被当成"选机需求"，回一句「是笔记本还是台式？」。

## 架构：模型理解 + 代码计算

这是本项目「模型理解与确定性计算分离」原则的又一次应用：

```
用户问「8000以内打游戏的机器平均多重」
  ↓ 模型：理解成 {agg: mean, field: w, filter: {price<=8000, cat=gaming}}
  ↓ 代码：确定性计算 → 1.98 kg（样本 23 台）
  ↓ 模型：组织成人话
```

**模型不碰数字。** 所有统计量由代码算，理由：
  · 聚合计算是确定性算法，模型没有优势
  · 模型算 223 台的平均值必然出错（实测过它编「最贵的一台」）
  · 答辩要能说清「每个数字怎么来的」→ 代码可复现

## 支持的操作

| 操作 | 例子 |
|---|---|
| count（计数） | 有多少台有独显 |
| mean / median / min / max（统计量） | 8000 以内的平均重量 |
| groupby（分组对比） | 各品牌售后评分对比 |
| distribution（分布） | 32G 内存的机型价格分布 |
| top / bottom（排行） | 性能最强的五台 |

## 用法

    from analyze import analyze, parse_query
    q = parse_query('8000以内游戏本平均多重')     # 模型解析
    r = analyze(q)                                # 代码计算
    print(r['value'], r['n'], r['text'])
"""
import io
import json
import os
import re
import statistics
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core

# ── 可分析的字段（白名单：只认这些，不许模型自创字段名）──
FIELDS = {
    "price": "价格",
    "perf": "性能分",
    "w": "重量",
    "ram": "内存容量",
    "batteryWh": "电池容量",
    "thicknessMm": "厚度",
    "refreshHz": "刷新率",
    "sizeInch": "屏幕尺寸",
    "brand": "品牌",
    "cat": "品类",
    "gpuKind": "显卡类型",
}

# 中文别名 → 字段名（模型可能给中文）
FIELD_ALIAS = {
    "价格": "price", "价钱": "price", "多少钱": "price", "到手价": "price",
    "性能": "perf", "性能分": "perf", "跑分": "perf",
    "重量": "w", "多重": "w", "kg": "w",
    "内存": "ram", "内存容量": "ram",
    "电池": "batteryWh", "电池容量": "batteryWh",
    "厚度": "thicknessMm",
    "刷新率": "refreshHz",
    "屏幕尺寸": "sizeInch", "尺寸": "sizeInch",
    "品牌": "brand",
    "品类": "cat", "类型": "cat",
    "显卡": "gpuKind", "显卡类型": "gpuKind",
}

AGG_ALIAS = {
    "count": "count", "数量": "count", "多少台": "count", "几台": "count",
    "mean": "mean", "平均": "mean", "均值": "mean",
    "median": "median", "中位数": "median",
    "min": "min", "最小": "min", "最低": "min", "最便宜": "min",
    "max": "max", "最大": "max", "最高": "max", "最贵": "max",
    "groupby": "groupby", "分组": "groupby", "对比": "groupby",
    "distribution": "distribution", "分布": "distribution",
    "top": "top", "排行": "top", "最强": "top",
}


def _val(o, field):
    """取字段值，统一数字/字符串。

    🔴 2026-11-21：**重量必须走 core.effective_weight**。
       test_weight 的 W8 是结构性检查：出口层出现裸 `weightKg` 就报错
       （重量有四个出口，各读各的字段会漂 —— 这个项目复现过 9 次）。

       这里的 `field == "w"` 分支就是为了避开字面量 `weightKg`。
       字段名对外仍叫 weightKg（见 FIELDS 表），内部取值走单一入口。
    """
    if field == "ram":
        return core.ram_of(o)
    if field == "w":                      # 重量：走单一入口，不裸读
        return core.effective_weight(o)
    if field == "sizeInch":
        return (o.get("screen") or {}).get("sizeInch")
    if field == "refreshHz":
        return (o.get("screen") or {}).get("refreshHz")
    if field == "gpuKind":
        k = o.get("gpuKind")
        return {"discrete": "独显", "integrated": "核显"}.get(k, k)
    return o.get(field)


def _apply_filter(offs, flt):
    """按条件筛。flt 是 {字段: 值} 或 {字段: {min,max}}。"""
    out = list(offs)
    for k, v in (flt or {}).items():
        k = FIELD_ALIAS.get(k, k)
        if isinstance(v, dict):
            lo, hi = v.get("min"), v.get("max")
            out = [o for o in out
                   if (_val(o, k) is not None
                       and (lo is None or _val(o, k) >= lo)
                       and (hi is None or _val(o, k) <= hi))]
        elif isinstance(v, (int, float)):
            # 数字等值 → 当成 >= （"32G内存"意思是至少 32G）
            out = [o for o in out
                   if _val(o, k) is not None and _val(o, k) >= v]
        else:
            # 字符串：品牌/品类/显卡类型 做包含匹配
            sv = str(v)
            if k == "cat":
                sv = {"游戏本": "gaming", "轻薄本": "thin",
                      "gaming": "gaming", "thin": "thin"}.get(sv, sv)
            elif k == "gpuKind":
                sv = {"独显": "discrete", "核显": "integrated",
                      "discrete": "discrete", "integrated": "integrated"}.get(sv, sv)
                out = [o for o in out if o.get("gpuKind") == sv]
                continue
            out = [o for o in out if sv and sv in str(_val(o, k) or "")]
    return out


def analyze(q, offs=None):
    """执行一次分析。q 是 parse_query() 的输出（或手写同构 dict）。

    返回：
      {
        "ok": bool, "n": 样本数, "agg": 操作, "field": 字段,
        "value": 主结果, "groups": {组: 统计}, "rows": [...],
        "text": 人话摘要, "note": 数据局限说明
      }
    """
    if offs is None:
        offs = core.pool_offers()

    agg = AGG_ALIAS.get(str(q.get("agg") or "").lower(), q.get("agg") or "count")
    field = FIELD_ALIAS.get(str(q.get("field") or ""), q.get("field"))
    flt = q.get("filter") or {}
    group_by = FIELD_ALIAS.get(str(q.get("group_by") or ""), q.get("group_by"))

    pool = _apply_filter(offs, flt)
    n = len(pool)

    res = {"ok": True, "n": n, "agg": agg, "field": field,
           "filter": flt, "groups": {}, "rows": [], "note": ""}

    if n == 0:
        res["ok"] = False
        res["text"] = "按这些条件筛完，库里一台都没有。"
        return res

    # 只对有值的样本算（缺失不算 0，这是硬规则）
    vals = [_val(o, field) for o in pool] if field else []
    nums = [v for v in vals if isinstance(v, (int, float))]

    if agg == "count":
        res["value"] = n
        res["text"] = "%d 台" % n
        if field:
            res["note"] = "（这是筛完的总台数）"
        return res

    if agg in ("mean", "median", "min", "max"):
        if not nums:
            res["ok"] = False
            res["text"] = "这些机器里没有「%s」的数据，算不了。" % (
                FIELDS.get(field, field))
            return res
        fn = {"mean": statistics.mean, "median": statistics.median,
              "min": min, "max": max}[agg]
        v = fn(nums)
        res["value"] = round(v, 2)
        res["n"] = len(nums)
        label = {"mean": "平均", "median": "中位数",
                 "min": "最低", "max": "最高"}[agg]
        res["text"] = "%s %s（样本 %d 台）" % (
            label, _fmt(field, v), len(nums))
        if len(nums) < n:
            res["note"] = "（%d 台里有 %d 台这个字段有值，其余没记录）" % (
                n, len(nums))
        return res

    if agg == "groupby":
        if not group_by:
            group_by = "brand"
        buckets = {}
        for o in pool:
            g = _val(o, group_by)
            if g in (None, ""):
                continue
            buckets.setdefault(str(g), []).append(o)
        groups = {}
        for g, arr in buckets.items():
            if field and agg != "count":
                gv = [_val(o, field) for o in arr]
                gv = [x for x in gv if isinstance(x, (int, float))]
                if gv:
                    groups[g] = {"n": len(arr),
                                 "mean": round(statistics.mean(gv), 2),
                                 "min": round(min(gv), 2),
                                 "max": round(max(gv), 2)}
                else:
                    groups[g] = {"n": len(arr)}
            else:
                groups[g] = {"n": len(arr)}
        # 按样本数降序（并列按名称）
        res["groups"] = dict(sorted(groups.items(),
                                    key=lambda x: (-x[1]["n"], x[0])))
        res["text"] = "按「%s」分了 %d 组" % (
            FIELDS.get(group_by, group_by), len(groups))
        return res

    if agg == "distribution":
        if not nums:
            res["ok"] = False
            res["text"] = "没有可分布的数值。"
            return res
        lo, hi = min(nums), max(nums)
        if lo == hi:
            res["value"] = {str(lo): len(nums)}
            res["text"] = "全部都是 %s" % _fmt(field, lo)
            return res
        # 分成 5 档
        span = (hi - lo) / 5.0
        edges = [lo + span * i for i in range(6)]
        buckets = {}
        for v in nums:
            idx = min(int((v - lo) / span), 4) if span else 0
            key = "%s~%s" % (_fmt(field, edges[idx]),
                             _fmt(field, edges[idx + 1]))
            buckets[key] = buckets.get(key, 0) + 1
        res["value"] = buckets
        res["text"] = "%s 分布在 %s ~ %s 之间（样本 %d 台）" % (
            FIELDS.get(field, field), _fmt(field, lo), _fmt(field, hi),
            len(nums))
        return res

    if agg in ("top", "bottom"):
        if not nums:
            res["ok"] = False
            res["text"] = "没有可排序的数值。"
            return res
        k = int(q.get("limit") or 5)
        pairs = [(_val(o, field), o) for o in pool
                 if isinstance(_val(o, field), (int, float))]
        pairs.sort(key=lambda x: x[0], reverse=(agg == "top"))
        res["rows"] = [{"name": o.get("name"), "value": round(v, 2),
                        "price": o.get("price")} for v, o in pairs[:k]]
        res["text"] = "%s 的 %s：" % (
            ("最高" if agg == "top" else "最低"), FIELDS.get(field, field))
        return res

    res["ok"] = False
    res["text"] = "不认识的分析方式：%s" % agg
    return res


def _fmt(field, v):
    """按字段加单位。"""
    if not isinstance(v, (int, float)):
        return str(v)
    if field == "price":
        return "¥%s" % format(int(round(v)), ",")
    if field == "w":
        return "%g kg" % round(v, 2)
    if field == "ram":
        return "%gG" % v
    if field == "perf":
        return "%g 分" % round(v, 1)
    if field == "batteryWh":
        return "%g Wh" % v
    if field == "thicknessMm":
        return "%g mm" % round(v, 1)
    if field == "refreshHz":
        return "%gHz" % v
    if field == "sizeInch":
        return "%g 寸" % v
    return "%g" % round(v, 2)


# ══════════════════════════════════════════════════════════════
# 模型解析层：把自然语言 → 结构化查询
# ══════════════════════════════════════════════════════════════

PARSE_SYS = """你是数据分析助手。把用户的问题解析成结构化查询。

可用的「操作」(agg)：
  count        计数（多少台）
  mean         平均值
  median       中位数
  min          最小值（最便宜/最轻）
  max          最大值（最贵/最强）
  groupby      分组对比（各品牌/各品类）
  distribution 分布（价格区间）
  top          排行（前几台）
  bottom       排行（后几台）

可用的「字段」(field)：
  price(价格) perf(性能分) w(重量) ram(内存) batteryWh(电池)
  thicknessMm(厚度) refreshHz(刷新率) sizeInch(屏幕尺寸)
  brand(品牌) cat(品类) gpuKind(显卡类型)

筛选条件 (filter) 用同样的字段名，数字条件写 {"max": 8000} 或 {"min": 32}。

只输出 JSON：
{"agg":"...","field":"...","filter":{...},"group_by":"...","limit":5}

例子：
  「有多少台有独显」→ {"agg":"count","field":"","filter":{"gpuKind":"独显"}}
  「8000以内平均多重」→ {"agg":"mean","field":"w","filter":{"price":{"max":8000}}}
  「8000以内**游戏本**平均多重」→ {"agg":"mean","field":"w",
      "filter":{"price":{"max":8000},"cat":"gaming"}}
  「各品牌售后对比」→ {"agg":"groupby","field":"","group_by":"brand"}
  「32G内存的价格分布」→ {"agg":"distribution","field":"price","filter":{"ram":{"min":32}}}
  「性能最强的五台」→ {"agg":"top","field":"perf","limit":5}

⚠️ **用户提到的每个筛选条件都要写进 filter，一个都不能漏。**
   「8000以内的游戏本」有两个条件：价格上限 + 品类=游戏本。
   漏掉品类会算错样本（实测：漏了 cat，18 台变 80 台，2.49kg 变 1.75kg）。
   品类词对照：游戏本→gaming，轻薄本→thin，台式→desktop。

⚠️ 不许自创字段名，只能用上面列的。
⚠️ 用户没提的筛选条件不要加。"""


def parse_query(text):
    """让模型把自然语言解析成结构化查询。

    模型只做「理解」，不碰数字 —— 解析结果交给 analyze() 算。
    """
    import agent
    try:
        out = agent.chat(
            [{"role": "system", "content": PARSE_SYS},
             {"role": "user", "content": text}],
            fmt="json", num_predict=200)
        d = json.loads(out)
        if not isinstance(d, dict):
            return None
        # 字段名统一（模型可能给中文）
        if d.get("field"):
            d["field"] = FIELD_ALIAS.get(d["field"], d["field"])
        if d.get("group_by"):
            d["group_by"] = FIELD_ALIAS.get(d["group_by"], d["group_by"])
        # 🔴 2026-11-21：**品类兜底**。模型会漏掉品类条件
        #    （实测「8000以内的游戏本平均多重」漏了 cat=gaming，
        #    导致 18 台变 80 台、2.49kg 变 1.75kg）。
        #    判据：原话里明确出现品类词，而解析结果没有 cat → 补上。
        flt = d.get("filter") or {}
        if isinstance(flt, dict) and "cat" not in flt:
            t = text or ""
            if "游戏本" in t or "电竞" in t:
                flt["cat"] = "gaming"
            elif "轻薄本" in t or "超极本" in t:
                flt["cat"] = "thin"
            elif "台式" in t:
                flt["cat"] = "desktop"
            d["filter"] = flt
        return d
    except Exception:
        return None
