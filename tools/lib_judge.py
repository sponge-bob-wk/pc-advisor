#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
lib_judge.py —— 「这台机器够不够这个专业用」的唯一判定实现

为什么要抽出来成一个模块：

  这个判断以前写在 coverage.py 里，是"算覆盖率"专用的。
  现在推荐工具（pick.py）也要用同一套判断。如果各自写一份，
  两边的结论迟早会分叉 —— 覆盖率报"能推荐 89 台"，推荐工具却给出另一批机器，
  而且没人能发现哪个对。同一个问题只能有一个实现。

三条规则写死在代码里（都是踩过的坑，不要"优化"掉）：

  1. 核显遇到显存要求 = 事实不满足（VIOLATE），不是"缺数据"
     核显共享系统内存，物理上没有独立显存。这是确定结论，不是未知。
     把它算成 UNKNOWN 会让"能出结论率"虚低，也会让用户以为补数据就能救。

  2. throttle 缺失 = 降级（DEGRADED），不阻断推荐
     峰值跑分仍可参考，只是不能保证长时间负载不掉速。
     这一条很重要：目前 0/29 台有 throttle 数据，若按"缺失=不推荐"处理，
     视频与引擎组会得到"0 台"，而真相是"有机器但没法保证持续性能"。

  3. efficiency 绝不进门槛判定（它是 P 级）
     P 级只回答"能补多少钱"。放进门槛会导致所有组被能效卡死，得出"0 台可用"的假象。
     国补资格单独在 subsidy 里判断。

判定返回四级：
  OK        —— R 级齐全，无违反，可推荐
  DEGRADED  —— R 级齐全但有违反？不，是 R 级齐全、关键 H 缺失，推荐要带标注
  VIOLATE   —— 已确定不满足（这是可靠结论："这台不够用"，不是"不知道"）
  UNKNOWN   —— R 级字段缺失，无法判断（这才是真缺口）
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

NON_EMPTY = (str, list, dict)


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def present(m, key):
    """字段是否有值。0 / False 算有值；None / "" / [] / {} 算缺失。"""
    if not isinstance(m, dict) or key not in m:
        return False
    v = m[key]
    if v is None:
        return False
    if isinstance(v, NON_EMPTY) and len(v) == 0:
        return False
    return True


def field_value(m, key):
    """把字段矩阵的字段名映射到机型结构，返回 (hasValue, value, 来源说明)。

    来源说明用于告诉用户"这项是推算的"还是"官方标称的"。
    tgpW 尤其需要：tgpBaseW 与 tgpMaxW 是两个不同的数，
    用 base 当 max 会把一台 55W 的机器说成 115W。
    """
    gpu = m.get("gpu") if isinstance(m.get("gpu"), dict) else {}
    ram = m.get("ram") if isinstance(m.get("ram"), dict) else {}
    scr = m.get("screen") if isinstance(m.get("screen"), dict) else {}

    if key == "cpu":
        c = m.get("cpu") if isinstance(m.get("cpu"), dict) else {}
        v = c.get("model")
        return (v is not None, v, "cpu.model")
    if key == "cores":
        c = m.get("cpu") if isinstance(m.get("cpu"), dict) else {}
        v = c.get("cores")
        return (v is not None, v, "cpu.cores")
    if key == "ramGB":
        v = ram.get("gb")
        return (v is not None, v, "ram.gb")
    if key == "ramType":
        # form（sodimm/onboard）比 type（DDR5）更能回答"能不能加内存"
        form = ram.get("form")
        v = form if form not in (None, "unknown") else ram.get("type")
        return (v is not None, v, "ram.form" if form not in (None, "unknown") else "ram.type")
    if key == "storageGB":
        v = m.get("storageGB")
        return (v is not None, v, "storageGB")
    if key == "gpuModel":
        v = gpu.get("model")
        return (v is not None, v, "gpu.model")
    if key == "vramGB":
        v = gpu.get("vramGB")
        return (v is not None, v, "gpu.vramGB")
    if key == "tgpW":
        if gpu.get("tgpMaxW") is not None:
            return True, gpu["tgpMaxW"], "gpu.tgpMaxW"
        if gpu.get("tgpBaseW") is not None:
            return True, gpu["tgpBaseW"], "gpu.tgpBaseW（标称下限，非释放上限）"
        return False, None, "gpu.tgpMaxW"
    if key == "screenInch":
        v = scr.get("sizeInch")
        return (v is not None, v, "screen.sizeInch")
    if key == "resolution":
        w = scr.get("widthPx")
        return (bool(w), w, "screen.widthPx")
    if key == "refreshHz":
        v = scr.get("refreshHz")
        return (v is not None, v, "screen.refreshHz")
    if key == "colorGamut":
        v = scr.get("colorGamut")
        return (bool(v), v, "screen.colorGamut")
    if key == "brightnessNit":
        v = scr.get("brightnessNit")
        return (v is not None, v, "screen.brightnessNit")
    if key == "weightKg":
        v = m.get("weightKg")
        return (v is not None, v, "weightKg")
    if key == "batteryWh":
        v = m.get("batteryWh")
        return (v is not None, v, "batteryWh")
    if key == "ports":
        v = m.get("ports")
        return (bool(v), v, "ports")
    if key == "efficiency":
        v = m.get("efficiency")
        return (v is not None, v, "efficiency")
    if key == "throttle":
        v = m.get("throttle")
        return (v is not None, v, "throttle")
    return False, None, key


