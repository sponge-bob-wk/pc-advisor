# -*- coding: utf-8 -*-
"""audit_parts_data.py —— 配件数据全面体检

体检项：
  ① 各品类的数量、价格区间
  ② 重复项（同名同价、同名不同价）
  ③ 异常值（价格离群、名称可疑）
  ④ 关键字段缺失率（装机要用但没抓到的）
  ⑤ 尺寸参数覆盖率
输出一份可读的报告。
"""
import io
import json
import os
import re
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"E:\电脑涨价分析"
PARTS = os.path.join(ROOT, "data", "parts.json")
FITS = os.path.join(ROOT, "data", "parts_fit.json")

# 装机必需字段（没这些就算数据不全）
NEEDED = {
    "cpu": ["价格", "插槽"],
    "motherboard": ["价格", "插槽", "板型"],
    "ram": ["价格", "代数", "容量"],
    "gpu": ["价格", "长度"],
    "ssd": ["价格", "容量"],
    "storage": ["价格", "容量"],
    "psu": ["价格", "功率"],
    "case": ["价格", "显卡限长", "板型支持"],
    "cooler": ["价格", "类型"],
}


def load(path):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return {}



# ══════════════════════════════════════════════════════════════════
#  停产 / 价格失真检测（2026-11-27 新增）
# ══════════════════════════════════════════════════════════════════
#
#  为什么需要：用户实测发现「i7 7 代 CPU 要 ¥2898」。
#  ZOL 对停产产品返回历史首发价，不是现价 —— 这些产品在二手市场
#  只值几百块，但系统会当新货推荐，还因为"价格高"被当成高端件。
#
#  判定两条：
#    ① 停产（代数老）
#    ② 价格倒挂（老世代比新世代同档还贵）← 更硬的指标

import re as _re


def _cpu_dead(name):
    if "Ultra" in name:
        return None
    s = name.replace("酷睿", "").replace(" ", "")
    m = _re.search(r"i[3579][-\s]?(\d{4,5})(?!\d)", s)
    if m:
        num = m.group(1)
        gen = int(num[0]) if len(num) == 4 else int(num[:2])
        return gen if gen <= 9 else None
    m = _re.search(r"Ryzen\s*[3579]\s*(\d)\d{3}", name)
    if m and int(m.group(1)) <= 2:
        return int(m.group(1)) + 100     # 标记 AMD
    return None


def _gpu_key(name):
    n = name.upper()
    m = _re.search(r"RTX\s*(\d{2})\s*(\d{2})", n)
    if m:
        return "NV", int(m.group(1)), int(m.group(2))
    m = _re.search(r"GTX\s*(1\d)\s*(\d{2})", n)
    if m:
        return "NV", int(m.group(1)), int(m.group(2))
    m = _re.search(r"RX\s*(\d)(\d{3})", n)
    if m:
        return "AMD", int(m.group(1)), int(m.group(2)[:2])
    return None, None, None


def legacy_or_inverted(data):
    """返回 [(cat, name, price, reason)] —— 停产或价格失真的配件。"""
    out = []

    # ── CPU ──
    for c in (data.get("cpu", {}).get("items") or []):
        gen = _cpu_dead(c["name"])
        if gen is not None:
            out.append(("cpu", c["name"], c.get("price") or 0,
                        "已停产（%s），ZOL 参考价是历史首发价"
                        % ("AMD Zen1/2" if gen > 100 else "Intel %d 代"
                           % gen)))

    # ── GPU：停产 + 价格倒挂 ──
    gpus = data.get("gpu", {}).get("items") or []
    from collections import defaultdict
    tier_min = defaultdict(dict)
    for g in gpus:
        if not g.get("price"):
            continue
        v, gen, tier = _gpu_key(g["name"])
        if v is None:
            continue
        cur = tier_min[(v, tier)].get(gen)
        if cur is None or g["price"] < cur:
            tier_min[(v, tier)][gen] = g["price"]

    for g in gpus:
        n, p = g["name"], g.get("price") or 0
        v, gen, tier = _gpu_key(n)
        # 停产系
        if _re.search(r"RTX\s*20\d{2}", n, _re.I) or \
           _re.search(r"GTX\s*1\d{3}", n, _re.I) or \
           _re.search(r"RX\s*5\d{3}", n, _re.I):
            out.append(("gpu", n, p, "已停产（RTX20/GTX/RX5000 系）"))
            continue
        # 价格倒挂
        if v is not None and p:
            newer = [x for gg, x in tier_min[(v, tier)].items() if gg > gen]
            if newer and p > min(newer):
                out.append(("gpu", n, p,
                            "价格倒挂：新一代同档只要 ¥%d，这张 ¥%d"
                            % (min(newer), int(p))))
    return out


