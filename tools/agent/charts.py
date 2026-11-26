# -*- coding: utf-8 -*-
"""charts.py —— 把 overview 数据转成图表（markdown 可渲染的格式）

## 为什么用 Mermaid 而不是 Chart.js

open-webui 内置了两种渲染能力：
  · Mermaid  —— markdown 里写 ```mermaid 代码块，**自动渲染**
  · Chart.js —— 需要模型主动调用 render_visualization 工具

我们的 route-v6-q8 是意图分类模型，不会主动调工具。
而 Mermaid 只要在 markdown 里写代码块就行 —— 桥接层能直接控制。

所以选 Mermaid：**不依赖模型能力，纯文本协议就能出图。**

## Mermaid 能画什么

  · 柱状图  —— xychart-beta（Mermaid 10.9+ 支持）
  · 饼图    —— pie
  · 时间线  —— timeline

⚠️ xychart-beta 需要较新版本。如果 open-webui 的 Mermaid 版本较老
   会渲染成代码块。所以这里**同时给表格兜底** —— 图挂了还有数据。

## 数据来源

全部来自 `/api/overview`，那个接口的注释写得很清楚：
  「所有数字在这里算好，前端只负责画」
所以这里**只做格式转换，不做任何计算** —— 保持"数字零写权限"。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")


def _pct(n, total):
    """百分比，保留整数。"""
    if not total:
        return 0
    return int(round(100.0 * n / total))


def category_bar(ov):
    """品类分布柱状图 + 表格兜底。

    🔴 字段名照 `/api/overview` 的实际返回：
        {"cat": "thin", "name": "轻薄本", "n": 112, "reviewed": 10,
         "priceMin": 1999, "priceMax": 38999, "priceMedian": 6486.575,
         "withWeight": 96}
       `name` 后端已经给了中文，不要自己再映射一遍。
    """
    by_cat = ov.get("byCat") or []
    if not by_cat:
        return ""

    total = ov.get("total") or 0

    out = ["| 品类 | 台数 | 价格中位 |", "|---|---:|---:|"]
    for c in by_cat:
        nm = c.get("name") or c.get("cat") or ""
        n = c.get("n") or 0
        mp = c.get("priceMedian")
        price = ("¥%d" % round(mp)) if mp else "—"
        out.append("| %s | %d（%d%%） | %s |" % (nm, n, _pct(n, total), price))
    out.append("")

    labels = ", ".join('"%s"' % (c.get("name") or c.get("cat") or "")
                       for c in by_cat)
    values = ", ".join(str(c.get("n") or 0) for c in by_cat)
    out.append("```mermaid")
    out.append("xychart-beta")
    out.append('    title "各品类机型数量"')
    out.append("    x-axis [%s]" % labels)
    out.append("    y-axis \"台数\" 0 --> %d"
               % (max((c.get("n") or 0) for c in by_cat) + 20))
    out.append("    bar [%s]" % values)
    out.append("```")

    return "\n".join(out)


def price_distribution(ov):
    """价格区间分布。

    🔴 2026-11-26 修正字段名。`/api/overview` 返回的真实结构是：
        {"label": "4000 以下", "min": 0, "max": 4000, "n": 17, "reviewed": 7}

        第一版我猜成了 lo/hi，结果标签全是「—」和「?」。
        **已经给好的 label 直接用，不要自己再拼** —— 后端拼的比前端准
        （「4000 以下」「1 万以上」这种自然语言，我自己拼会变成「<4000」）。
    """
    buckets = ov.get("priceBuckets") or []
    if not buckets:
        return ""

    out = ["| 价格区间 | 台数 |", "|---|---:|"]
    for b in buckets:
        label = b.get("label") or "—"
        out.append("| %s | %d |" % (label, b.get("n") or 0))
    out.append("")

    # Mermaid 的 x 轴标签要短，把「4000–5000」压成「4-5k」
    def _short(b):
        lo, hi = b.get("min"), b.get("max")
        if hi is None and lo is not None:
            return "%.0fk+" % (lo / 1000.0)
        if lo is not None and hi is not None:
            return "%.0f-%.0fk" % (lo / 1000.0, hi / 1000.0)
        return "?"

    labels = ", ".join('"%s"' % _short(b) for b in buckets)
    values = ", ".join(str(b.get("n") or 0) for b in buckets)

    out.append("```mermaid")
    out.append("xychart-beta")
    out.append('    title "价格区间分布（台）"')
    out.append("    x-axis [%s]" % labels)
    out.append("    y-axis \"台数\" 0 --> %d"
               % (max((b.get("n") or 0) for b in buckets) + 20))
    out.append("    bar [%s]" % values)
    out.append("```")

    return "\n".join(out)


def weight_compare(ov):
    """各品类重量对比（只有库里记了重量的才算）。"""
    by_cat = ov.get("byCat") or []
    # ⚠️ overview 里**没有 weightMedian** —— 只有 withWeight（有重量的台数）。
    #    所以重量对比图这里给「覆盖情况」，不给中位数。
    #    要真中位数得走 /api/stats 或 core.effective_weight，那属于重复计算，
    #    按「数字零写权限」原则这里不做。
    rows = [c for c in by_cat if c.get("withWeight")]
    if not rows:
        return ""

    out = ["| 品类 | 有重量记录 | 占比 |", "|---|---:|---:|"]
    for c in rows:
        n = c.get("n") or 0
        w = c.get("withWeight") or 0
        out.append("| %s | %d / %d | %d%% |"
                   % (c.get("name") or c.get("cat"), w, n, _pct(w, n)))
    out.append("")

    labels = ", ".join('"%s"' % NAME.get(c.get("cat"), c.get("cat"))
                       for c in rows)
    values = ", ".join("%.2f" % c.get("weightMedian") for c in rows)
    out.append("```mermaid")
    out.append("xychart-beta")
    out.append('    title "各品类重量中位数（kg）"')
    out.append("    x-axis [%s]" % labels)
    out.append("    y-axis \"kg\" 0 --> %d"
               % (int(max(c.get("weightMedian") for c in rows)) + 1))
    out.append("    bar [%s]" % values)
    out.append("```")

    return "\n".join(out)