def field_present(m, key):
    return field_value(m, key)[0], field_value(m, key)[1]


def is_integrated(m):
    """是不是核显机。不只看 type，也看型号名 —— 数据里两种写法都有。"""
    gpu = m.get("gpu") if isinstance(m.get("gpu"), dict) else {}
    t = (gpu.get("type") or "").lower()
    if t in ("integrated", "核显"):
        return True
    name = (gpu.get("model") or "").lower()
    if any(k in name for k in ("核显", "uhd", "iris", "radeon graphics", "integrated")):
        return True
    # vramGB 为空的独显名（如 "RTX5060" 没写显存）不算核显
    return False


def form_is_soldered(m):
    """内存是否焊死。这是 code 组最在意的硬门槛。

    返回 True=焊死 / False=可换 / None=不知道。
    form 字段：sodimm=插槽 / onboard=焊死。
    form 缺失但 slots>=1 也算可换 —— 有插槽就说明不是全焊。
    """
    ram = m.get("ram") if isinstance(m.get("ram"), dict) else {}
    form = (ram.get("form") or "").lower()
    # 采集记录用 soldered，specs 用 onboard，两种写法都表示焊死。
    # 只认 onboard 会把 14 台焊死机漏判成"可换"，编程组的硬门槛形同虚设。
    if form in ("onboard", "soldered", "板载", "焊死", "solder"):
        return True
    if form in ("sodimm", "so-dimm", "插槽", "socketed", "slot"):
        return False
    slots = ram.get("slots")
    if isinstance(slots, int):
        return slots < 1
    return None


def port_has(m, kind):
    """机器有没有某类接口。ports 是自然语言列表，只能宽松匹配。"""
    ports = m.get("ports")
    if not ports:
        return False
    txt = " ".join(str(x) for x in ports).lower()
    if kind == "usb-a":
        return "usb-a" in txt and "无 usb-a" not in txt
    if kind == "rj45":
        return ("rj45" in txt or "网口" in txt) and "无 rj45" not in txt
    if kind == "card-reader":
        return "读卡器" in txt or "sdxc" in txt
    return False


def is_unified(m):
    """统一内存架构（Apple Silicon）：GPU 与 CPU 共享同一块内存，没有独立显存。

    为什么单列一类：
      Mac 的 GPU 不是"核显"（性能远超传统核显），也不是独显（没有独立显存颗粒）。
      用 vramGB 去卡它永远卡不出来 —— 会一直判 UNKNOWN"缺 vramGB"。
      正确做法是：显存要求改看统一内存容量，因为视频/渲染吃的是同一块内存。
    """
    gpu = m.get("gpu") if isinstance(m.get("gpu"), dict) else {}
    t = (gpu.get("type") or "").lower()
    if t == "unified":
        return True
    # 批次01 字段约定：苹果机 gpu.type 填 integrated、ram.form 填 onboard。
    # 判定上仍要按统一内存处理，否则会被"核显没有独立显存"误拦。
    if (m.get("devicePlatform") or "").lower() == "macos" and t in ("integrated", ""):
        return True
    return False


