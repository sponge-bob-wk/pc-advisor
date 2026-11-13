#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
parse_energy_raw.py —— 把能效标识网「Ctrl+A / Ctrl+C」拿到的原始文本解析成结构化记录

为什么不能按行切：
  型号字段会列出一串型号（如启天M660-N001..N144），在表格里折行，
  复制出来后行结构完全乱掉，按行切分会把一条记录劈成几十条。

做法：以「备案号」为正则锚点（格式 YYYYMMDD-CEL+7位数字-6位数字），
  每条记录的 型号 = 上一个 产品类型行 与 备案号 之间的全部内容拼接。
"""
import json
import re
import sys
from pathlib import Path

# 备案号有两种格式，2012 版与 2025 版完全不同：
#   2025 版： 20261012-CEL0272025-445480      （含 CEL 标记）
#   2012 版： 20220914-301100-678046999313097 （无 CEL，末段是长数字）
REG_RE = re.compile(r"^\d{8}-CEL\d{7}-\d{6}$|^\d{8}-\d{6}-\d{9,}$")
TYPE_RE = re.compile(r"^[^\d\s][^,]{0,40}?\s*\d{4}版$")
LEVEL_RE = re.compile(r"^[1-3]$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TYPES = ("新增备案", "扩展备案", "变更备案", "注销备案")


def parse(text: str):
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln != ""]

    # 找到所有备案号所在行号
    reg_idx = [i for i, ln in enumerate(lines) if REG_RE.match(ln)]

    out = []
    for n, ri in enumerate(reg_idx):
        # 往上找：产品类型行（"微型计算机 2025版"）
        type_i = None
        for j in range(ri - 1, max(ri - 40, -1), -1):
            if TYPE_RE.match(lines[j]):
                type_i = j
                break
        if type_i is None:
            continue
        # 产品类型行的再上一行应是序号
        seq = None
        if type_i - 1 >= 0 and lines[type_i - 1].isdigit():
            seq = int(lines[type_i - 1])

        model = "".join(lines[type_i + 1: ri])
        model_raw = "\n".join(lines[type_i + 1: ri])

        # 往下取 生产者 / 等级 / 备案类型 / 日期
        producer = level = ftype = date = None
        k = ri + 1
        nxt = reg_idx[n + 1] if n + 1 < len(reg_idx) else len(lines)
        while k < len(lines) and k < nxt:
            ln = lines[k]
            if producer is None and "公司" in ln:
                producer = ln
            elif level is None and LEVEL_RE.match(ln):
                level = int(ln)
            elif ftype is None and ln.strip("\t ") in TYPES:
                ftype = ln.strip("\t ")
            elif date is None and DATE_RE.match(ln):
                date = ln
            k += 1

        out.append({
            "seq": seq,
            "productType": " ".join(lines[type_i].split()),
            "regScheme": 2025 if "CEL" in lines[ri] else 2012,
            "model": model if len(model) <= 80 else model[:80] + "…",
            "modelRaw": model_raw,
            "modelCount": model_raw.count(",") + 1 if model_raw else 1,
            "regNo": lines[ri],
            "producer": producer,
            "level": level,
            "filingType": ftype,
            "date": date,
        })
    return out


def main():
    paths = sys.argv[1:]
    if not paths:
        print("usage: parse_energy_raw.py <raw txt> [...]")
        sys.exit(2)
    allrecs = []
    for p in paths:
        txt = Path(p).read_text(encoding="utf-8-sig")
        recs = parse(txt)
        m = re.search(r"共\s*(\d+)\s*条", txt)
        total = int(m.group(1)) if m else None
        print(f"{Path(p).name}: parsed={len(recs)}  total_on_site={total}")
        for r in recs:
            allrecs.append(r)
    print(f"\ntotal parsed = {len(allrecs)}")
    lv = {}
    for r in allrecs:
        lv[r["level"]] = lv.get(r["level"], 0) + 1
    print("level dist:", lv)
    print("\nsample:")
    for r in allrecs[:3]:
        print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()