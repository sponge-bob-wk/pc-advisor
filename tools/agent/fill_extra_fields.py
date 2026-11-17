# -*- coding: utf-8 -*-
"""fill_extra_fields.py —— 补齐「参数页有、但没进池」的维度

## 为什么会有这个脚本

用户问「保修几年」，系统答「库里没记录」。
但 `data/zol/params-2026-11-16.jsonl` 里 **60 台有「质保时间 = 2年」**——
数据早就抓回来了，只是 `zol_map_fields.py` 的映射表没收录这个键。

顺着查了一遍参数页里所有键，发现不止保修：

| 参数页键 | 覆盖台数 | 用户会问吗 |
|---|---:|---|
| `保修政策` / `质保时间` | 60 | ✅「保修几年」 |
| `续航时间` | 84 | ✅「能用多久」 |
| `厚度` | 130 | ✅「厚不厚」 |
| `触控屏` | 121 | ✅「能触屏吗」 |
| `操作系统` | 157 | ✅「带不带 Windows」 |
| `蓝牙` / `无线网卡` | 152 | ✅「有蓝牙吗」 |
| `摄像头` | 146 | ✅「摄像头多少像素」 |
| `扬声器` / `麦克风` | 147 | ✅「音质怎么样」 |
| `键盘描述` | 140 | ✅「有背光吗」 |
| `外壳材质` | 98 | ✅「什么材质」 |
| `上市时间` | 135 | ✅「什么时候出的」 |

**这些都不是"要不要加"的问题，是"已经抓到了却没接上"。**

## 设计

- 只填空，不覆盖已有值（与 `zol_map_fields.py` 同一纪律）
- 每个新字段都带 `dataFrom` 留痕
- 解析失败就跳过，不猜

## 用法

    python fill_extra_fields.py            # 演练
    python fill_extra_fields.py --write    # 落盘
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
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(ROOT, "data")
POOL = os.path.join(DATA, "machine_pool.json")
SRC = os.path.join(DATA, "zol", "params-2026-11-16.jsonl")


def clean(v):
    if v is None:
        return None
    s = str(v)
    s = re.split(r"更多", s)[0]
    s = s.replace("手机性能排行", "").replace("笔记本性能排行", "")
    return s.strip(" <>，,。;；") or None


def num(v):
    if v is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def parse_warranty(p):
    """返回 (质保年数, 保修政策原文, 备注)。"""
    years = None
    t = clean(p.get("质保时间"))
    if t:
        m = re.search(r"(\d+(?:\.\d+)?)\s*年", t)
        if m:
            years = float(m.group(1))
        elif "三包" in t or "整机" in t:
            # 「整机1年，主要部件2年」这类，取最长
            nums = [float(x) for x in re.findall(r"(\d+)\s*年", t)]
            if nums:
                years = max(nums)
    policy = clean(p.get("保修政策"))
    note = clean(p.get("质保备注"))
    if years is None and not policy:
        return None, None, None
    return years, policy, note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    params = {}
    for line in open(SRC, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        params[r["id"]] = r.get("params") or {}

    pool = json.load(open(POOL, encoding="utf-8"))
    ms = pool["machines"]

    stats = {}
    samples = []
    for m in ms:
        p = params.get(m["id"])
        if not p:
            continue
        patch = {}

        # ── 保修 ──
        years, policy, note = parse_warranty(p)
        if years is not None and m.get("warrantyYears") is None:
            patch["warrantyYears"] = years
        if policy and m.get("warrantyPolicy") is None:
            patch["warrantyPolicy"] = policy
        if note and m.get("warrantyNote") is None:
            patch["warrantyNote"] = note

        # ── 续航（小时）──
        if m.get("batteryLifeH") is None:
            bl = clean(p.get("续航时间"))
            if bl:
                h = None
                mm = re.search(r"(\d+(?:\.\d+)?)\s*小时", bl)
                if mm:
                    h = float(mm.group(1))
                if h:
                    patch["batteryLifeH"] = h
                    patch["batteryLifeText"] = bl[:80]

        # ── 厚度 ──
        if m.get("thicknessMm") is None:
            th = num(p.get("厚度"))
            if th and 5 <= th <= 60:
                patch["thicknessMm"] = round(th, 1)

        # ── 触控屏 ──
        if m.get("touchScreen") is None:
            tc = clean(p.get("触控屏"))
            if tc:
                patch["touchScreen"] = not ("不支持" in tc or "非触控" in tc)

        # ── 操作系统 ──
        if m.get("os") is None:
            osv = clean(p.get("操作系统"))
            if osv:
                patch["os"] = osv[:60]

        # ── 无线/蓝牙 ──
        if m.get("wifi") is None:
            w = clean(p.get("无线网卡"))
            if w:
                patch["wifi"] = w[:80]
        if m.get("bluetooth") is None:
            b = clean(p.get("蓝牙"))
            if b:
                patch["bluetooth"] = b[:40]

        # ── 摄像头 ──
        if m.get("webcam") is None:
            c = clean(p.get("摄像头"))
            if c:
                patch["webcam"] = c[:60]

        # ── 音频 ──
        if m.get("speakers") is None:
            s = clean(p.get("扬声器"))
            if s:
                patch["speakers"] = s[:60]
        if m.get("audioSystem") is None:
            a = clean(p.get("音频系统"))
            if a:
                patch["audioSystem"] = a[:80]

        # ── 键盘 ──
        if m.get("keyboard") is None:
            k = clean(p.get("键盘描述"))
            if k:
                patch["keyboard"] = k[:80]

        # ── 外壳材质 ──
        if m.get("shellMaterial") is None:
            sm = clean(p.get("外壳材质"))
            if sm:
                patch["shellMaterial"] = sm[:80]

        # ── 上市时间 ──
        if m.get("launchDate") is None:
            ld = clean(p.get("上市时间"))
            if ld:
                patch["launchDate"] = ld[:30]

        # ── 显存类型（补充 gpu 子字段）──
        gpu = m.get("gpu")
        if isinstance(gpu, dict) and gpu.get("vramType") is None:
            vt = clean(p.get("显存类型"))
            if vt:
                patch["gpu.vramType"] = vt[:20]

        # ── CPU 功耗（2026-11-17 补）──
        #
        # 🔴 **重要更正**：参数页的「功耗」字段**不是整机功耗，是 CPU TDP**。
        #
        #    验证方法（zol-2128664 暗影精灵 11）：
        #      功耗        = 55W     ← 这个
        #      电源适配器   = 230W
        #      显卡最高功耗 = 115W
        #    CPU 55W + 显卡 115W + 主板/内存/屏 ≈ 200W+，
        #    整机绝不可能只有 55W —— 55W 是 i9-14900HX 的 TDP。
        #
        #    所以字段名必须是 `cpu.tdpW`，**不许叫 powerW**。
        #    叫 powerW 会让上层以为"整机 55W，限电 500W 绰绰有余"——
        #    那是错的，而且错得很危险（用户真按这个买会跳闸）。
        #
        #    整机功耗库里**确实没有**。能算的只有：
        #      CPU TDP + 显卡 TGP（两者都有时，是个**下界**，不是实际值）
        cpu2 = m.get("cpu")
        if isinstance(cpu2, dict) and cpu2.get("tdpW") is None:
            pw = clean(p.get("功耗"))
            if pw:
                w = None
                mm = re.search(r"(\d{1,4})\s*W", pw, re.I)
                if mm:
                    w = int(mm.group(1))
                if w and 5 <= w <= 300:
                    patch["cpu.tdpW"] = w

        # ── 显卡最高功耗（笔记本独显的 TGP）──
        # 用户会问「显卡满血吗」。这个数字决定性能释放。
        if isinstance(gpu, dict) and gpu.get("tgpMaxW") is None:
            gp = clean(p.get("显卡最高功耗"))
            if gp:
                w = None
                mm = re.search(r"(\d{1,4})\s*W", gp, re.I)
                if mm:
                    w = int(mm.group(1))
                if w and 5 <= w <= 300:
                    patch["gpu.tgpMaxW"] = w

        # ── 电池容量（Wh）──
        # 用户会问「电池多大」。这个比「续航时间」硬（续航是厂商标称，水分大）。
        if m.get("batteryWh") is None:
            bc = clean(p.get("电池容量"))
            if bc:
                wh = None
                mm = re.search(r"(\d{2,4})\s*Wh", bc, re.I)
                if mm:
                    wh = int(mm.group(1))
                if wh and 20 <= wh <= 200:
                    patch["batteryWh"] = wh

        # ── 电源适配器（2026-11-17 补）──
        # 用户会问「充电快吗」「充电器多大」。参数页写「280W智能交流电源适配器」，
        # 抽出瓦数存成数字，原文一起留（有些写「100W USB Type-C」）。
        if m.get("adapterW") is None:
            ad = clean(p.get("电源适配器"))
            if ad:
                w = None
                mm = re.search(r"(\d{2,4})\s*W", ad, re.I)
                if mm:
                    w = int(mm.group(1))
                if w and 20 <= w <= 500:
                    patch["adapterW"] = w
                patch["adapterText"] = ad[:70]

        # ── 外壳描述（颜色）──
        # 用户会问「有别的颜色吗」。这是纯描述，不做解析。
        if m.get("shellColor") is None:
            sc = clean(p.get("外壳描述"))
            if sc:
                patch["shellColor"] = sc[:60]

        # ── 屏幕技术（刷新率之外的那些：VRR/DC调光/莱茵认证…）──
        if m.get("screenTech") is None:
            st = clean(p.get("屏幕技术"))
            if st:
                patch["screenTech"] = st[:100]

        # ── 指取设备（触控板）──
        if m.get("touchpad") is None:
            tp = clean(p.get("指取设备"))
            if tp:
                patch["touchpad"] = tp[:60]

        # ── 机身尺寸（长×宽，mm）──
        if m.get("bodyLengthMm") is None:
            ln = num(p.get("长度"))
            if ln and 100 <= ln <= 500:
                patch["bodyLengthMm"] = round(ln, 1)
        if m.get("bodyWidthMm") is None:
            wd = num(p.get("宽度"))
            if wd and 100 <= wd <= 500:
                patch["bodyWidthMm"] = round(wd, 1)

        # ── CPU 细节（睿频/缓存/主频）──
        # 这三项属于「问得少但偶尔问」，存进 cpu 子对象。
        cpu = m.get("cpu")
        if isinstance(cpu, dict):
            if cpu.get("boostGHz") is None:
                bg = num(p.get("最高睿频"))
                if bg and 1 <= bg <= 7:
                    patch["cpu.boostGHz"] = round(bg, 1)
            if cpu.get("l3CacheMB") is None:
                l3 = num(p.get("三级缓存"))
                if l3 and 2 <= l3 <= 128:
                    patch["cpu.l3CacheMB"] = round(l3)
            if cpu.get("baseGHz") is None:
                bgh = num(p.get("CPU主频"))
                if bgh and 0.5 <= bgh <= 6:
                    patch["cpu.baseGHz"] = round(bgh, 1)

        if not patch:
            continue
        for k in patch:
            stats[k] = stats.get(k, 0) + 1
        if len(samples) < 5:
            samples.append((m["name"], patch))
        if args.write:
            for k, v in patch.items():
                if "." in k:
                    head, tail = k.split(".", 1)
                    node = m.get(head)
                    if not isinstance(node, dict):
                        node = {}
                        m[head] = node
                    node[tail] = v
                else:
                    m[k] = v
            df = dict(m.get("dataFrom") or {})
            df["extra_fields"] = "ZOL 参数页补录（保修/续航/厚度/触控/网络/音频…）"
            m["dataFrom"] = df
            bf = set(m.get("backfilled") or [])
            bf |= set(patch.keys())
            m["backfilled"] = sorted(bf)

    print("补录字段统计（按写入台数）：")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print("  %-22s %3d 台" % (k, v))
    print()
    print("样例：")
    for name, patch in samples:
        print("  %s" % name[:34])
        for k, v in list(patch.items())[:6]:
            print("     %-20s = %s" % (k, str(v)[:50]))

    if args.write:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print()
        print("已写入 %s（备份 %s）" % (POOL, bak))
    else:
        print()
        print("（演练模式。加 --write 落盘）")


if __name__ == "__main__":
    main()