def merge_reasons(violates):
    """把同因的违反项归并成一句，避免"一件事说三遍"。

    归并维度是「根因」，不是「字段」：
      核显 → 不管拦它的是 vramMin、gpu 要求、还是 hardGates 的 gpu，
             根因都是"这台没有独显"
      内存 → 不管是 req.ramMin 还是 hardGates.ram，根因都是容量不够
      存储 → 同理
    """
    code, other = [], []
    for r in violates:
        if "核显" in r:
            if "code-ic" not in code:
                code.append("code-ic")
        # 「内存焊死」必须先于 startswith("内存") 判断 —— 否则
        # "内存焊死，不能升级" 会以"内存"开头被误归成容量不足，
        # 输出"内存不够"，用户会以为加根内存就行，可它根本加不了。
        elif "内存焊死" in r:
            if "code-solder" not in code:
                code.append("code-solder")
        elif r.startswith("内存"):
            if "code-ram" not in code:
                code.append("code-ram")
        elif r.startswith("存储"):
            if "code-ssd" not in code:
                code.append("code-ssd")
        else:
            if r not in other:
                other.append(r)

    out = []
    # 核显那条要带上组里真正要求的显存数，否则"没显存"太笼统
    if "code-ic" in code:
        out.append("核显机（没有独立显卡）")
    if "code-ram" in code:
        nums = [r for r in violates if r.startswith("内存") and "低于" in r]
        want = None
        for r in nums:
            for token in r.replace("，", " ").split():
                if token.endswith("G") and token[:-1].isdigit():
                    v = int(token[:-1])
                    want = v if want is None else max(want, v)
        out.append(f"内存不够{('（要求 ' + str(want) + 'G）') if want else ''}")
    if "code-ssd" in code:
        nums = [r for r in violates if r.startswith("存储")]
        want = None
        for r in nums:
            for token in r.replace("，", " ").split():
                if token.endswith("G") and token[:-1].isdigit():
                    v = int(token[:-1])
                    want = v if want is None else max(want, v)
        out.append(f"存储不够{('（要求 ' + str(want) + 'G）') if want else ''}")
    if "code-solder" in code:
        out.append("内存焊死，不能升级")
    out.extend(other)
    return out or violates


