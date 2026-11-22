# -*- coding: utf-8 -*-
"""analyze_price_suspects.py —— 调查 15 台可疑价格

## 目的：先判断「差异是真的还是假的」，再决定要不要裁决

价格交叉验证报 15 台 >30%。但**差异大不等于数据错**：

  ① **SKU 不同** —— 同型号不同配置（16G vs 32G），价格本来就差
  ② **口径不同** —— zolRef 是「媒体参考价」，平台价是「当前挂牌价」
  ③ **时间差** —— 参考价旧、平台价新（涨价潮）
  ④ **真错** —— 抓错页面、抓到配件、单位错

判据：**先看「这台有没有到手价」和「配置是否单一」**。

关键洞察：`real`（到手价）为 null 时，这台只有标价来源。
标价本来就不可靠（划线价、不同店铺、不同 SKU），
这种机器**不该用标价差异去裁决** —— 应该标记「无实价，仅供参考」。
"""
import io
import json
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8')

D = r'E:\电脑涨价分析\tools\agent'
POOL = r'E:\电脑涨价分析\data\machine_pool.json'


def get_all():
    r = subprocess.run([sys.executable, 'price_cross_check.py', '--json'],
                       cwd=D, capture_output=True, text=True,
                       encoding='utf-8')
    i = (r.stdout or "").find('{')
    if i < 0:
        return {}
    return json.loads(r.stdout[i:])


def main():
    d = get_all()
    items = d.get("items") or []
    sus = [x for x in items if x.get("level") == "suspect"]

    pool = json.load(io.open(POOL, encoding='utf-8'))
    by_id = {m.get("id"): m for m in pool.get("machines") or []}

    print("可核对 %d 台，可疑 %d 台" % (len(items), len(sus)))
    print()

    # 分类统计
    cat = {"no_real": [], "has_real": [], "multi_sku": []}
    for x in sus:
        m = by_id.get(x["id"]) or {}
        spec = m.get("spec") or ""
        if x.get("real") is None:
            cat["no_real"].append((x, m))
        else:
            cat["has_real"].append((x, m))
        if any(k in spec for k in ("/", "或", "可选")):
            cat["multi_sku"].append((x, m))

    print("=== 分类 ===")
    print("  无到手价（real=null）: %d 台 ← 只有标价，不可靠" % len(cat["no_real"]))
    print("  有到手价:              %d 台 ← 有实价可参考" % len(cat["has_real"]))
    print("  配置含多个 SKU:        %d 台 ← 价差可能正常" % len(cat["multi_sku"]))
    print()

    print("=" * 72)
    print("明细")
    print("=" * 72)
    for x in sus:
        m = by_id.get(x["id"]) or {}
        srcs = x.get("sources") or {}
        print()
        print("  %s" % x["name"][:46])
        print("    id=%s  差异 %+.0f%%" % (x["id"], x["spread"] * 100))
        for k, v in sorted(srcs.items()):
            print("      %-10s ¥%s" % (k, v))
        print("      到手价: %s" % ("无" if x.get("real") is None
                                  else "¥%s" % x["real"]))
        spec = m.get("spec") or ""
        print("      配置: %s" % (spec[:60] or "(空)"))
        # 判定
        if x.get("real") is None:
            print("      → 判定：**无实价**，标价差异不作为错误处理")
        elif any(k in spec for k in ("/", "或", "可选")):
            print("      → 判定：配置多 SKU，价差可能正常，需人工确认")
        else:
            print("      → 判定：**需核查**（有到手价 + 单一配置，价差偏大）")


if __name__ == "__main__":
    main()
