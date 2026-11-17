# -*- coding: utf-8 -*-
"""migrate_screen_keys.py —— 修 screen.gamut / screen.brightness 键名错位

## 症状

`lib_judge.field_value()` 读的是 `screen.colorGamut` 与 `screen.brightnessNit`，
而 `zol_to_pool.py` 写的是 `screen.gamut` / `screen.brightness`。
两套键名并存 ⇒ **数据在库里，判定引擎看不见**：

    平面视觉组一直报 「缺 colorGamut×3」 —— 那 3 台其实有值，只是键名不对
    实测：86 台有 gamut、96 台有 brightness，判定引擎一台都读不到

## 做法

1. **按 `_zolRaw` 里的原始标签还原色域标准**（sRGB / DCI-P3 / NTSC），
   不拿「100%」这种裸数字猜标准 —— 猜错等于伪造数据。
2. 目标键已有值就**不覆盖**（以现有 colorGamut/brightnessNit 为准）。
3. 迁移完把旧键删掉，避免下一轮又出现"两套键名"。
4. 每笔写入记进 `backfilled`。

## 用法

    python migrate_screen_keys.py            # 演练
    python migrate_screen_keys.py --apply    # 落盘（自动备份）
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

LEGACY = {"gamut": "colorGamut", "brightness": "brightnessNit"}


def _num(v, lo=None, hi=None):
    if v in (None, ""):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    if not m:
        return None
    f = float(m.group(0))
    if lo is not None and f < lo:
        return None
    if hi is not None and f > hi:
        return None
    return int(f) if f == int(f) else round(f, 1)


def gamut_from_raw(raw, fallback_value):
    """从 `_zolRaw` 的原始标签还原色域标准。拿不到标签就不填。"""
    out = {}
    for key, dst in (("sRGB色域", "srgb"), ("DCI-P3色域", "dciP3"), ("NTSC色域", "ntsc")):
        n = _num(raw.get(key), 30, 200)
        if n is not None:
            out[dst] = n
    if not out and fallback_value is not None:
        n = _num(fallback_value, 30, 200)
        label = str(raw.get("色域") or "").lower()
        if n is not None:
            if "p3" in label:
                out["dciP3"] = n
            elif "srgb" in label:
                out["srgb"] = n
            elif "ntsc" in label:
                out["ntsc"] = n
    return out or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    pool = json.load(open(POOL, encoding="utf-8"))
    ms = pool["machines"]

    moved_g = moved_b = 0
    dropped_g = dropped_b = 0
    samples = []

    for m in ms:
        scr = m.get("screen")
        if not isinstance(scr, dict):
            continue
        raw = m.get("_zolRaw") or {}

        if scr.get("gamut") is not None:
            target = scr.get("colorGamut")
            if target:
                dropped_g += 1          # 已有正名键，旧键直接删
            else:
                val = gamut_from_raw(raw, scr.get("gamut"))
                if val:
                    moved_g += 1
                    if args.apply:
                        scr["colorGamut"] = val
                    if len(samples) < 10:
                        samples.append((m["id"], scr.get("gamut"), val))
            if args.apply:
                scr.pop("gamut", None)

        if scr.get("brightness") is not None:
            if scr.get("brightnessNit") is not None:
                dropped_b += 1
            else:
                n = _num(scr.get("brightness"), 100, 3000)
                if n is not None:
                    moved_b += 1
                    if args.apply:
                        scr["brightnessNit"] = n
            if args.apply:
                scr.pop("brightness", None)

        if args.apply and (moved_g or moved_b):
            bf = set(m.get("backfilled") or [])
            bf |= {"screen.colorGamut", "screen.brightnessNit"}
            m["backfilled"] = sorted(bf)

    print("色域 gamut → colorGamut：迁 %d 台（已有正名键 %d 台，旧键删除）" % (moved_g, dropped_g))
    print("亮度 brightness → brightnessNit：迁 %d 台（已有正名键 %d 台，旧键删除）" % (moved_b, dropped_b))
    print()
    print("迁移样例：")
    for s in samples:
        print("  %-16s %s -> %s" % s)

    if args.apply:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写入 %s（备份 %s）" % (POOL, bak))
    else:
        print("\n（演练模式，未写文件；加 --apply 落盘）")


if __name__ == "__main__":
    main()
