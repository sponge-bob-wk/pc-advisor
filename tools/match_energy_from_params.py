# -*- coding: utf-8 -*-
"""match_energy_from_params.py —— 用 ZOL 参数页的「产品型号」到能效台账里匹配备案

## 为什么这条路可行

能效网索引的是**厂商备案型号**（`FA608PM`、`am2071TX`、`DB9Q7PA`），
不是营销名（「天选6 Pro」）。历史上 7 台之所以能查到，就是因为先拿到了这个型号。

ZOL 参数页的 `产品型号` 字段正好是这个东西：
    `am2071TX，DB9Q7PA`        ← 惠普暗影精灵 Pro，逗号分隔多型号
    `拯救者Y9000P 2026`         ← 联想，中文
    `HUAWEI MateBook Pro S`

而 `data/efficiency_ledger.json` 里已有 186 条备案（65 个唯一型号），
是之前人工/半自动从能效网抓回来的。**先做本地匹配，命中就不用联网。**

## 匹配规则（保守，只认强证据）

1. 从 `产品型号` 按 `，,;；/、` 拆成候选型号，逐个去台账里比；
2. **只做规范化后完全相等**（去空格、去 `-`、转大写）；
   不做子串匹配 —— `Y9000P` 会命中 `Y9000P 2025` 和 `Y9000P 2026` 两个不同备案；
3. 台账里同一型号有多条时，**只有全部 level 一致才写入**；
   不一致就跳过并报告冲突（不许挑一个看起来顺眼的）；
4. 只写 `efficiency` 空着的记录，不覆盖已有值。

## 用法

    python match_energy_from_params.py            # 演练
    python match_energy_from_params.py --apply    # 落盘
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
LEDGER = os.path.join(ROOT, "data", "efficiency_ledger.json")
SRC = os.path.join(ROOT, "data", "zol", "params-2026-11-16.jsonl")


def norm(s):
    """型号规范化：去空格/连字符/下划线，转大写。"""
    return re.sub(r"[\s\-_／/]", "", str(s or "")).upper()


def model_candidates(raw):
    """把 `am2071TX，DB9Q7PA` 拆成多个候选型号。"""
    if not raw:
        return []
    parts = re.split(r"[，,;；、/|]", str(raw))
    out = []
    for p in parts:
        p = p.strip()
        # 去掉营销尾巴（「2026」「酷睿版」这类中文描述不是型号的一部分）
        if not p or len(p) < 3:
            continue
        out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    led = json.load(open(LEDGER, encoding="utf-8"))
    recs = list(led.get("records") or [])
    # 第二份台账（2026-11-10 无障碍树抓的），字段名不同，一起用
    auto_path = os.path.join(ROOT, "data", "efficiency_ledger_auto.json")
    if os.path.exists(auto_path):
        try:
            auto = json.load(open(auto_path, encoding="utf-8"))
            for r in auto.get("records") or []:
                lv = r.get("level")
                try:
                    lv = int(str(lv).strip())
                except (TypeError, ValueError):
                    lv = None
                recs.append({"model": r.get("model"), "level": lv,
                             "regNo": r.get("regNo"), "producer": r.get("producer"),
                             "srcFile": "efficiency_ledger_auto"})
        except Exception as e:  # noqa: BLE001
            print("!! 读 efficiency_ledger_auto.json 失败：%s" % e)

    by_model = {}
    for r in recs:
        m = norm(r.get("model"))
        if not m:
            continue
        by_model.setdefault(m, []).append(r)

    print("台账 %d 条，%d 个唯一型号" % (len(recs), len(by_model)))

    params = {}
    if os.path.exists(SRC):
        for line in open(SRC, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            params[rec["id"]] = rec.get("params") or {}

    pool = json.load(open(POOL, encoding="utf-8"))
    hits, conflicts = [], []
    for m in pool["machines"]:
        if m.get("efficiency") is not None:
            continue
        # 候选来源：参数页 产品型号 + 库里已有 certModel/energyModel
        cands = []
        cands += model_candidates((params.get(m["id"]) or {}).get("产品型号"))
        cands += model_candidates((m.get("_zolRaw") or {}).get("产品型号"))
        cands += model_candidates(m.get("certModel"))
        cands += model_candidates(m.get("energyModel"))

        found = None
        for c in cands:
            rows = by_model.get(norm(c))
            if not rows:
                continue
            levels = {r.get("level") for r in rows if r.get("level") is not None}
            if len(levels) == 1:
                found = (c, levels.pop(), rows[0])
                break
            conflicts.append((m["id"], (m.get("name") or "")[:24], c,
                              sorted(levels), len(rows)))
        if not found:
            continue
        model, level, row = found
        hits.append((m["id"], (m.get("name") or "")[:26], model, level,
                     row.get("regNo"), row.get("producer")))
        if args.apply:
            m["efficiency"] = level
            m["energyModel"] = row.get("model")
            if row.get("regNo"):
                m["certModel"] = row.get("model")
            bf = set(m.get("backfilled") or [])
            bf.add("efficiency")
            m["backfilled"] = sorted(bf)
            df = dict(m.get("dataFrom") or {})
            df["efficiency"] = "efficiency_ledger 型号精确匹配（%s / %s）" % (
                row.get("model"), row.get("regNo"))
            m["dataFrom"] = df
            # ⚠️ 口径注记：能效备案是**按型号码**登记的，同一型号码下的不同 GPU 配置
            #    （如 R7000P ADR10 的 5050/5060/5070 三版）共用同一条备案。
            #    efficiency 是 P 级字段，只影响国补金额、不影响"够不够用"的判定，
            #    但仍要写清楚这是型号级匹配，不是逐 SKU 直读。
            note = ("能效等级=型号级备案匹配（产品型号 %s 与台账备案 %s 精确一致，"
                    "同一型号码下的不同配置共用该备案）。非遗 SKU 直读。"
                    % (model, row.get("regNo")))
            m["efficiencyBasis"] = note

    print()
    print("=== 精确匹配成功：%d 台 ===" % len(hits))
    for mid, name, model, level, reg, prod in hits:
        print("  %-16s %-26s %-14s %s级  %s  %s" % (
            mid, name, model, level, reg, (prod or "")[:16]))

    if conflicts:
        print()
        print("=== 型号命中但等级冲突（不写）：%d 台 ===" % len(conflicts))
        for mid, name, model, levels, n in conflicts:
            print("  %-16s %-26s %-14s levels=%s (%d 条备案)" % (mid, name, model, levels, n))

    if args.apply and hits:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写入 %s（备份 %s）" % (POOL, bak))
    elif not args.apply:
        print("\n（演练模式，未写文件；加 --apply 落盘）")


if __name__ == "__main__":
    main()