def main():
    parts = load(PARTS)
    fits = load(FITS)

    LABEL = {k: (v.get("label") or k) for k, v in parts.items()}
    fit_items = {k: (v.get("items") or {}) for k, v in fits.items()}

    print("=" * 80)
    print("配件数据体检")
    print("=" * 80)
    print()

    # ── ⓪ 停产 / 价格失真（2026-11-27 新增）──
    # 用户实测：「i7 7 代 CPU 要 ¥2898」—— ZOL 对停产产品返回
    # 历史首发价。必须每次体检都报，否则刷新会重新抓回来。
    _legacy = legacy_or_inverted(parts)
    if _legacy:
        print("【⓪】停产 / 价格失真")
        print("-" * 80)
        for _c, _n, _p, _w in _legacy:
            print("  ⚠️  [%s] %-36s ¥%-7d %s"
                  % (_c, _n[:34], int(_p), _w))
        print()
        print("  → 这些配件的历史价不可信，建议从 parts.json 移除")
        print()


    # ── ① 数量与价格 ──
    print("【一】规模与价格")
    print("-" * 80)
    print("  %-12s %-8s %6s %10s %10s %10s"
          % ("品类", "名称", "数量", "最低价", "中位价", "最高价"))
    total = 0
    for k, v in parts.items():
        items = v.get("items") or []
        if not items:
            print("  %-12s %-8s %6d  （无数据）" % (k, LABEL.get(k, k), 0))
            continue
        ps = sorted(x["price"] for x in items if x.get("price"))
        if not ps:
            continue
        med = ps[len(ps) // 2]
        print("  %-12s %-8s %6d %10d %10d %10d"
              % (k, LABEL.get(k, k), len(items), ps[0], med, ps[-1]))
        total += len(items)
    print("  %-12s %-8s %6d" % ("合计", "", total))
    print()

    # ── ② 重复项 ──
    print("【二】重复项")
    print("-" * 80)
    any_dup = False
    for k, v in parts.items():
        items = v.get("items") or []
        cn = Counter(x["name"] for x in items)
        dups = {n: c for n, c in cn.items() if c > 1}
        if dups:
            any_dup = True
            print("  %s：%d 组重复" % (LABEL.get(k, k), len(dups)))
            for n, c in list(dups.items())[:3]:
                prices = [x["price"] for x in items if x["name"] == n]
                print("      %-40s ×%d  价格 %s"
                      % (n[:38], c, prices))
    if not any_dup:
        print("  ✓ 无重复")
    print()

    # ── ③ 异常值 ──
    print("【三】价格异常值（离群检测）")
    print("-" * 80)
    for k, v in parts.items():
        items = v.get("items") or []
        ps = sorted(x["price"] for x in items if x.get("price"))
        if len(ps) < 5:
            continue
        # 用「中位数的 1/8 以下」或「8 倍以上」当粗略离群
        med = ps[len(ps) // 2]
        low = [x for x in items
               if x.get("price") and x["price"] < med / 8]
        high = [x for x in items
                if x.get("price") and x["price"] > med * 8]
        if low or high:
            print("  %s（中位 ¥%d）" % (LABEL.get(k, k), med))
            for x in low[:2]:
                print("      ⚠ 超低 ¥%-7d %s" % (x["price"], x["name"][:40]))
            for x in high[:2]:
                print("      ⚠ 超高 ¥%-7d %s" % (x["price"], x["name"][:40]))
    print()

    # ── ④ 尺寸参数覆盖率 ──
    print("【四】尺寸参数覆盖率（装机校验要用）")
    print("-" * 80)
    for k in ("gpu", "case", "cooler", "motherboard"):
        items = (parts.get(k) or {}).get("items") or []
        got = fit_items.get(k) or {}
        n = len(got)
        pct = 100.0 * n / len(items) if items else 0
        flag = "✓" if pct >= 80 else ("⚠" if pct >= 40 else "✗")
        print("  %s %-12s %3d / %3d  %5.1f%%"
              % (flag, LABEL.get(k, k), n, len(items), pct))
    print()

    # ── ⑤ 关键字段缺失 ──
    print("【五】装机关键字段缺失情况")
    print("-" * 80)

    # 显卡长度
    gpu_items = (parts.get("gpu") or {}).get("items") or []
    gpu_fit = fit_items.get("gpu") or {}
    no_size = [x["name"] for x in gpu_items
               if x["name"] not in gpu_fit
               or "产品尺寸" not in (gpu_fit[x["name"]].get("params") or {})]
    print("  显卡缺「产品尺寸」: %d / %d" % (len(no_size), len(gpu_items)))
    for n in no_size[:3]:
        print("      %s" % n[:50])

    # 显卡建议电源
    has_psu = [x for x in gpu_items
               if x["name"] in gpu_fit
               and "建议电源" in (gpu_fit[x["name"]].get("params") or {})]
    print("  显卡有「建议电源」: %d / %d" % (len(has_psu), len(gpu_items)))

    # 机箱显卡限长
    case_items = (parts.get("case") or {}).get("items") or []
    case_fit = fit_items.get("case") or {}
    no_lim = [x["name"] for x in case_items
              if x["name"] not in case_fit
              or "显卡限长" not in (case_fit[x["name"]].get("params") or {})]
    print("  机箱缺「显卡限长」: %d / %d" % (len(no_lim), len(case_items)))
    for n in no_lim[:3]:
        print("      %s" % n[:50])

    # 散热器类型
    cool_items = (parts.get("cooler") or {}).get("items") or []
    cool_fit = fit_items.get("cooler") or {}
    no_type = [x["name"] for x in cool_items
               if x["name"] not in cool_fit
               or "散热方式" not in (cool_fit[x["name"]].get("params") or {})]
    print("  散热器缺「散热方式」: %d / %d" % (len(no_type), len(cool_items)))
    print()

    # ── ⑥ 名称质量问题 ──
    print("【六】名称质量问题")
    print("-" * 80)
    for k, v in parts.items():
        items = v.get("items") or []
        long_names = [x for x in items if len(x["name"]) > 45]
        weird = [x for x in items
                 if re.search(r"[<>\"]|&[a-z]+;", x["name"])]
        if long_names or weird:
            print("  %s：" % LABEL.get(k, k))
            if long_names:
                print("      超长名（>45字）: %d 个" % len(long_names))
                for x in long_names[:2]:
                    print("          %s" % x["name"][:60])
            if weird:
                print("      含异常字符: %d 个" % len(weird))
                for x in weird[:2]:
                    print("          %s" % x["name"][:50])
    print()

    # ── ⑦ 建议 ──
    print("=" * 80)
    print("建议")
    print("=" * 80)
    print("  1. 显卡尺寸覆盖率 %d/%d，没抓到的在装机时会跳过尺寸校验"
          % (len(gpu_fit), len(gpu_items)))
    print("  2. 机箱限长覆盖率 %d/%d" % (len(case_fit), len(case_items)))
    print("  3. 显卡「建议电源」可替代 CPU/GPU 功耗公式，更准")
    return 0


if __name__ == "__main__":
    sys.exit(main())
