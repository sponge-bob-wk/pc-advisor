# -*- coding: utf-8 -*-
"""fit_rules.py —— 机箱/显卡/散热器的物理兼容性

## 为什么需要

装机最容易翻车的不是参数，是**尺寸**：
  · 显卡 340mm 塞进限长 320mm 的机箱 → 装不进去
  · 水冷 360 冷排，机箱只支持 240 → 装不上
  · 风冷高 160mm，机箱限高 155mm → 侧板盖不上
  · MATX 主板装 ITX 机箱 → 放不下

这些在参数表上都是对的，但合起来是坑。

## 数据来源（2026-11-26 新抓）

ZOL 参数页 `/{id前4位}/{id}/param.shtml` 有完整数据：

  机箱：
    显卡限长          400mm
    CPU散热器限高     160mm
    适用主板          MATX板型，ITX板型
    支持水冷          顶置：360mm水冷排 / 主板前侧240mm水冷排
    机箱结构          MATX

  显卡：
    产品尺寸          336.66×134×59.4mm
    建议电源          1000W以上

  散热器：
    散热器高度        （风冷有，水冷看冷排尺寸）

## 判据

  ① 显卡长度 ≤ 机箱显卡限长（留 10mm 余量给供电线）
  ② 散热器高度 ≤ 机箱 CPU 散热器限高（风冷）
     或 冷排尺寸 ≤ 机箱支持的水冷尺寸（水冷）
  ③ 主板板型 ∈ 机箱适用主板
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")


def parse_mm(text):
    """从「400mm」「336.66×134×59.4mm」里抠尺寸（mm）。

    🔴 2026-11-26 修的 bug：
        第一版正则 `(\\d{2,4}(?:\\.\\d+)?)\\s*mm` 只能匹配紧邻 mm 的那个数，
        所以「336.66×134×59.4mm」取到的是 **59.4**（厚度），
        不是 336.66（长度）—— 结果「420mm 显卡进 400mm 机箱」被判成没问题。

    正确做法：尺寸是「长×宽×厚mm」格式，
      ① 先按 × 拆开，取第一个数（长度）
      ② 没有 × 的，才取紧邻 mm 的那个数
    """
    t = text or ""
    m = re.search(r"(\d{2,4}(?:\.\d+)?)\s*[×xX*]\s*(\d{2,4}(?:\.\d+)?)"
                  r"(?:\s*[×xX*]\s*(\d{2,4}(?:\.\d+)?))?\s*mm", t)
    if m:
        try:
            return float(m.group(1))   # 第一个数 = 长度
        except ValueError:
            return None
    # 单个尺寸：「400mm」
    m = re.search(r"(\d{2,4}(?:\.\d+)?)\s*mm", t)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def parse_all_mm(text):
    """把「125*71*148mm」拆成 [125, 71, 148]（全部分量）。

    🔴 为什么需要单独一个函数：
        散热器的三个分量含义和显卡不同：
          显卡 336×134×59.4mm  → 长 × 宽 × 厚   （取第一个）
          散热器 125×71×148mm  → 宽 × 深 × **高**（取第三个！）

        风冷限高校验必须用高度（第三个），不是第一个。
        实测：利民TL-C12 是 125×71×148mm，148mm 才是要跟机箱
        「CPU散热器限高 160mm」比的那个数。
    """
    t = text or ""
    m = re.search(r"(\d{2,4}(?:\.\d+)?)\s*[×xX*]\s*(\d{2,4}(?:\.\d+)?)"
                  r"(?:\s*[×xX*]\s*(\d{2,4}(?:\.\d+)?))?\s*mm", t)
    if not m:
        v = parse_mm(t)
        return [v] if v else []
    out = []
    for g in m.groups():
        if g:
            try:
                out.append(float(g))
            except ValueError:
                pass
    return out


def cooler_height(spec):
    """风冷散热器高度（mm）= 产品尺寸的第三个分量。"""
    parts = parse_all_mm(spec)
    if len(parts) >= 3:
        return parts[2]
    return None


def gpu_length(name_or_spec):
    """显卡长度（mm）。从「产品尺寸 336.66×134×59.4mm」取第一个数。"""
    return parse_mm(name_or_spec)


def case_gpu_limit(spec):
    """机箱显卡限长（mm）。

    🔴 2026-11-26 修的 bug：
        参数拼成字符串后是「显卡限长330mm；CPU散热器限高177mm；…」，
        直接调 parse_mm 会取到**第一个** mm 数字（330），
        但那是显卡限长，不是散热器限高 —— 导致风冷限高校验永远用错值。

        ⚠️ 第二个 bug：有些机箱参数里**没有「显卡限长」这一栏**
        （实测乔思伯X400 只有「CPU散热器限高176mm」），
        原来的兜底 `parse_mm(s)` 会把散热器限高 176 当成显卡限长 ——
        于是"显卡 300mm 超过机箱限长 176mm"这种假警报。
        修法：**兜底时排除掉散热器限高那个数**，取不到就返回 None。
    """
    s = spec or ""
    m = re.search(r"显卡限长[^0-9]{0,6}(\d{2,4}(?:\.\d+)?)\s*mm", s)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None

    # 兜底：没有「显卡限长」字段时，不能用 parse_mm（会拿到散热器限高）
    # 明确返回 None，让调用方知道"这个机箱没这个数据"
    return None


def case_cooler_height(spec):
    """机箱 CPU 散热器限高（mm）。

    同 case_gpu_limit：必须按字段名「CPU散热器限高」定位，
    不能取第一个 mm 数字。
    """
    s = spec or ""
    for pat in (r"CPU散热器限高[^0-9]{0,6}(\d{2,4}(?:\.\d+)?)\s*mm",
                r"散热器限高[^0-9]{0,6}(\d{2,4}(?:\.\d+)?)\s*mm"):
        m = re.search(pat, s, re.I)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def case_liquid_support(spec):
    """机箱支持的水冷排尺寸集合，如 {240, 360}。

    🔴 2026-11-26 修的 bug：ZOL 的写法有四五种，原来只认两种：

        「顶置：240/280/360mm水冷排」   ← 数字在前，mm 在中，水冷在后
        「360冷排，280冷排，240冷排」    ← 数字在前，冷排在后（没 mm！）
        「支持360水冷」                 ← 支持 + 数字 + 水冷
        「顶置：360mm水冷排」            ← 顶置 + 数字 + mm + 水冷

    原来的正则 `(\\d{3})\\s*mm\\s*水冷` 只认第一种，
    结果「骨伽乘风MX600」的「360冷排，280冷排」解析成空集 ——
    于是任何水冷都被判"机箱没写支持尺寸"，筛选失效。

    改成：**只要 120-480 之间的三位数，后面跟「mm水冷/冷排/水冷」就算**。
    """
    out = set()
    s = spec or ""
    # 三种写法一起覆盖：数字 + 可选mm + (水冷|冷排)
    for m in re.finditer(r"(\d{3})\s*(?:mm)?\s*(?:水冷|冷排)", s):
        v = int(m.group(1))
        if 120 <= v <= 480:
            out.add(v)
    return out


def cooler_radiator(name, spec=None):
    """散热器冷排尺寸（mm）。

    🔴 2026-11-26 两处修：

    ① **判类型不能靠名字**。
       实测「瓦尔基里B360高性能风扇」名字里没有「水冷」二字，
       但它参数里写着 `散热方式 = 水冷`。靠名字判断会把它当风冷，
       然后拿它的「产品尺寸」去跟机箱的「风冷限高」比 —— 逻辑错了。

       所以优先读参数里的 `散热方式` 字段：
         含「水冷」→ 是水冷
         只有「风冷」→ 是风冷

    ② 冷排尺寸从名字的 360/240 抠，参数里没有专门的冷排字段。
    """
    # ① 先看参数里的散热方式（最靠谱）
    is_liquid = None
    if spec is not None:
        if "水冷" in (spec or ""):
            is_liquid = True
        elif "风冷" in (spec or ""):
            is_liquid = False

    # ② 参数没有时退回看名字
    if is_liquid is None:
        n = (name or "").upper()
        is_liquid = any(k in n for k in ("水冷", "AIO", "冷排", "一体式"))

    if not is_liquid:
        return None

    # ③ 冷排尺寸：名字里的 240/360
    n = (name or "").upper()
    for m in re.finditer(r"(\d{3})", n):
        v = int(m.group(1))
        if v in (120, 240, 280, 360, 420, 480):
            return v
    return None


def is_liquid_cooler(name, spec=None):
    """散热器是不是水冷。参数优先，名字兜底。"""
    if spec:
        if "水冷" in spec:
            return True
        if "风冷" in spec:
            return False
    n = (name or "").upper()
    return any(k in n for k in ("水冷", "AIO", "冷排", "一体式"))


def case_board_types(spec):
    """机箱支持的板型集合，如 {"MATX", "ITX"}。"""
    out = set()
    s = (spec or "").upper().replace("-", "").replace(" ", "")
    for m in re.finditer(r"(EATX|MATX|M?ATX|ITX|MINIITX|MICROATX)", s):
        t = m.group(1)
        if t in ("EATX",):
            out.add("EATX")
        elif t in ("MATX", "MICROATX"):
            out.add("MATX")
        elif t in ("ITX", "MINIITX"):
            out.add("ITX")
        elif t == "ATX":
            out.add("ATX")
    return out


# 板型 → 从大到小的容纳关系
# ATX 机箱能装 MATX 和 ITX；MATX 机箱装不了 ATX
BOARD_FITS = {
    "EATX": {"EATX", "ATX", "MATX", "ITX"},
    "ATX": {"ATX", "MATX", "ITX"},
    "MATX": {"MATX", "ITX"},
    "ITX": {"ITX"},
}


def board_type_of(name):
    """主板板型。从型号名推断（M 结尾/带 M 的多是 MATX）。"""
    n = (name or "").upper().replace("-", "").replace(" ", "")
    # 明确写了 ITX
    if "ITX" in n:
        return "ITX"
    # 带 M 的（B760M / B650M）通常是 MATX
    if re.search(r"[BXHZA]\d{3}M", n):
        return "MATX"
    # 没带 M 的（B760 / Z790）是 ATX
    if re.search(r"[BXHZA]\d{3}", n):
        return "ATX"
    return None


def check(gpu_spec, case_spec, cooler_name=None, board_name=None,
          cooler_spec=None, gpu_psu=None):
    """检查一套配置的物理兼容性。

    gpu_spec    显卡「产品尺寸」字符串，如 "336.66×134×59.4mm"
    case_spec   机箱参数拼成的字符串（显卡限长/限高/适用主板/支持水冷）
    cooler_name 散热器名（用来识别水冷/风冷 + 抠冷排尺寸）
    board_name  主板名（用来识别板型）
    cooler_spec 散热器「产品尺寸」，如 "125*71*148mm"（风冷高度用第三个分量）
    gpu_psu     显卡「建议电源」，如 "400W"

    返回 [(level, 说明)]
      level: "hard" = 装不上；"soft" = 需确认
    """
    out = []

    # ① 显卡长度
    gl = gpu_length(gpu_spec)
    cl = case_gpu_limit(case_spec)
    if gl and cl:
        # 留 10mm 给供电线弯折
        if gl > cl - 10:
            out.append(("hard",
                        "显卡 %.0fmm 超过机箱限长 %.0fmm，装不进去"
                        % (gl, cl)))
        elif gl > cl - 30:
            out.append(("soft",
                        "显卡 %.0fmm 接近机箱限长 %.0fmm，"
                        "供电线可能顶到，建议确认" % (gl, cl)))

    # ② 散热器
    if cooler_name:
        # 判类型：参数里的「散热方式」优先，名字兜底（见 cooler_radiator 注释）
        _liquid = is_liquid_cooler(cooler_name, cooler_spec)
        rad = cooler_radiator(cooler_name, cooler_spec) if _liquid else None
        if _liquid and rad:
            # 水冷：验冷排尺寸
            sup = case_liquid_support(case_spec)
            if sup and rad not in sup:
                out.append(("hard",
                            "水冷冷排 %dmm，机箱只支持 %s"
                            % (rad, "/".join(str(x) for x in sorted(sup)))))
            elif not sup:
                out.append(("soft",
                            "机箱参数里没写支持的水冷尺寸，"
                            "%dmm 冷排需确认能装" % rad))
        elif _liquid and not rad:
            out.append(("soft", "水冷散热器，但没识别出冷排尺寸，需人工确认"))
        else:
            # 风冷：验高度（第三个分量）
            h = case_cooler_height(case_spec)
            ch = cooler_height(cooler_spec) if cooler_spec else None
            if h and ch:
                if ch > h:
                    out.append(("hard",
                                "风冷高 %.0fmm 超过机箱限高 %.0fmm，"
                                "侧板盖不上" % (ch, h)))
                elif ch > h - 5:
                    out.append(("soft",
                                "风冷高 %.0fmm 接近机箱限高 %.0fmm，"
                                "建议确认" % (ch, h)))
            elif h and not ch:
                out.append(("soft",
                            "机箱风冷限高 %.0fmm，"
                            "但散热器尺寸没抓到，需人工确认" % h))

    # ③ 主板板型
    bt = board_type_of(board_name)
    if bt:
        fits = case_board_types(case_spec)
        if fits and bt not in fits:
            out.append(("hard",
                        "主板是 %s 板型，机箱只支持 %s"
                        % (bt, "/".join(sorted(fits)))))

    return out


if __name__ == "__main__":
    print("=" * 76)
    print("机箱/显卡/散热器 物理兼容性测试")
    print("=" * 76)

    CASE = ("显卡限长400mm；CPU散热器限高160mm；"
            "适用主板MATX板型，ITX板型；支持水冷顶置：360mm水冷排")

    CASES = [
        ("显卡 336mm 进 400mm 机箱", "336.66×134×59.4mm", CASE, None, None),
        ("显卡 420mm 进 400mm 机箱", "420×140×70mm", CASE, None, None),
        ("360 水冷进支持 360 的机箱", "300×130×50mm", CASE,
         "瓦尔基里B360水冷", None),
        ("MATX 主板进 MATX 机箱", "300×130×50mm", CASE, None,
         "技嘉B760M GAMING"),
        ("ATX 主板进 MATX 机箱", "300×130×50mm", CASE, None,
         "华硕ROG STRIX Z790-A"),
    ]
    for desc, gpu, case, cooler, board in CASES:
        res = check(gpu, case, cooler, board)
        print()
        print("  %s" % desc)
        if not res:
            print("    ✓ 没问题")
        for lv, msg in res:
            mark = "✗" if lv == "hard" else "⚠"
            print("    %s %s" % (mark, msg))
