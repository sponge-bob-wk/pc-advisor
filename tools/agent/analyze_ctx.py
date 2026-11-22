# -*- coding: utf-8 -*-
"""分析上下文：让「那它呢」「联想呢」这类追问能接住

## 问题（2026-11-22 实测）

```
Q1: 各品牌数量对比
    → 按品牌分了 16 组，最大组 联想（98 台）

Q2: 那它平均价格多少
    解析 → {'agg': 'mean', 'field': 'price', 'filter': {}}
    结果 → 平均 ¥11,360（样本 223 台）   ← 全库平均

    期望 → 联想平均 ¥13,206（98 台）
```

**用户以为是联想的，系统给了全库的。** 差 2000 元，且回答里看不出来。

## 根因

`analyze.parse_query()` 只看当前句，不知道上一轮在分析什么。
「它」「这个」「那 XX 呢」这些指代词在孤立解析时必然丢信息。

## 解法：把上一轮的分析结果作为上下文喂给模型

```
parse_query(text, ctx={
    "last_agg": "groupby",
    "last_group_by": "brand",
    "top_group": "联想",
    "last_field": None,
})
```

模型看到上下文就知道「它」指的是 `top_group` = 联想。

**为什么不写规则硬编码「它=最大组」**：
「它」可能指用户上一句提到的任何东西，规则穷举不完。
这正是「模型理解、代码计算」的适用场景 —— 让模型看着上下文去判断。

## 上下文怎么维护

`agent._run` 里有个模块级变量 `_LAST_ANALYSIS`，
每次分析完写进去，下一轮解析时读出来。
"""
import json
import sys

# 模块级：上一轮的分析上下文。None = 没有历史。
_LAST_ANALYSIS = [None]


def remember(res, q):
    """记下这一轮分析的关键信息，供下一轮追问用。"""
    if not res or not res.get("ok"):
        return
    groups = res.get("groups") or {}
    top_group = None
    if groups:
        top_group = max(groups.items(), key=lambda x: x[1].get("n", 0))[0]
    _LAST_ANALYSIS[0] = {
        "agg": res.get("agg"),
        "field": res.get("field"),
        "group_by": q.get("group_by"),
        "top_group": top_group,
        "n": res.get("n"),
        "value": res.get("value"),
        "rows": [r.get("name") for r in (res.get("rows") or [])][:3],
    }


def get():
    """读当前上下文。"""
    return _LAST_ANALYSIS[0]


def clear():
    _LAST_ANALYSIS[0] = None


def brief(ctx=None):
    """把上下文压成一句话，喂给模型。"""
    c = ctx if ctx is not None else _LAST_ANALYSIS[0]
    if not c:
        return None
    bits = []
    if c.get("group_by"):
        bits.append("上一轮是按「%s」分组对比" % _CN_FIELD.get(
            c["group_by"], c["group_by"]))
    if c.get("top_group"):
        bits.append("其中最大的一组是「%s」" % c["top_group"])
    if c.get("agg") and not c.get("group_by"):
        bits.append("上一轮做的是 %s 统计" % c["agg"])
    if c.get("rows"):
        bits.append("排在前面的有：%s" % "、".join(c["rows"]))
    if c.get("n"):
        bits.append("样本 %d 台" % c["n"])
    return "；".join(bits) if bits else None


_CN_FIELD = {
    "brand": "品牌", "cat": "品类", "gpuKind": "显卡类型",
    "price": "价格", "perf": "性能分", "w": "重量",
    "ram": "内存", "batteryWh": "电池容量", "thicknessMm": "厚度",
    "refreshHz": "刷新率", "sizeInch": "屏幕尺寸",
}


def has_reference(text):
    """这句有没有指代（它/这个/该/那 XX 呢）。

    只用来决定「要不要把上下文喂给模型」，不做语义判断。
    """
    import re
    t = text or ""
    return bool(re.search(
        r"^那|它|这个|该[台款]|上面|刚才|前者|后者|其中|"
        r"呢[？?]?$|呢$", t))
