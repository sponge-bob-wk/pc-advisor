# -*- coding: utf-8 -*-
"""zol_recrawl.py —— 重爬 ZOL 参数页，补回「参数表里其实有」的字段（2026-11-16）

## 为什么要重爬

`zol_fetch.py` 的注释断言「ZOL 参数表没有 CPU型号 / 内存容量 / 硬盘容量 / 分辨率」，
所以那四项只能从页面标题的型号串里正则抠。**实测这个断言是错的** ——
参数表里全都有：

    内存容量 = 16GB        （新键 newPmName_15 / newPmVal_15）
    硬盘容量 = 1TB
    显存容量 = 8GB
    CPU型号  = Intel 酷睿 Ultra 7 255HX
    核心/线程数 = 20核心/20线程
    显卡最高功耗 = 115W
    屏幕分辨率 = 2560x1600
    数据接口 / 读卡器 / 插槽数量 / 最大内存容量 / 色域 / 亮度 ...

从标题抠的后果：标题只有「32GB/1TB」这种粗配置，`gpu` 只有型号没有显存，
`cpu.cores` 全空、`gpu.tgpMaxW` 全空 —— 这正是 243 台池子里
cores 缺 163、vramGB 缺 167、tgpMaxW 缺 205 的直接原因。

## 解析要点（踩过的坑）

1. 表头名字有两种写法：`<th><span id="newPmName_N">` 和 `<a id="newPmName_N">`，
   只匹配一种会漏掉一半字段。
2. 值的 span 里嵌着 `<span class="text-hui"><a>16GB</a></span><i/><span>极速运行</span>`
   这类营销尾巴，必须先按 id 截出整段再剥标签，否则会拿到「16GB 极速运行」。
3. 页面是 **GBK**，且有 gzip。
4. 输出只存原始键值对（`_params`），映射成池格式在 `zol_map_fields.py` 里做 ——
   采集与映射分开，重跑映射不用重新联网。

## 用法

    python zol_recrawl.py --limit 5        # 试跑
    python zol_recrawl.py                  # 全量（已爬过的跳过，可断点续）
"""
import argparse
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POOL = os.path.join(ROOT, "data", "machine_pool.json")
OUT = os.path.join(ROOT, "data", "zol", "params-2026-11-16.jsonl")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")
NAME_RE = re.compile(r'<(?:th|a|span)[^>]*id="newPmName_(\d+)"[^>]*>(.*?)</(?:th|a|span)>', re.S)
VAL_RE = re.compile(r'<span id="newPmVal_(\d+)">(.*?)</span>\s*(?:<em|</td>|<i)', re.S)


def clean(s):
    s = TAG.sub(" ", s)
    s = (s.replace("&nbsp;", " ").replace("&gt;", ">").replace("&lt;", "<")
          .replace("&amp;", "&").replace("&quot;", '"'))
    return WS.sub(" ", s).strip().rstrip(">")


def fetch(url, retry=3):
    last = None
    for i in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "gzip",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            r = urllib.request.urlopen(req, timeout=25)
            d = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                d = gzip.decompress(d)
            return d.decode("gbk", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def params(html):
    names, vals = {}, {}
    for m in NAME_RE.finditer(html):
        n = clean(m.group(2))
        if n:
            names[m.group(1)] = n
    for m in VAL_RE.finditer(html):
        vals[m.group(1)] = clean(m.group(2))
    return {names[k]: vals[k] for k in names if k in vals}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-interval", type=float, default=1.0)
    args = ap.parse_args()

    pool = json.load(open(POOL, encoding="utf-8"))
    targets = []
    for m in pool["machines"]:
        u = m.get("url") or ""
        if "detail.zol.com.cn" in u and "param.shtml" in u:
            targets.append((m["id"], u))
    print("ZOL 机型 %d 台" % len(targets))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["id"])
            except Exception:  # noqa: BLE001
                pass
    print("已爬 %d 台，待爬 %d 台" % (len(done), len([t for t in targets if t[0] not in done])))

    todo = [t for t in targets if t[0] not in done]
    if args.limit:
        todo = todo[: args.limit]

    ok = fail = 0
    with open(OUT, "a", encoding="utf-8") as f:
        for i, (mid, url) in enumerate(todo, 1):
            try:
                html = fetch(url)
                p = params(html)
                if not p:
                    raise RuntimeError("解析到 0 项（页面结构可能变了）")
                rec = {"id": mid, "url": url, "capturedAt": time.strftime("%Y-%m-%d %H:%M"),
                       "paramCount": len(p), "params": p}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                ok += 1
                print("[%d/%d] %-16s %2d 项" % (i, len(todo), mid, len(p)))
            except Exception as e:  # noqa: BLE001
                fail += 1
                print("[%d/%d] %-16s FAIL %s" % (i, len(todo), mid, e))
            time.sleep(args.min_interval)

    print("完成：成功 %d，失败 %d，输出 %s" % (ok, fail, OUT))


if __name__ == "__main__":
    main()
