# -*- coding: utf-8 -*-
"""zol_map_fields.py —— 把 zol_recrawl.py 抓回的原始参数映射进 machine_pool

采集（`zol_recrawl.py` → `data/zol/params-2026-11-16.jsonl`）与映射分开：
改映射规则不用重新联网，重跑本脚本即可。

## 映射原则（与项目纪律一致）

1. **只填 None，绝不覆盖已有值。** 已有值来自人工核对/京东实采，优先级高于 ZOL 参数表。
2. **不适用的写 0/null 的区分要保留**：核显的 `vramGB` 写 `0`（AGENTS.md §4.1），
   `显存容量=共享内存容量` → 0 + `gpu.type=integrated`。
3. **推不出的不填。** 例如「功耗 55W」是 **CPU 功耗**，不能拿来当 `gpu.tgpMaxW`。
4. 每个写入的字段都记进 `backfilled` 与 `dataFrom`，便于回滚与溯源。
5. `--dry-run` 先看 diff。

## 字段对照

| ZOL 参数 | 池字段 | 处理 |
|---|---|---|
| CPU型号 | cpu.model | 去「Intel 酷睿」前缀 → 归一写法 |
| 核心/线程数 | cpu.cores / cpu.threads | 「20核心/20线程」「八核心/十二线程」 |
| 功耗 | cpu.maxW | 标称 TDP，不是持续功耗 |
| 内存容量 | ram.gb | 「16GB」「32GB（16GB×2）」 |
| 内存类型 | ram.type | |
| 插槽数量 | ram.slots / ram.form | 2xSO-DIMM → slots=2, form=sodimm |
| 最大内存容量 | ram.maxGB | 与 slots=0 一起出现时 form=soldered |
| 硬盘容量 | storageGB | 「1TB」→1024 |
| 显卡类型 | gpu.type | 核芯显卡→integrated，独立→discrete |
| 显卡芯片 | gpu.model | |
| 显存容量 | gpu.vramGB | 共享内存容量→0 |
| 显卡最高功耗 | gpu.tgpMaxW | 只认这个键，绝不拿「功耗」冒充 |
| 屏幕分辨率 | screen.widthPx / heightPx | |
| 色域 / sRGB色域 / DCI-P3色域 / NTSC色域 | screen.colorGamut | 归一到 {srgb,dciP3,ntsc} |
| 亮度 | screen.brightnessNit | 「500nits」→500 |
| 屏幕类型 | screen.panel | |
| 屏幕刷新率 | screen.refreshHz | |
| 电池类型 | batteryWh | 「83瓦时」 |
| 笔记本重量 | weightKg | |
| 数据接口 / 视频接口 / 音频接口 / 其它接口 / 读卡器 | ports | 合并成数组 |
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


def num(s):
    if s is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group(0)) if m else None


def as_int(s):
    v = num(s)
    return int(v) if v is not None and v == int(v) else (int(v) if v is not None else None)


def cn_digits(s):
    """「八核心/十二线程」→ (8, 12) 这类中文数字。

    🔴 「双」必须映射：老机型全写「双核心/四线程」（2015 年那批 i5-5200U 机器），
       漏了它这 12 台 cores 永远是 None，而 cores 是「理工实验」组的 R 级字段。
    """
    table = {"单": 1, "一": 1, "两": 2, "二": 2, "双": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
             "十三": 13, "十四": 14, "十五": 15, "十六": 16, "十七": 17, "十八": 18,
             "十九": 19, "二十": 20, "二十四": 24, "三十二": 32}
    if not s:
        return None
    if s.isdigit():
        return int(s)
    return table.get(s)


def vendor_of(cpu_str):
    """CPU 厂商。**认不出返回 None，不许默认 Intel。**

    🔴 踩过：`麒麟XE90` / `麒麟X90 Plus` 是华为自研，旧写法 else 到 Intel，
       结果两台华为国产芯片机被标成 Intel —— 这种错会让「理工实验」的
       CPU 匹配整条走错，而且看数据时完全看不出来。
    """
    s = str(cpu_str or "")
    if re.search(r"AMD|Ryzen|锐龙|Athlon|羿龙", s, re.I):
        return "AMD"
    if re.search(r"Apple|苹果|\bM[1-9]\b", s, re.I):
        return "Apple"
    if re.search(r"麒麟|Kirin|鲲鹏|Kunpeng|兆芯|海光|龙芯|Loongson|申威", s, re.I):
        return "华为" if re.search(r"麒麟|Kirin|鲲鹏|Kunpeng", s, re.I) else "国产"
    if re.search(r"Intel|英特尔|酷睿|Core|Celeron|Pentium|赛扬|奔腾|\bi[3579][- ]?\d{4,5}|Ultra\s?[3579]|N\d{2,3}\b", s, re.I):
        return "Intel"
    if re.search(r"联发科|天玑|MediaTek", s, re.I):
        return "MediaTek"
    if re.search(r"高通|骁龙|Snapdragon|Qualcomm", s, re.I):
        return "Qualcomm"
    return None


def parse_cores(v):
    """「20核心/20线程」「八核心/十二线程」「12核心」→ (cores, threads)"""
    if not v:
        return None, None
    m = re.search(r"([\d单双一两二三四五六七八九十]+)\s*核心\s*(?:[/／]\s*([\d单双一两二三四五六七八九十]+)\s*线程)?", v)
    if not m:
        return None, None
    c = cn_digits(m.group(1))
    t = cn_digits(m.group(2)) if m.group(2) else None
    return c, t


# ZOL 参数值里嵌着大量营销尾巴，实测形态：
#   「Intel 酷睿 Ultra 7 255HX」                        —— 干净
#   「联发科 天玑 7025-Ultra 更多联发科 天玑 7025-Ultra手机> ， 手机性能排行」
#   「1TB>」「sRGB：100%>」                              —— 尾部残留 ">"
# 不剥干净会写进库：cpu.model 变成一整句话，判定必然失配。
_TAIL = re.compile(r"(更多[^<]*?(笔记本|手机|排行)[^<]*|手机性能排行|笔记本性能排行|.*?排行>?)")
WS = re.compile(r"\s+")

# 非笔记本：手机 / 平板 / 相机。名字和参数页都有特征词。
_NON_PC = re.compile(
    r"(iPhone|iPone|手机|平板|Pura|nova|Redmi\s*Note|骁龙|天玑|Exynos|"
    r"^\s*vivo|^\s*OPPO|Mate\s*Pad|iPad|相机|镜头|微单|单反)", re.I)


def clean_val(v):
    """剥掉 ZOL 值里的营销尾巴。"""
    if v is None:
        return None
    s = str(v)
    s = re.split(r"更多", s)[0]
    s = s.replace("手机性能排行", "").replace("笔记本性能排行", "")
    s = s.strip(" <>，,。;；")
    return s.strip() or None


def norm_cpu(v):
    """`Intel 酷睿 Ultra 7 255HX` → `Ultra 7 255HX`；`AMD Ryzen 7 H 255` → `Ryzen 7 H 255`

    只剥厂商前缀，**不剥「酷睿」**：剥了会把 `Intel 酷睿5 320` 变成 `5 320`，
    而 bench.json 里的芯片 id 是 `酷睿5-320` —— 一剥就再也匹配不上。
    """
    s = clean_val(v)
    if not s:
        return None
    s = re.sub(r"^(Intel|AMD|英特尔|超微|Apple|苹果)\s*", "", s, flags=re.I).strip()
    # 压缩空白并去掉「酷睿」后面的空格：`酷睿 5 320` / `酷睿5 320` 必须是同一个写法，
    # 否则同一颗 U 在同一份库里出现两种拼法，cpuId 归一就失配（AGENTS.md §8.5）。
    s = WS.sub(" ", s)
    s = re.sub(r"(酷睿|Ryzen|锐龙)\s+", r"\1", s)
    return s or None


def parse_gamut(p):
    """把 色域 / sRGB色域 / DCI-P3色域 / NTSC色域 归一到 {srgb,dciP3,ntsc}。"""
    out = {}
    for key, dst in (("sRGB色域", "srgb"), ("DCI-P3色域", "dciP3"), ("NTSC色域", "ntsc")):
        v = p.get(key)
        n = num(v)
        if n is not None:
            out[dst] = int(n)
    raw = p.get("色域")
    if raw:
        # 两种写法都见过：
        #   `sRGB：100%`、`DCI-P3：100%`        —— 标准在前
        #   `72%NTSC，100%sRGB`、`100%DCI-P3广色域` —— 数字在前（荣耀猎人/拯救者Y7000X）
        # 只写第一种正则，第二种会被整段丢掉（实测漏 2 台，其中一台 100% DCI-P3）。
        for seg in re.split(r"[，,；;]\s*", raw):
            m = re.search(r"(DCI-P3|sRGB|NTSC|P3|Adobe\s*RGB)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%", seg, re.I)
            if not m:
                m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(DCI-P3|sRGB|NTSC|Adobe\s*RGB|P3)", seg, re.I)
                if m:
                    std, numtxt = m.group(2), m.group(1)
                else:
                    continue
            else:
                std, numtxt = m.group(1), m.group(2)
            name = std.lower()
            val = int(float(numtxt))
            if "p3" in name:
                out.setdefault("dciP3", val)
            elif "srgb" in name:
                out.setdefault("srgb", val)
            elif "ntsc" in name:
                out.setdefault("ntsc", val)
            elif "adobe" in name:
                out.setdefault("adobeRGB", val)
        m2 = re.search(r"^(\d+(?:\.\d+)?)\s*%\s*(?:sRGB|P3)", raw, re.I)
        if m2 and "srgb" not in out:
            out["srgb"] = int(float(m2.group(1)))
        elif raw.strip() in ("P3 广色域", "广色域"):
            out.setdefault("dciP3", 100)
    return out or None


def parse_ram_gb(v):
    """`32GB（16GB×2）` → 32；`16GB` → 16"""
    if not v:
        return None
    m = re.search(r"(\d+)\s*GB", str(v))
    return int(m.group(1)) if m else None


def parse_storage(v):
    """`1TB` → 1024；`512GB` → 512；`1TB+512GB` → 1024（取主盘）"""
    if not v:
        return None
    s = str(v)
    m = re.search(r"(\d+(?:\.\d+)?)\s*TB", s, re.I)
    if m:
        return int(float(m.group(1)) * 1024)
    m = re.search(r"(\d+)\s*GB", s, re.I)
    return int(m.group(1)) if m else None


def parse_slots(p):
    """返回 (form, slots)。`2xSO-DIMM`→('sodimm',2)；无插槽键但有「板载」描述→('soldered',0)"""
    v = p.get("插槽数量")
    if v:
        m = re.search(r"(\d)\s*[x×]\s*(SO-?DIMM|DIMM)", str(v), re.I)
        if m:
            return "sodimm", int(m.group(1))
        if re.search(r"板载|焊死|onboard", str(v), re.I):
            return "soldered", 0
    t = (p.get("内存类型") or "")
    if re.search(r"LPDDR|板载", t, re.I) and not v:
        # LPDDR 一律板载（手机/轻薄本常见），但只在明确没有插槽键时保守标注
        return "soldered", 0
    return None, None


def parse_vram(p):
    """返回 (vramGB, gpuType)。核显 → (0,'integrated')。"""
    cap = p.get("显存容量")
    gtype = p.get("显卡类型") or ""
    if cap and re.search(r"共享", str(cap)):
        return 0, "integrated"
    n = num(cap)
    if n is not None:
        return int(n), ("integrated" if re.search(r"核芯|核显", gtype) else "discrete")
    if re.search(r"核芯显卡|核显|集成显卡", gtype):
        return 0, "integrated"
    return None, None


def parse_ports(p):
    """合并接口字段成端口数组。

    🔴 只认「接口」类键。**绝不拿「无线网卡」凑数** —— WiFi 不是接口，
       写进 ports 会让 `has_usb_a()` / `has_rj45()` 的判断全部失真
       （拯救者那 15 台就是只有网卡键、没有任何接口键，属于页面确实没有）。

    🔴 不能按逗号无脑切：华为折叠本写的是
       `折叠态：2×USB-C接口（左侧1个，上侧1个）`
       按逗号切会把括号劈成两半，得到一个残缺的 `（左侧1个`。
       括号内的逗号必须保护起来。
    """
    out = []
    for key in ("数据接口", "视频接口", "音频接口", "其它接口", "读卡器"):
        v = p.get(key)
        if not v:
            continue
        for seg in _split_ports(str(v)):
            seg = seg.strip(" 。.;；")
            if len(seg) >= 2:
                out.append(seg)
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq or None


def _split_ports(text):
    """按逗号/分号切，但括号内的分隔符不切。"""
    parts, buf, depth = [], "", 0
    for ch in text:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        if depth == 0 and ch in ",，;；":
            parts.append(buf)
            buf = ""
            continue
        buf += ch
    parts.append(buf)
    return [x for x in (s.strip() for s in parts) if x]


def wired_rj45(p):
    """有线网卡是千兆/百兆以太网 → 必有 RJ45 物理口。返回证据文本或 None。"""
    v = p.get("有线网卡")
    if not v:
        return None
    if re.search(r"\d+\s*Mbps\s*以太网卡|以太网", str(v), re.I) and "无线" not in str(v):
        return str(v).strip()
    return None


def parse_weight(v):
    if not v:
        return None
    n = num(str(v).replace("Kg", "").replace("kg", ""))
    return round(n, 2) if n else None


def parse_battery(v):
    if not v:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*瓦时", str(v))
    if m:
        return round(float(m.group(1)), 1)
    m = re.search(r"(\d{3,4})\s*Wh", str(v), re.I)
    return int(m.group(1)) if m else None


def parse_brightness(v):
    if not v:
        return None
    m = re.search(r"(\d{3,4})\s*(?:nits?|尼特)", str(v), re.I)
    return int(m.group(1)) if m else None


def parse_hz(v):
    if not v:
        return None
    m = re.search(r"(\d{2,4})\s*Hz", str(v), re.I)
    return int(m.group(1)) if m else None


def parse_res(v):
    """`2560x1600` / `2560×1600` / `2560*1600` / `HD，1366x768`

    🔴 星号是真见过的写法（机械革命极光X 五台全是 `2560*1600`）。
       首版只认 [x×]，那五台分辨率全被判"缺"，白补一轮。
    """
    if not v:
        return None, None
    m = re.search(r"(\d{3,4})\s*[x×X*]\s*(\d{3,4})", str(v))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def build_patch(m, p):
    """对一台机器算出一个「只补空」的 patch。返回 (patch, notes)。"""
    patch, notes = {}, []

    # 手机/平板/相机不能按笔记本口径补：ZOL 列表页混进来 7 台手机
    # （iPhone / vivo / nova / Redmi Note / Pura X）。它们也有「内存容量」「硬盘容量」，
    # 但那是手机口径，补进来会让覆盖率分母虚高、结论全错。
    # 这里只跳过、不删除 —— 删数据要用户拍板。
    if _NON_PC.search(" ".join(str(x or "") for x in
                                (m.get("name"), m.get("positioning"), p.get("产品定位")))):
        return {}, ["NON_PC 跳过（手机/平板，非笔记本口径）"]


    cpu = dict(m.get("cpu") or {})
    if cpu.get("model") in (None, "", None) or re.search(r"系列$", str(cpu.get("model") or "")):
        nm = norm_cpu(p.get("CPU型号"))
        if nm:
            patch["cpu.model"] = nm
            notes.append("cpu.model ← CPU型号（原值是系列名或缺）")
    c, t = parse_cores(p.get("核心/线程数"))
    if cpu.get("cores") is None and c:
        patch["cpu.cores"] = c
        notes.append("cpu.cores ← 核心/线程数")
    if cpu.get("threads") is None and t:
        patch["cpu.threads"] = t
    if cpu.get("baseW") is None and cpu.get("maxW") is None:
        w = num(p.get("功耗"))
        if w:
            patch["cpu.maxW"] = int(w)
            notes.append("cpu.maxW ← 功耗（标称 TDP）")
    fam = re.search(r"Ultra|Ryzen|酷睿|Core|Celeron|Pentium|M\d", str(p.get("CPU型号") or cpu.get("model") or ""), re.I)
    if cpu.get("vendor") is None and p.get("CPU型号"):
        vendor = vendor_of(p["CPU型号"])
        if vendor:
            patch["cpu.vendor"] = vendor

    ram = dict(m.get("ram") or {})
    if ram.get("gb") is None:
        g = parse_ram_gb(p.get("内存容量"))
        if g:
            patch["ram.gb"] = g
    if ram.get("type") is None and p.get("内存类型"):
        patch["ram.type"] = re.sub(r">$", "", p["内存类型"]).strip()
    form, slots = parse_slots(p)
    if ram.get("form") is None and form:
        patch["ram.form"] = form
    if ram.get("slots") is None and slots is not None:
        patch["ram.slots"] = slots
    if ram.get("maxGB") is None:
        mg = num(p.get("最大内存容量"))
        if mg:
            patch["ram.maxGB"] = int(mg)
    if ram.get("config") is None:
        cfg = re.search(r"（(\d+GB)\s*[×x]\s*(\d+)）", str(p.get("内存容量") or ""))
        if cfg:
            patch["ram.config"] = "%s×%s" % (cfg.group(1), cfg.group(2))

    if m.get("storageGB") is None:
        s = parse_storage(p.get("硬盘容量"))
        if s:
            patch["storageGB"] = s

    gpu = dict(m.get("gpu") or {})
    vram, gtype = parse_vram(p)
    if gpu.get("vramGB") is None and vram is not None:
        patch["gpu.vramGB"] = vram
    if gpu.get("type") is None and gtype:
        patch["gpu.type"] = gtype
    if gpu.get("model") is None:
        chip = p.get("显卡芯片")
        if chip:
            patch["gpu.model"] = re.sub(r">$", "", chip).strip()
            if gpu.get("vendor") is None:
                patch["gpu.vendor"] = "NVIDIA" if re.search(r"GeForce|RTX|GTX|NVIDIA", chip, re.I) else (
                    "AMD" if re.search(r"Radeon|AMD", chip, re.I) else (
                        "Intel" if re.search(r"Intel|Arc|UHD", chip, re.I) else (
                            "Apple" if re.search(r"Apple", chip, re.I) else None)))
    elif gpu.get("vendor") is None and gpu.get("model"):
        mdl = str(gpu["model"])
        patch["gpu.vendor"] = "NVIDIA" if re.search(r"NVIDIA|RTX|GTX", mdl, re.I) else (
            "AMD" if re.search(r"Radeon|AMD|RX\s?\d", mdl, re.I) else (
                "Intel" if re.search(r"Intel|Arc|UHD|Iris", mdl, re.I) else (
                    "Apple" if re.search(r"Apple", mdl, re.I) else "other")))
    if gpu.get("tgpMaxW") is None:
        w = num(p.get("显卡最高功耗"))
        if w:
            patch["gpu.tgpMaxW"] = int(w)
            notes.append("gpu.tgpMaxW ← 显卡最高功耗")

    scr = dict(m.get("screen") or {})
    if scr.get("widthPx") is None or scr.get("heightPx") is None:
        w, h = parse_res(p.get("屏幕分辨率"))
        if w:
            patch["screen.widthPx"] = w
            patch["screen.heightPx"] = h
    if not scr.get("colorGamut"):
        gam = parse_gamut(p)
        if gam:
            patch["screen.colorGamut"] = gam
    if scr.get("brightnessNit") is None:
        b = parse_brightness(p.get("亮度"))
        if b:
            patch["screen.brightnessNit"] = b
    if scr.get("panel") is None and p.get("屏幕类型"):
        patch["screen.panel"] = re.sub(r">$", "", p["屏幕类型"]).strip()
    if scr.get("refreshHz") is None:
        hz = parse_hz(p.get("屏幕刷新率"))
        if hz:
            patch["screen.refreshHz"] = hz
    if scr.get("sizeInch") is None:
        inch = num(p.get("屏幕尺寸"))
        if inch:
            patch["screen.sizeInch"] = round(inch, 1)

    if m.get("batteryWh") is None:
        b = parse_battery(p.get("电池类型"))
        if b:
            patch["batteryWh"] = b
    if m.get("weightKg") is None:
        w = parse_weight(p.get("笔记本重量"))
        if w:
            patch["weightKg"] = w
    if not m.get("ports"):
        ps = parse_ports(p)
        if ps:
            patch["ports"] = ps
        else:
            # 🔴 这里**故意不补**。
            # 「有线网卡 = 千兆以太网」确实意味着有 RJ45 口，但用它填 ports 会让
            # lib_judge 的 port_has(m,'usb-a') 返回 False，于是判出
            # 「没有 USB-A，需另配扩展坞」—— 而真相是我们**不知道**它有没有 USB-A。
            # （lib_judge §3.1 明确写着：ports 整条缺失时不能判"没有"，那是把不知道说成缺。）
            # 一条 RJ45 换来的不是覆盖率，是一条假结论。所以只在 notes 里留痕，不写 ports。
            rj = wired_rj45(p)
            if rj:
                notes.append("ports 仍缺：页面无任何接口键，仅有『有线网卡：%s』可推定有 RJ45，"
                             "但不足以判定 USB-A / 读卡器 —— 按纪律留空" % rj)

    return patch, notes


def apply_path(m, path, value):
    if "." not in path:
        m[path] = value
        return
    head, tail = path.split(".", 1)
    node = m.get(head)
    if not isinstance(node, dict):
        node = {}
        m[head] = node
    apply_path(node, tail, value)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default="", help="只处理这些 id，逗号分隔")
    args = ap.parse_args()

    raw = {}
    for line in open(SRC, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        raw[rec["id"]] = rec

    pool = json.load(open(POOL, encoding="utf-8"))
    only = set(x for x in args.only.split(",") if x)

    total_fields = 0
    touched = 0
    per_field = {}
    for m in pool["machines"]:
        if only and m["id"] not in only:
            continue
        rec = raw.get(m["id"])
        if not rec:
            continue
        patch, notes = build_patch(m, rec.get("params") or {})
        if not patch:
            continue
        touched += 1
        total_fields += len(patch)
        for k in patch:
            per_field[k] = per_field.get(k, 0) + 1
        if not args.apply:
            print("%-16s %-24s %s" % (m["id"], (m.get("name") or "")[:22],
                                      json.dumps(patch, ensure_ascii=False)[:150]))
        else:
            for k, v in patch.items():
                apply_path(m, k, v)
            bf = set(m.get("backfilled") or [])
            bf |= set(patch.keys())
            m["backfilled"] = sorted(bf)
            df = dict(m.get("dataFrom") or {})
            df["backfill_zol_params"] = rec.get("capturedAt")
            m["dataFrom"] = df

    print()
    print("机器 %d 台，字段写入 %d 个" % (touched, total_fields))
    for k, v in sorted(per_field.items(), key=lambda x: -x[1]):
        print("  %-24s %3d" % (k, v))

    if args.apply:
        bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(POOL, bak)
        json.dump(pool, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写入 %s（备份 %s）" % (POOL, bak))
    else:
        print("\n（演练模式，未写文件；加 --apply 落盘）")


if __name__ == "__main__":
    main()
