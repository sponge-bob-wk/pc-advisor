# -*- coding: utf-8 -*-
"""fill_cert_model.py —— 把 ZOL 参数页的「产品型号」写入 certModel

## 为什么要做

`certModel`（能效网规格型号）是查能效等级的**唯一钥匙**。AGENTS.md 记录过：

> 能效要用「能效网规格型号」查：搜"天选"查不到，得搜"FA608"这种字母码。
> 营销名在能效网一条都没有。

现在 232 台缺 certModel，其中 103 台其实**参数页里就写着产品型号**，
只是从来没搬进 `certModel` 字段。搬进去之后：
1. 下一轮查能效网时不用再重新抓页面；
2. `pick.py` 的国补说明能写出具体型号，而不是「未查到备案」。

## 关键判断：这个值能不能当能效网型号

`产品型号` 里混了两种东西：
- **字母数字码**：`am2071TX` / `DB9Q7PA` / `ku0023TU` / `Legion R7000P ADR10` —— 能效网查得到
- **中文营销名**：`天选6 Pro 酷睿版` / `蛟龙16Pro 2025` / `MacBook Neo` —— 能效网查不到

只把**含 ASCII 字母且不含中文**的候选写进 certModel。
中文名写进去会让下一次查询白跑一趟（`efficiency_nodata.json` 里已经记了一堆「暂无数据」）。

## 用法

    python fill_cert_model.py            # 演练
    python fill_cert_model.py --apply    # 落盘
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POOL = os.path.join(ROOT, "data", "machine_pool.json")
SRC = os.path.join(ROOT, "data", "zol", "params-2026-11-16.jsonl")

CJK = re.compile(r"[\u4e00-\u9fff]")
# 型号码：至少 4 个字符，含 ASCII 字母，允许数字、连字符、点、斜杠
MODEL_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .\-_/()]{3,}$")
# 手机/平板不是笔记本，不该占能效备案字段（这 7 台本身也是误采进池的）
NON_PC = re.compile(r"(iPhone|手机|Pura|nova|Redmi\s*Note|^\s*vivo|iPad|Mate\s*Pad|骁龙|天玑)", re.I)


def pick_code(raw):
    """从 `am2071TX，DB9Q7PA` 这类串里挑出型号码；挑不到返回 None。"""
    if not raw:
        return None
    for part in re.split(r"[，,;；、|]", str(raw)):
        p = part.strip()
        if not p or CJK.search(p):
            continue
        if not MODEL_CODE.match(p):
            continue
        if not re.search(r"[A-Za-z]", p):
            continue
        # 纯年份、纯描述不是型号
        if re.fullmatch(r"(20\d{2}|[0-9]+)", p):
            continue
        return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    params = {}
    if os.path.exists(SRC):
        for line in open(SRC, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            params[rec["id"]] = rec.get("params") or {}

    pool = json.load(open(POOL, encoding="utf-8"))
    filled, skipped_cjk, skipped_pc = [], 0, 0
    for m in pool["machines"]:
        if m.get("certModel"):
            continue
        if NON_PC.search(str(m.get("name") or "")):
            skipped_pc += 1
            continue
        raw = (params.get(m["id"]) or {}).get("产品型号") or (m.get("_zolRaw") or {}).get("产品型号")
        if not raw:
            continue
        code = pick_code(raw)
        if not code:
            skipped_cjk += 1
            continue
        filled.append((m["id"], (m.get("name") or "")[:26], code, raw))
        if args.apply:
            m["certModel"] = code
            bf = set(m.get("backfilled") or [])
            bf.add("certModel")
            m["backfilled"] = sorted(bf)
            df = dict(m.get("dataFrom") or {})
            df["certModel"] = "ZOL 参数页 产品型号（原值：%s）" % raw
            m["dataFrom"] = df

    print("可补 certModel：%d 台；只有中文营销名、不写：%d 台；手机/平板跳过：%d 台"
          % (len(filled), skipped_cjk, skipped_pc))
    print()
    for mid, name, code, raw in filled:
        print("  %-16s %-26s %-16s  ← %s" % (mid, name, code, str(raw)[:40]))

    if args.apply and filled:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写入 %s（备份 %s）" % (POOL, bak))
    elif not args.apply:
        print("\n（演练模式，未写文件；加 --apply 落盘）")


if __name__ == "__main__":
    main()