def judge(m, req, r_fields, hard_gates=None):
    """返回 (verdict, reasons, missing_fields)

    verdict: OK / DEGRADED / VIOLATE / UNKNOWN
    reasons: 人话解释，直接给用户看
    missing: 卡住的 R 级字段名列表（只有 UNKNOWN 时非空）
    """
    # ---------- 0) 先看这台机器的身份是否成立 ----------
    # 如果规格里有未裁决的身份冲突（例如 CPU 型号对不上），那这台机器
    # 本身就不确定是哪个 SKU —— 它的"够用"没有意义，因为可能根本买不到。
    # 这种不能算 OK，也不能算 UNKNOWN（不是缺数据，是数据矛盾）。
    if m.get("priceBlockedBy"):
        return "VIOLATE", [f"规格与在售 SKU 不是同一台（{m['priceBlockedBy']}），无法推荐"], []

    req = req or {}
    reasons = []
    gpu = m.get("gpu") if isinstance(m.get("gpu"), dict) else {}
    ram = m.get("ram") if isinstance(m.get("ram"), dict) else {}

    # ---------- 0.5) 平台适配（macOS 能不能学这个专业） ----------
    # 不是"性能够不够"，而是"软件装不装得上"。Revit / SolidWorks / ArcGIS Pro /
    # Multisim 这类都是 Windows 独占，Mac 跑不了就是跑不了，虚拟机也不是正经解法。
    # 这类必须判 VIOLATE 而不是 DEGRADED —— 性能差能忍，交不了作业不能忍。
    dev_os = (m.get("devicePlatform") or "windows").lower()
    unfit = (req.get("platformUnfit") or {}).get(dev_os)
    if unfit:
        _os = {"macos": "macOS", "windows": "Windows"}.get(dev_os, dev_os)
        return "VIOLATE", [f"{_os} 装不了这一组的核心软件：{unfit}"], []

    # ---------- 1) 硬门槛先行 ----------
    # 已确定的不满足，不能因为别的字段缺失就降级成"未知"。
    # 一台核显机不会因为"不知道屏幕色域"就变成"可能够用"。
    violates = []

    vneed = req.get("vramMin") or 0
    if vneed:
        if is_unified(m):
            # 统一内存架构：显存来自同一块内存，按内存容量判断是否够用
            _ram = (ram.get("gb") or 0)
            if _ram and _ram < vneed:
                violates.append(f"统一内存 {_ram}G，低于这一组要求的 {vneed}G 显存等效容量")
        elif is_integrated(m):
            violates.append(f"核显没有独立显存，达不到 {vneed}G")
        elif gpu.get("vramGB") is not None and gpu["vramGB"] < vneed:
            violates.append(f"显存 {gpu['vramGB']}G，低于 {vneed}G")

    rneed = req.get("ramMin")
    if rneed and ram.get("gb") is not None and ram["gb"] < rneed:
        violates.append(f"内存 {ram['gb']}G，低于 {rneed}G")

    sneed = req.get("storageMinGB")
    if sneed and m.get("storageGB") and m["storageGB"] < sneed:
        violates.append(f"存储 {m['storageGB']}G，低于 {sneed}G")

    gpu_req = (req.get("gpu") or "").lower()
    if gpu_req in ("discrete", "独显", "必须独显") and is_integrated(m):
        violates.append("要求独立显卡，这台是核显")

    # hardGates 是专业组自己声明的硬性条件，例如 code 组的「内存必须可加装」
    for gate in (hard_gates or []):
        dim, mn = gate.get("dim"), gate.get("min")
        if dim == "ram" and mn and ram.get("gb") is not None and ram["gb"] < mn:
            violates.append(f"内存 {ram['gb']}G 低于硬门槛 {mn}G")
        elif dim == "gpu" and mn:
            # gpu 的 min 按 majors.json 的 dimensions.scale 定义：
            #   0 核显 / 1 入门独显(4G) / 2 中端独显(6-8G) / 3 高端独显(12G+)
            # 所以 min=2 要求的是 6G 起，不是 4G —— 按 4G 判会把勉强及格的机器放过去。
            need_vram = {0: 0, 1: 4, 2: 6, 3: 12}.get(int(mn), 0)
            if mn >= 1 and is_integrated(m):
                violates.append("硬门槛要求独立显卡，这台是核显")
            elif need_vram and gpu.get("vramGB") is not None and gpu["vramGB"] < need_vram:
                violates.append(f"显存 {gpu['vramGB']}G，低于这一档硬门槛要求的 {need_vram}G")
        elif dim == "storage" and mn and m.get("storageGB") and m["storageGB"] < mn:
            violates.append(f"存储 {m['storageGB']}G 低于硬门槛 {mn}G")
        elif dim == "upgrade" and mn:
            # upgrade>=2 含义：内存必须可加装。焊死就是硬伤。
            if form_is_soldered(m) is True:
                violates.append("内存焊死，四年后无法升级（这一组最不能接受）")

    if violates:
        # 去重：同一个事实会被三条规则各说一遍
        #   「核显没有独立显存」——vramMin 检查说的
        #   「要求独立显卡，这台是核显」——gpu 要求检查说的
        #   「硬门槛要求独立显卡」——hardGates 说的
        # 三句话是同一件事。对用户来说这看起来像"错了三处"，
        # 实际只有一处，会让人误判严重程度。所以按"根因"归并。
        deduped = merge_reasons(violates)
        # Mac 被内存/存储门槛拦掉时，要说明"这是 PC 口径"——
        # 门槛来自 Premiere/AE 等 PC 端官方建议，统一内存的实际表现未必一样。
        # 不换算是不想给没出处的等效，但也不能让用户以为结论是终局。
        if (m.get("devicePlatform") or "").lower() == "macos" and any("内存" in str(x) for x in deduped):
            deduped = list(deduped) + [
                "（注：内存门槛取自 PC 端软件官方建议，Mac 统一内存的实际表现可能更宽裕；"
                "本工具没做等效换算，这条结论对 Mac 偏保守，建议单独确认）"]
        return "VIOLATE", deduped, []

    # ---------- 1.5) 声明了门槛、但取不到值 —— 不能当成"已满足" ----------
    # 这是个真实的漏洞，踩过一次：机械革命 蛟龙 15K 的内存容量是 null，
    # 而「电气与自动化」「理工实验」的 R 级字段里只有 ports / cores，
    # 没有 ramGB。于是 ramMin=16 这条门槛因为"值不知道"被整条跳过，
    # 这台机器被判成"够用"并推给了用户 —— 等于拿一个未知数当合格证。
    #
    # 规则：只要 req 或 hardGates 里声明了某个维度的门槛，该维度的实际值就必须已知。
    # 不知道 = UNKNOWN，不是 OK。核显已在上面判为 VIOLATE，不会走到这里。
    unknown_gate = []
    if rneed and ram.get("gb") is None:
        unknown_gate.append("ramGB")
    if sneed and m.get("storageGB") is None:
        unknown_gate.append("storageGB")
    if vneed and gpu.get("vramGB") is None and not is_integrated(m) and not is_unified(m):
        unknown_gate.append("vramGB")
    for gate in (hard_gates or []):
        dim, mn = gate.get("dim"), gate.get("min")
        if not mn:
            continue
        if dim == "ram" and ram.get("gb") is None:
            unknown_gate.append("ramGB")
        elif dim == "storage" and m.get("storageGB") is None:
            unknown_gate.append("storageGB")
        elif dim == "gpu" and gpu.get("vramGB") is None and not is_integrated(m):
            unknown_gate.append("vramGB")
        elif dim == "upgrade" and form_is_soldered(m) is None:
            unknown_gate.append("ramType")
    if unknown_gate:
        uniq = list(dict.fromkeys(unknown_gate))
        return "UNKNOWN", [f"缺 {x}" for x in uniq], uniq

    # ---------- 2) R 级字段齐全性 ----------
    missing = []
    for f in r_fields:
        # throttle 绝不能在这里拦 —— 它缺了走下面的降级逻辑（DEGRADED），
        # 不是"判不了"。曾把 throttle 留在 R 级清单里，结果视频/引擎组
        # 0/32 台有 throttle，三台 32G+独显+1T 的机器全部卡成 UNKNOWN，
        # "有机器但没法保证持续性能"被表达成了"不知道能不能用"。
        if f == "throttle":
            continue
        if not field_present(m, f)[0]:
            missing.append(f)
    if missing:
        return "UNKNOWN", [f"缺 {x}" for x in missing], missing

    # ---------- 3) 降级项 ----------
    degraded = []
    gpu_needed = bool(vneed) or gpu_req in ("discrete", "独显", "必须独显")
    if gpu_needed and not present(m, "throttle"):
        degraded.append("缺持续性能数据（长时间跑会掉多少速未核实）")

    # 3.1) 接口缺口：不是"不能用"，是"要另花钱买扩展坞"。
    #      电气接仪器要 USB-A、连工控要网口；音频接声卡要 USB-A；测绘导数据要读卡器或 USB-A。
    #      这类判 DEGRADED 而不是 VIOLATE —— 一个百来块的扩展坞能解决，
    #      但它的成本必须让用户提前知道，不能等买回来才发现。
    need = req.get("portNeeds") or []
    # 接口字段整条缺失时不能判"没有" —— 那是把"不知道"说成"缺"。
    # 真实踩过：旷世X 的 ports 是 null，被误报成"没有 USB-A"。
    if need and not m.get("ports"):
        need = []
    if need:
        lack = []
        for n in need:
            label = {"usb-a": "USB-A", "rj45": "网口", "card-reader": "读卡器"}.get(n, n)
            if not port_has(m, n):
                lack.append(label)
        if lack:
            degraded.append(f"没有 {'/'.join(lack)}，这一组要接仪器或导素材，"
                            f"需另配扩展坞（约 100-300 元），并且占用唯一的接口")

    # 3.2) 无风扇机型：性能不是不够，是"持续不了"。
    #      MacBook Air 全系无风扇，剪 4K/渲染时间一长就撞温度墙降频；
    #      有风扇的 Pro 才扛得住。这条直接影响视频组该选 Air 还是 Pro。
    if req.get("preferActiveCooling") and m.get("fanless"):
        th = m.get("throttle") if isinstance(m.get("throttle"), dict) else {}
        pct = th.get("sustainedDropPct")
        # 有实测就用实测，没有就说"会降频"——别把没量化的说成量化了
        tail = f"（实测持续 20 分钟多核衰减约 {pct}%）" if pct else ""
        degraded.append(f"无风扇被动散热，长时间导出/渲染会降频{tail} —— "
                        f"短片段没问题，一小时以上的成片建议选带风扇的机型")

    return ("DEGRADED" if degraded else "OK"), degraded, []


VERDICT_CN = {
    "OK": "够用",
    "DEGRADED": "够用（有保留）",
    "VIOLATE": "不够用",
    "UNKNOWN": "数据不足",
}


def r_fields_of(matrix, gid):
    """取某专业组的 R 级字段列表。顺序固定，保证输出稳定。"""
    cells = matrix.get(gid) or {}
    return [k for k, v in cells.items() if v == "R"]


def subsidy_of(net, rules):
    """按国补规则算理论补贴额。

    电脑走「家电类」：实付 15%、单台封顶 1500、无价格门槛。
    注意 threshold 是 exists 才判断 —— 手机线有 6000 门槛，电脑线没有。
    """
    if not net:
        return None
    r = rules.get("pc") or {}
    if r.get("threshold") and net > r["threshold"]:
        return 0
    amt = net * (r.get("rate") or 0) / 100.0
    if r.get("cap"):
        amt = min(amt, r["cap"])
    return round(amt, 2)