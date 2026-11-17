# -*- coding: utf-8 -*-
"""bench_fetch_nbc.py —— 从 Notebookcheck 抓 CPU 跑分（带来源留痕）

## 为什么要接这个源

`bench_coverage.py` 诊断出的真实缺口（2026-11-16）：

    推荐池 223 台，无跑分 88 台（覆盖 60.5%）
      A. 归一化能救        0 台   ← 已修到极限
      B. 真的没这颗芯片    74 台   ← 本脚本要补的
      C. 型号不可用        14 台   ← 系列名/空/非 x86，补不了

交接文档说「国际跑分站全 000（Geekbench/3DMark/Notebookcheck/cpu-monkey）」，
2026-11-16 实测 **Notebookcheck 已可达（HTTP 200）**，所以这条路重新可用。

## 数据从哪来、怎么认

`https://www.notebookcheck.net/Mobile-Processors-Benchmark-List.2436.0.html`
是一个 6.2MB 的汇总表，每颗 CPU 一行 `<tr>`，跑分列用 CSS class 编码：

    class="value bv_768"   → Cinebench R23   （两个值：单核 / 多核）
    class="value bv_967"   → Geekbench 6     （两个值：单核 / 多核）
    class="value bv_244"   → Cinebench R15
    class="value bv_552"   → 7-Zip

值后面常带 `n8`/`n2` 后缀 —— 那是**样本数**，不是小数。必须剥掉再转 float，
否则 `1685.5n8` 会被当成解析失败（首版就踩了这个）。

## 红线（继承自项目，不许破）

1. **每条都要带 source + url + 抓取时间** —— `bench_add.py` 的硬要求。
2. **只填原始实测值，不填换算值** —— `perfCurrent` 由 `bench_add.py` 用统一尺子算，
   本脚本只产出 `r23.single` / `r23.multi` 这类原始分。
3. **查不到就是查不到** —— 不猜、不用邻近型号替代。
4. **一颗 CPU 一个分本身是错的**（交接文档 §5.3）：Ultra 7 356H 多核从 9019 到 20298，
   差 2.25 倍是功耗墙的真实差别。Notebookcheck 给的是**跨机型中位**，
   所以写进 `observed` 并标注"跨机型聚合"，与单机实测区分开。

## 用法

    python bench_fetch_nbc.py                # 抓取 + 匹配，只报告
    python bench_fetch_nbc.py --write        # 写 data/bench_nbc.json（中间产物，不直接改 bench.json）
    python bench_fetch_nbc.py --only "i7 14700HX"   # 只查一颗，调试用
"""
import argparse
import gzip
import json
import os
import re
import socket
import sys
import time
import urllib.request
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = os.path.join(os.path.dirname(os.path.dirname(HERE)), "data")

import core  # noqa: E402

