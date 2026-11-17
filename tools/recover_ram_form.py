# -*- coding: utf-8 -*-
"""recover_ram_form.py —— 用「插槽数量 / LPDDR / 最大内存容量」补 ram.form

## 为什么这 58 个判定对卡着

`field_matrix` 里 code 组（编程数据）有一条硬门槛：

    hardGates: [{dim: "upgrade", min: 2}]   —— 内存焊死等于四年后没后路

`lib_judge` 判这条门槛时调 `form_is_soldered(m)`，它读 `ram.form`；
`form` 为空时返回 None → 判 `UNKNOWN "缺 ramType"` → 整组判不了。
58 个判定对全卡在这里，而这 58 台**内存容量都有**，只是不知道能不能换。

## 证据分级（只写有证据的，不猜）

| 证据 | 能推出什么 | 强度 |
|---|---|---|
| `插槽数量 = 2xSO-DIMM` | form=sodimm, slots=2 | 直读 |
| `插槽数量 = 0` / 「板载」 | form=soldered, slots=0 | 直读 |
| 内存类型 = LPDDR* | form=soldered | 强推断（LPDDR 物理上全是板载封装） |
| 最大内存容量 > 已装容量 | form=sodimm（否则怎么加） | 强推断 |

**没证据就不写。** `DDR5` 只是芯片类型，DDR5 也可以板载焊死（很多轻薄本就是这样），
拿它推 sodimm 是错的 —— 这一条写清楚，免得下一个人"顺手"补上。

## 用法

    python recover_ram_form.py            # 演练
    python recover_ram_form.py --apply    # 落盘（自动备份）
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


def load_params():
    out = {}
    if not os.path.exists(SRC):
        return out
    for line in open(SRC, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        out[rec["id"]] = rec.get("params") or {}
    return out


def gb(v):
    m = re.search(r"(\d+)", str(v or ""))
    return int(m.group(1)) if m else None


def infer(m, p):
    """返回 (form, slots, evidence) 或 (None, None, None)。"""
    slot_raw = p.get("插槽数量")
    typ = str((m.get("ram") or {}).get("type") or p.get("内存类型") or "")
    inst = (m.get("ram") or {}).get("gb")
    maxg = gb(p.get("最大内存容量"))
    cfg = (m.get("ram") or {}).get("config")

    # 1) 直读插槽数
    if slot_raw:
        s = str(slot_raw)
        mm = re.search(r"(\d)\s*[x×]\s*(SO-?DIMM|DIMM)", s, re.I)
        if mm:
            return "sodimm", int(mm.group(1)), "插槽数量=%s" % s
        if re.search(r"板载|焊死|onboard|不可", s, re.I):
            return "soldered", 0, "插槽数量=%s" % s
        if re.fullmatch(r"\s*0\s*", s):
            return "soldered", 0, "插槽数量=0"

    # 2) LPDDR → 板载封装（物理事实，不是推测）
    if re.search(r"LPDDR", typ, re.I):
        return "soldered", 0, "内存类型=%s（LPDDR 均为板载封装）" % typ

    # 3) 配置写成「N GB × M 条」 → M 条物理内存，必然不是焊死
    #    ZOL/官网把板载 LPDDR 一律写成总量（`16GB`），从不写 ×2；
    #    写 ×2 就是两条模组，×1 是单条插在插槽里。这一档要求 type 里没有 LPDDR
    #    （上面第 2 条已提前拦掉），所以只可能是 DDR 插槽内存。
    if cfg and re.search(r"[×x]\s*\d+", str(cfg)) and re.search(r"DDR", typ, re.I):
        mm = re.search(r"[×x]\s*(\d+)", str(cfg))
        return "sodimm", int(mm.group(1)) if mm else None, \
            "内存配置=%s（%s，物理 %s 条）" % (cfg, typ, mm.group(1) if mm else "多")

    # 4) 最大内存容量 > 已装 → 必然可加
    if inst and maxg and maxg > inst:
        return "sodimm", None, "最大内存容量 %sGB > 已装 %sGB" % (maxg, inst)

    # 5) 其余（只写 DDR5、无插槽键、无配置）不足以判定 —— 保持 None。
    #    DDR5 可以板载也可以插槽，拿它推 sodimm 是错的。
    return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    params = load_params()
    pool = json.load(open(POOL, encoding="utf-8"))
    ms = pool["machines"]

    filled = skipped = 0
    detail = []
    for m in ms:
        ram = m.get("ram")
        if not isinstance(ram, dict) or ram.get("form") is not None:
            continue
        form, slots, ev = infer(m, params.get(m["id"]) or {})
        if not form:
            skipped += 1
            continue
        filled += 1
        detail.append((m["id"], (m.get("name") or "")[:24], form, slots, ev))
        if args.apply:
            ram["form"] = form
            if slots is not None and ram.get("slots") is None:
                ram["slots"] = slots
            bf = set(m.get("backfilled") or [])
            bf.add("ram.form")
            m["backfilled"] = sorted(bf)
            df = dict(m.get("dataFrom") or {})
            df["ramform_inference"] = ev
            m["dataFrom"] = df

    print("可推断 ram.form：%d 台；证据不足保持空：%d 台" % (filled, skipped))
    print()
    for mid, name, form, slots, ev in detail:
        print("  %-16s %-26s %-9s slots=%-4s %s" % (mid, name, form, slots, ev))

    if args.apply:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写入 %s（备份 %s）" % (POOL, bak))
    else:
        print("\n（演练模式，未写文件；加 --apply 落盘）")


if __name__ == "__main__":
    main()
