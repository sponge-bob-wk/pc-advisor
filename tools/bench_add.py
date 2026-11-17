# -*- coding: utf-8 -*-
"""向 bench.json 补录跑分数据（2026-11-16）

## 为什么单独一个脚本，而不是手改 JSON

手改 JSON 会踩三个坑，都实际发生过：
  ① `observedR23.variants` 忘了加 → 多来源信息丢了，以后没法复核
  ② `spreadPct` 手算 → 和 variants 对不上（一个是过去某次算的，一个是最新的）
  ③ `perfCurrent` 不知道按哪个值算 → 上下两条同一颗 CPU 用了不同口径

所以：**数据只填 variants（原始实测），其余全部由代码算**。
    perfCurrent = 按 R23 多核，用和 prices.json 同一把尺子换算。

## perf 的尺子（必须和人工层一致，见 prices.json 的 meta）

    电脑：perf = 100 × (0.5×单核/2400 + 0.5×多核/40000)
    手机：perf = 100 × (0.5×单核/4000 + 0.5×多核/12000)

## 🔴 必须记住的一条现实（本次实测发现）

同一颗 CPU 在不同机型上，多核分能差 **2 倍以上**：
    Ultra 7 356H：ThinkPad X1 Carbon（超轻薄，低功耗墙）R23 多核 9019
                  Acer Helios Neo 16S（高性能）      R23 多核 20298
所以 `variants` 不是"多个来源测同一个东西"，而是"同一颗芯片在不同机身里的真实表现"。
`note` 里必须写清每个 variant 对应的机型，否则以后没人知道 9019 是怎么来的。

用法：
    python tools/bench_add.py            # 预览：算出来长什么样，不写文件
    python tools/bench_add.py --write    # 写回 data/bench.json（先备份）
"""
import argparse
import io
import json
import os
import shutil
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(os.path.dirname(HERE), "data", "bench.json")

# ============================================================
# 待补录的数据。每一条都必须带 source（站点）+ machine（机型）+ url。
# 没有来源的数字一律不许写进来 —— 这是项目红线。
# ============================================================
DATA = [
    {
        "name": "i9-14900HX", "vendor": "Intel", "cores": 24,
        # (单核, 多核) —— 每个都对应一个具体机型，不是"多来源平均"
        "r23_variants": [
            (2192, 27690, "HyperX 暗影精灵 PRO 16（200W 释放）"),
            (2197, 29035, "七彩虹将星 X17 Pro Max（顶级散热）"),
            (2239, 34973, "cputime 数据库均值 —— 偏高 20%+，疑含高释放/桌面条件，仅记录不采信"),
        ],
        "r24": (128, 1520),
        "r26": (517, 6233),
        "gb6_variants": [
            (3072, 18217, "七彩虹将星 X17 Pro Max（太平洋电脑网实测）"),
            (3029, 18430, "cputime 数据库均值"),
        ],
        "source": "ZOL 中关村在线（暗影精灵 PRO 16 评测）+ 太平洋电脑网（七彩虹将星 X17 Pro Max）+ cputime 数据库",
        "note": "24核32线程(8P+16E)/36MB L3/基础55W。多核 27690~34973 跨度大："
                "实测 200W 整机释放的游戏本约 2.77~2.90 万，cputime 的 34973 "
                "接近桌面级条件，不作为移动端代表值。R26 数据来自 ZOL 同一台机。",
    },
    {
        "name": "Ultra 7 356H", "vendor": "Intel", "cores": 12,
        "r23_variants": [
            (1760, 9019, "ThinkPad X1 Carbon Aura 2026（超轻薄，低功耗墙）"),
            (1910, 20298, "Acer Predator Helios Neo 16S AI"),
            (1968, 19722, "百度百科词条综合值"),
            (2036, 17662, "雷神 ZERO Air 15（快科技实测，160W 整机释放）"),
        ],
        "r24": (116, 1052),
        "r26": (477, 4172),
        "gb6_variants": [
            (2654, 16204, "ThinkPad X1 Carbon Aura 2026"),
            (2755, 15367, "Acer Predator Helios Neo 16S AI"),
            (2765, 15961, "laptopmedia 数据库（低值）"),
            (2913, 17081, "laptopmedia 数据库（高值）"),
        ],
        "source": "百度百科（ThinkPad X1 Carbon 词条）+ NOVA 資訊廣場（Acer Helios Neo 16S 开箱）"
                  "+ 快科技（雷神 ZERO Air 15 评测）+ laptopmedia 数据库",
        "note": "12核/18MB L3/Panther Lake。**同一颗芯片多核能差 2.25 倍**："
                "ThinkPad X1 超轻薄机身只跑到 9019，游戏本模具能到 20298。"
                "这个跨度不是测错，是功耗墙的真实差别 —— 选机时必须按机型看，"
                "不能拿 CPU 型号的平均分代表这台机器。",
    },
    {
        "name": "i7-14650HX", "vendor": "Intel", "cores": 16,
        "r23_variants": [
            (1935, 23257, "ZOL 评测（游戏本模具）"),
            (2038, 24946, "百度百科汇编上限值"),
            (1986, 24100, "此前 web-search 记录（落在上述区间内）"),
        ],
        "r24": (115, 1125),
        "r26": None,
        "gb6_variants": [
            (2671, 13485, "ZOL 评测（Geekbench 6.4）"),
            (2664, 11503, "迷你主机 ACEMAGIC M5（低释放版本，仅参考）"),
        ],
        "source": "ZOL 中关村在线评测 + 百度百科（i7-14650HX 词条）+ mini PC 评测",
        "note": "16核24线程(8P+8E)/30MB L3/基础55W。多核 2.33~2.49 万为游戏本区间；"
                "迷你主机功耗墙低，多核只到 1.15 万（Geekbench 口径）。",
    },
]