NBC_URL = "https://www.notebookcheck.net/Mobile-Processors-Benchmark-List.2436.0.html"
OUT = os.path.join(DATA, "bench_nbc.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 跑分列的 CSS class → 我们的字段名
COLS = {
    "bv_768": "r23",     # Cinebench R23
    "bv_967": "gb6",     # Geekbench 6
    "bv_244": "r15",     # Cinebench R15
    "bv_552": "zip7",    # 7-Zip
}


def fetch(url, retry=2):
    last = None
    for i in range(retry + 1):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Encoding": "gzip",
                "Accept-Language": "en-US,en;q=0.9"}), timeout=45)
            d = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                d = gzip.decompress(d)
            return d.decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def clean_num(v):
    """`1685.5n8` → 1685.5。剥掉样本数后缀 n8/n2。"""
    s = str(v or "").strip()
    if not s:
        return None
    s = s.replace("&#126;", "").replace("~", "").replace("&nbsp;", " ")
    s = re.sub(r"n\d+$", "", s)          # 样本数后缀
    s = re.sub(r"[^\d.]", "", s)
    if not s or s.count(".") > 1:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_table(html):
    """解析整张表 → {cpu_key: {name, r23:{single,multi}, gb6:{...}, ...}}"""
    out = {}
    # 按行切，找含 "class=\"specs\"" 的第二列是 CPU 名的行
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        row = m.group(1)
        tds = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S)
        if len(tds) < 12:
            continue
        # 第二列（index 1）是 CPU 名
        name_raw = re.sub(r"<[^>]+>", "", tds[1][1])
        name = re.sub(r"\s+", " ", name_raw.replace("&nbsp;", " ")).strip()
        if not name or len(name) > 60 or not re.search(r"[A-Za-z]", name):
            continue
        # 第一列是排名（纯数字），用来确认这是一颗 CPU 行
        rank = re.sub(r"<[^>]+>", "", tds[0][1]).strip()
        if not rank.isdigit():
            continue

        rec = {"name": name}
        for attr, val in tds:
            cm = re.search(r'class="[^"]*\b(bv_\d+)\b[^"]*"', attr)
            if not cm:
                continue
            field = COLS.get(cm.group(1))
            if not field:
                continue
            n = clean_num(re.sub(r"<[^>]+>", "", val))
            if n is None:
                continue
            rec.setdefault(field, []).append(n)

        # 每个 bench 只有两个值时按 [单核, 多核] 解释（NBC 的列顺序就是这样）
        for k in list(rec):
            if isinstance(rec[k], list):
                vals = rec[k]
                if len(vals) >= 2:
                    rec[k] = {"single": vals[0], "multi": vals[1]}
                elif len(vals) == 1:
                    rec[k] = {"single": None, "multi": vals[0]}
        if any(k in rec for k in COLS.values()):
            out[core._cpu_key(name)] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    # 目标：无跑分的 B 类芯片
    bench = json.load(open(os.path.join(DATA, "bench.json"), encoding="utf-8"))
    have = set()
    for c in bench.get("cpus") or []:
        if c.get("perfCurrent") is not None:
            have.add(core._cpu_key(c.get("name")))
            have.add(core._cpu_key(c.get("id")))

    offs = core.pool_offers()
    want = {}
    for o in offs:
        if o.get("perf"):
            continue
        chip = (o.get("chip") or "").strip()
        if not chip or "系列" in chip:
            continue
        if re.search(r"(麒麟|Kirin|腾锐|飞腾|骁龙|天玑)", chip):
            continue
        k = core._cpu_key(chip)
        if not k or k in have:
            continue
        want.setdefault(k, {"chip": chip, "machines": []})
        want[k]["machines"].append(o["name"])

    if args.only:
        k = core._cpu_key(args.only)
        want = {k: want.get(k, {"chip": args.only, "machines": []})}

    print("待补跑分的芯片：%d 颗（覆盖 %d 台机器）"
          % (len(want), sum(len(v["machines"]) for v in want.values())))
    print("抓取 %s" % NBC_URL)
    html = fetch(NBC_URL)
    print("页面 %.1f MB" % (len(html) / 1024 / 1024))

    table = parse_table(html)
    print("解析出 %d 颗 CPU 的跑分" % len(table))
    print()

    hit, miss = [], []
    for k, info in sorted(want.items()):
        rec = table.get(k)
        if rec:
            hit.append((k, info, rec))
        else:
            miss.append((k, info))

    print("=" * 70)
    print("命中 %d 颗 / 未命中 %d 颗" % (len(hit), len(miss)))
    print("=" * 70)
    for k, info, rec in hit:
        r23 = rec.get("r23") or {}
        gb6 = rec.get("gb6") or {}
        n = len(info["machines"])
        print("  %-28s R23 单%-7s 多%-8s GB6 单%-7s 多%-8s  (%d 台)"
              % (info["chip"][:26],
                 r23.get("single") or "-", r23.get("multi") or "-",
                 gb6.get("single") or "-", gb6.get("multi") or "-", n))

    if miss:
        print()
        print("未命中（Notebookcheck 也没有，或写法差异）：")
        for k, info in miss:
            print("  %-28s %d 台" % (info["chip"][:26], len(info["machines"])))

    if args.write and hit:
        payload = {
            "meta": {
                "source": "Notebookcheck Mobile Processors Benchmark List",
                "url": NBC_URL,
                "capturedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "note": ("跨机型聚合值（NBC 汇总表），不是单机实测。"
                         "写进 bench.json 时必须走 bench_add.py，"
                         "并标 aggregated=true，不许直接当 perfCurrent。"),
                "colMap": COLS,
            },
            "cpus": [{
                "chip": info["chip"],
                "cpuKey": k,
                "name": rec.get("name"),
                "r23": rec.get("r23"),
                "gb6": rec.get("gb6"),
                "r15": rec.get("r15"),
                "zip7": rec.get("zip7"),
                "machines": info["machines"],
                "aggregated": True,
            } for k, info, rec in hit],
        }
        json.dump(payload, open(OUT, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print()
        print("写出 %d 颗 → %s" % (len(hit), OUT))
        print("⚠ 这是中间产物，不是 bench.json。入库要走 bench_add.py（它负责算 perfCurrent 并留来源）。")


if __name__ == "__main__":
    main()
