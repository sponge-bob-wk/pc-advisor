# -*- coding: utf-8 -*-
"""fetch_fit_params.py —— 抓显卡/机箱/散热器的物理尺寸参数

## 为什么单独一个脚本

`fetch_parts.py` 只抓「型号 + 价格」，装机要验尺寸还需要：
  · 显卡：产品尺寸（长×宽×厚）、建议电源
  · 机箱：显卡限长、CPU 散热器限高、适用主板、支持水冷
  · 散热器：高度（风冷）、冷排尺寸（水冷）
  · 主板：板型（ATX/MATX/ITX）

这些都从 ZOL 参数页拿：`/{id前4位}/{id}/param.shtml`
（和机型参数页同一个规则）

## 成本

每个配件要单独抓一次参数页，慢。
所以只抓「可能被选中」的：价格在合理区间的、各品类前 N 个。

用法：
    python fetch_fit_params.py --limit 40
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PARTS = os.path.join(ROOT, "data", "parts.json")
OUT = os.path.join(ROOT, "data", "parts_fit.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 要抓参数的品类 + 关心的字段
TARGETS = {
    "gpu": {
        "keys": ["产品尺寸", "建议电源", "接口类型", "散热方式"],
    },
    "case": {
        "keys": ["显卡限长", "CPU散热器限高", "适用主板", "支持水冷",
                 "机箱结构", "机箱尺寸"],
    },
    "cooler": {
        "keys": ["散热器高度", "散热方式", "风扇尺寸", "适用范围",
                 "最大风量", "噪音"],
    },
    "motherboard": {
        "keys": ["主板板型", "主芯片组", "最大内存容量", "M.2接口"],
    },
}


def fetch(url, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            raw = urllib.request.urlopen(req, timeout=25).read()
            return raw.decode("gbk", "replace")
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(1.5)
    raise last


def param_page(pid):
    """参数页 URL：/{id前4位}/{id}/param.shtml"""
    return "https://detail.zol.com.cn/%s/%s/param.shtml" % (pid[:4], pid)


def parse_params(html):
    """从参数页提取 {字段名: 值}。"""
    rows = re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
                      html, re.S)
    out = {}
    for k, v in rows:
        k2 = re.sub(r"<[^>]+>", "", k).strip()
        v2 = re.sub(r"<[^>]+>", "", v).strip()
        v2 = re.sub(r"\s+", " ", v2).replace("纠错", "").strip()
        if k2 and v2:
            out[k2] = v2
    return out


def pid_of(url):
    """从 /xxx/index1234567.shtml 里抠出 pid。"""
    m = re.search(r"index(\d+)\.shtml", url or "")
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40,
                    help="每个品类最多抓多少个")
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(PARTS):
        print("✗ 缺 %s，先跑 fetch_parts.py" % PARTS)
        return 1

    parts = json.load(io.open(PARTS, encoding="utf-8"))

    print("=" * 74)
    print("抓取配件的物理尺寸参数（%s）" % ("演练" if args.dry else "正式"))
    print("=" * 74)
    print("  ⚠️ 每个配件要单独抓参数页，慢；按 --limit 控制数量")
    print()

    result = {}
    if os.path.exists(OUT):
        try:
            result = json.load(io.open(OUT, encoding="utf-8"))
        except Exception:
            result = {}

    now = time.strftime("%Y-%m-%d %H:%M")
    total = 0

    for key, cfg in TARGETS.items():
        items = (parts.get(key) or {}).get("items") or []
        if not items:
            print("  ○ %-14s 无数据" % key)
            continue

        # 挑要抓的：按价格从低到高（装机主要用中低价位）
        sample = sorted(items, key=lambda x: x["price"] or 0)[:args.limit]

        got = {}
        ok = 0
        fail = 0
        for it in sample:
            pid = pid_of(it.get("url"))
            if not pid:
                fail += 1
                continue
            try:
                html = fetch(param_page(pid))
                params = parse_params(html)
                picked = {}
                for k in cfg["keys"]:
                    if k in params:
                        picked[k] = params[k]
                if picked:
                    got[it["name"]] = {
                        "price": it["price"],
                        "pid": pid,
                        "params": picked,
                    }
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
            time.sleep(args.sleep)

        print("  ✓ %-14s 成功 %2d / 失败 %2d（共试 %d）"
              % (key, ok, fail, len(sample)))

        # 打印 2 个样例，确认字段抓对了
        for nm, d in list(got.items())[:2]:
            print("      %s" % nm[:44])
            for k, v in d["params"].items():
                print("        %-16s %s" % (k[:14], str(v)[:44]))

        result[key] = {
            "fetchedAt": now,
            "items": got,
        }
        total += ok

    print()
    print("  合计抓到 %d 个配件的尺寸参数" % total)

    if args.dry:
        print("  （演练，未写盘）")
        return 0
    if not total:
        print("  没抓到，不写盘")
        return 1

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if os.path.exists(OUT):
        import shutil
        bak = OUT + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(OUT, bak)
        print("  备份: %s" % os.path.basename(bak))
    json.dump(result, io.open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("  已写入 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