def r23_perf(single, multi):
    """和 prices.json 同一把尺子：perf = 100×(0.5×单/2400 + 0.5×多/40000)"""
    return round(100 * (0.5 * single / 2400 + 0.5 * multi / 40000), 1)


def median(vals):
    v = sorted(x for x in vals if isinstance(x, (int, float)))
    if not v:
        return None
    return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2


def build(entry, existing):
    """把一条 DATA 合进已有记录（有则更新，无则新建）。"""
    e = dict(existing or {})
    e.setdefault("id", entry["name"].lower().replace(" ", "-").replace("_", "-"))
    e["name"] = entry["name"]
    e["vendor"] = entry["vendor"]
    e["cores"] = entry.get("cores")

    rv = entry.get("r23_variants") or []
    # 只取前两个来源算代表值 —— 第三个若是标注为"不采信"的异常值，不该拉高中位
    trusted = [v for v in rv if "不采信" not in v[2]] if len(rv) >= 3 else rv
    if trusted:
        single = median([v[0] for v in trusted])
        multi = median([v[1] for v in trusted])
        bench = e.get("bench") or {}
        bench["r23"] = {"single": int(single), "multi": int(multi)}
        if entry.get("r24"):
            bench["r24"] = {"single": entry["r24"][0], "multi": entry["r24"][1]}
        else:
            bench.setdefault("r24", None)
        if entry.get("r26"):
            bench["r26"] = {"single": entry["r26"][0], "multi": entry["r26"][1]}
        else:
            bench.setdefault("r26", None)

        gv = entry.get("gb6_variants") or []
        if gv:
            bench["geekbench6"] = {
                "single": int(median([v[0] for v in gv])),
                "multi": int(median([v[1] for v in gv])),
            }
        e["bench"] = bench
        e["perfCurrent"] = r23_perf(single, multi)

        # variants 保留**原始值 + 机型**，供以后复核
        variants = [[v[0], v[1]] for v in rv]
        spreads = []
        if len(variants) >= 2:
            singles = [v[0] for v in variants]
            multis = [v[1] for v in variants]
            sp = (max(singles) - min(singles)) / median(singles)
            mp = (max(multis) - min(multis)) / median(multis)
            spreads = [round(max(sp, mp) * 100)]
        e["observedR23"] = {
            "variants": variants,
            "machineCount": len(variants),
            "spreadPct": spreads[0] if spreads else 0,
            "detail": [{"single": v[0], "multi": v[1], "machine": v[2]} for v in rv],
            "note": "spreadPct = (max-min)/median，按单核与多核里较大的那个算。"
                    "跨度大主要是功耗墙差异，不是测量误差 —— 见 detail 里的机型和分数对应。",
        }
        if gv:
            e["observedGB6"] = {
                "variants": [[v[0], v[1]] for v in gv],
                "detail": [{"single": v[0], "multi": v[1], "machine": v[2]} for v in gv],
            }

    e["source"] = entry["source"]
    e["confidence"] = "medium"
    e["note"] = entry["note"]
    return e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    d = json.load(open(BENCH, encoding="utf-8"))
    cpus = d.setdefault("cpus", [])
    idx = {(c.get("name") or "").lower(): i for i, c in enumerate(cpus)}

    print("现有 CPU: %d 条" % len(cpus))
    print()
    changed = 0
    for entry in DATA:
        key = entry["name"].lower()
        if key in idx:
            i = idx[key]
            old = cpus[i]
            new = build(entry, old)
            cpus[i] = new
            act = "更新"
        else:
            new = build(entry, None)
            cpus.append(new)
            act = "新增"
        changed += 1
        b = new.get("bench") or {}
        r23 = b.get("r23") or {}
        gb = b.get("geekbench6") or {}
        print("[%s] %-14s R23=%s/%s  GB6=%s/%s  perf=%s  跨度=%s%%"
              % (act, new["name"], r23.get("single"), r23.get("multi"),
                 gb.get("single") or "-", gb.get("multi") or "-",
                 new.get("perfCurrent"), (new.get("observedR23") or {}).get("spreadPct")))

    print()
    print("变更 %d 条，现有 %d 条" % (changed, len(cpus)))

    if args.write:
        bak = BENCH + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(BENCH, bak)
        # 保持和原文件一致的排序：按 perfCurrent 降序（原来是这么排的）
        cpus.sort(key=lambda c: -(c.get("perfCurrent") or -1))
        json.dump(d, open(BENCH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("已写入。备份: %s" % os.path.basename(bak))
    else:
        print("（预览模式。加 --write 写回文件）")


if __name__ == "__main__":
    main()
