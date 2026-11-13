#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pick.py —— 输入专业，输出该买哪台

这是整个项目最初要解决的问题。前面所有的数据整理（能效备案、字段矩阵、陷阱库、
跨源校验）都是为了这一步能给出一个敢签字的答案。

用法：
    python tools/pick.py 计算机科学与技术
    python tools/pick.py 计科
    python tools/pick.py 动画 --budget 8000
    python tools/pick.py 土木 --budget 6000 --all

它回答四件事，顺序不能反：
  1. 你这个专业到底卡在哪几项硬件上（门槛 + 依据，不是拍脑袋）
  2. 库里哪几台真的够用 —— 分「可放心买」「能买但要知道风险」两档
  3. 哪几台明确不够用 —— 这是结论，不是"不知道"。要写清为什么不够
  4. 还差什么数据 —— 数据不足的机器逐台点名，说明缺哪一项

刻意不做的事：
  - 不猜价格。没有采集到价格就不报价，写"价格未采"。
  - 不做模糊匹配。找不到就报找不到，并给最接近的候选。
  - 不把「不满足」说成「待确认」。核显机达不到显存要求，这就是结论。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_judge import (  # noqa: E402
    VERDICT_CN, field_value, form_is_soldered, is_integrated, judge,
    r_fields_of, subsidy_of,
)
from major_lookup import dwidth, mlist, resolve  # noqa: E402
import lib_bench as LB  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

GPU_WORD = {
    "none": "不要独显（核显足够）",
    "either": "核显或独显都行",
    "discrete": "必须是独立显卡",
    "certified": "需认证显卡 + 认证驱动",
}
CAT_WORD = {"thin": "轻薄本", "gaming": "游戏本", "desktop": "台式整机",
            "upgrade": "升级配件", "phone": "手机"}
MODE_WORD = {"value": "省钱优先", "perf": "性能优先",
             "future": "能加装优先", "carry": "要带出门"}
FIELD_WORD = {
    "cpu": "CPU 型号", "cores": "CPU 核心数", "ramGB": "内存容量",
    "ramType": "内存形态/类型", "storageGB": "存储容量", "gpuModel": "显卡型号",
    "vramGB": "显存容量", "tgpW": "显卡功耗释放", "screenInch": "屏幕尺寸",
    "resolution": "分辨率", "refreshHz": "刷新率", "colorGamut": "色域",
    "brightnessNit": "屏幕亮度", "weightKg": "重量", "batteryWh": "电池容量",
    "ports": "接口", "efficiency": "能效等级", "throttle": "持续性能衰减",
    # 身份冲突里会出现「字段（说明）」的写法，不映射就会把原文甩给用户
    "modelHint（年份）": "型号年份", "modelHint": "型号命名",
    "certModel": "认证型号", "energyModel": "能效备案型号",
    # 软冲突里会出现嵌套字段，取末段来查
    "form": "内存形态", "type": "内存类型", "panel": "屏幕面板", "model": "型号",
}


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def pad(s, n):
    """按显示宽度右补空格。中文算两格。"""
    return s + " " * max(0, n - dwidth(s))


def money(v):
    if v is None:
        return "—"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def spec_line(m):
    """一句话说清配置。只写有的，不填空话。"""
    bits = []
    cpu = m.get("cpu") if isinstance(m.get("cpu"), dict) else {}
    if cpu.get("model"):
        bits.append(str(cpu["model"]))
    ram = m.get("ram") if isinstance(m.get("ram"), dict) else {}
    if ram.get("gb"):
        bits.append(f"{ram['gb']}G")
    if m.get("storageGB"):
        g = m["storageGB"]
        bits.append(f"{g // 1024}T" if g >= 1024 else f"{g}G")
    gpu = m.get("gpu") if isinstance(m.get("gpu"), dict) else {}
    if gpu.get("model"):
        g = str(gpu["model"])
        if gpu.get("vramGB"):
            g += f" {gpu['vramGB']}G"
        bits.append(g)
    return " · ".join(bits) or "配置未采"


def warn_text(m):
    """这台机器固有的风险提示。来自数据本身，不是套话。"""
    out = []
    if form_is_soldered(m) is True:
        out.append("内存焊死")
    elif form_is_soldered(m) is None:
        out.append("内存可否加装未核实")
    if m.get("confidence") == "unverified":
        out.append("规格未经交叉核实")
    # 软冲突：同一台机器在不同来源的标注不一致。价格照挂，但这一项结论要打折。
    if m.get("softConflicts"):
        names = "、".join(FIELD_WORD.get(f.split(".")[-1], f) for f in m["softConflicts"])
        out.append(f"{names}的官方标注有出入")
    if m.get("efficiency") is None and m.get("efficiencyStatus") == "conflict":
        # 已经查到了，只是两条备案互相矛盾 —— 不能说成"未查"，那会让人以为加把劲就能查清
        out.append("能效备案冲突，国补存疑")
    elif m.get("efficiency") is None:
        out.append("能效等级未查，国补资格未知")
    elif m.get("efficiency") != 1:
        out.append(f"{m['efficiency']} 级能效，国补家电线要求 1 级")
    return out


def value_metric(gid, r_fields):
    """这一组该用哪个跑分维度算「每元性能」。

    为什么按组的 R 字段来选，而不是统一用多核：
      视频/三维/游戏看显卡，理工看 CPU 多核，办公/音频看单核。
      用错维度会把「显卡强但 CPU 弱」的机器判成不划算，反过来也一样。

    两个特判：
      · code（编程/数据）R 字段里只有 ramGB/ramType，没有 cores，
        但编译是高度并行的，多核比单核更能说明"编译等多久"。
      · audio（音乐音频）虽然也只有 storageGB/ports，
        但实时音频链看的是单核延迟，保持单核。
    """
    if any(f in r_fields for f in ("gpuModel", "vramGB", "tgpW")):
        return "timeSpy", "每元显卡分"
    if "cores" in r_fields or gid == "code":
        return "cbMulti", "每元多核分"
    return "cbSingle", "每元单核分"


def value_pick(priced, gid, r_fields):
    """在同组『够用』的机器里找每元性能最高的那台。

    只在这一组内部比 —— 这些机器都已经过了门槛，
    比出来的才是「同样都能干活的情况下，哪台钱花得最值」。
    没有跑分数据的机器不参与比较，也不能因此被判成「不划算」。
    """
    key, label = value_metric(gid, r_fields)
    rows = []
    for m, _w in priced:
        pr = m.get("price") or {}
        price = pr.get("final") or pr.get("net")
        if not price:
            continue
        b = _bench_for(m, key)
        if not b:
            continue
        rows.append((b / price, m, b, price))
    if len(rows) < 2:
        # 只有一台够用，比"每元"没有意义
        return None
    rows.sort(key=lambda t: -t[0])
    best = rows[0]
    worst = rows[-1]
    return {"row": best, "label": label, "n": len(rows),
            "ratio": best[0], "runner": rows[1] if len(rows) > 1 else None,
            "spread": (best[0] / worst[0]) if worst[0] else None}


def _bench_for(m, key):
    cpu = (m.get("cpu") or {}).get("model")
    gpu = (m.get("gpu") or {}).get("model")
    if key == "timeSpy":
        g = LB.gpu_bench(gpu)
        return ((g or {}).get("bench") or {}).get("timeSpyGraphics")
    c = LB.cpu_bench(cpu)
    r23 = ((c or {}).get("bench") or {}).get("r23") or {}
    return r23.get({"cbMulti": "multi", "cbSingle": "single"}[key])


def pick_one(docs, major, level, group, how, budget, show_all):
    pool = docs["pool"]
    fmat = docs["fmat"]
    rules = docs["rules"]
    gid = group["id"]
    r_fields = r_fields_of(fmat["matrix"], gid)
    req = group.get("req") or {}
    gates = group.get("hardGates") or []

    print()
    print(f"专业   {major}（{'本科' if level == '本' else '专科'}）")
    print(f"分组   {group['name']} — {group.get('tagline', '')}")
    if how == "alias":
        print(f"       （按别名匹配到「{major}」）")
    elif how == "fuzzy":
        print(f"       （按包含关系匹配，如果不是请用全名）")
    print("─" * 72)

    # ---------- 1. 门槛，先说清楚 ----------
    print()
    print("这个专业卡在哪儿")
    print(f"  内存   最低 {req.get('ramMin')}G / 建议 {req.get('ramRec')}G")
    print(f"  显卡   {GPU_WORD.get(req.get('gpu'), req.get('gpu'))}")
    if req.get("vramMin"):
        print(f"  显存   最低 {req['vramMin']}G / 建议 {req.get('vramRec')}G")
    print(f"  存储   {req.get('storageMinGB')}G 起")
    print(f"  CPU    {req.get('cpu')}")
    if req.get("rationale"):
        for line in wrap(req["rationale"], 60):
            print("         " + line if line is not wrap(req["rationale"], 60)[0] else "  依据   " + line)
    if gates:
        print("  硬门槛（不满足就不该买）")
        for h in gates:
            dim = {"ram": "内存", "gpu": "显卡", "storage": "存储", "upgrade": "可加装"}.get(h["dim"], h["dim"])
            print(f"         · {dim} ≥ {h['min']}：{h['why']}")

    # ---------- 2. 逐台判定 ----------
    buckets = {"OK": [], "DEGRADED": [], "VIOLATE": [], "UNKNOWN": []}
    for m in pool:
        verdict, why, missing = judge(m, req, r_fields, gates)
        m["_verdict"] = verdict  # 记在机器上，后面展示时不必再回头查
        buckets[verdict].append((m, why, missing))

    # 有价格优先；同档内按到手价升序，无价格的排最后
    def sort_key(t):
        m = t[0]
        pr = (m.get("price") or {})
        final = pr.get("final") or pr.get("net")
        return (final is None, final or 0)

    for k in buckets:
        buckets[k].sort(key=sort_key)

    # ---------- 2.5 一句话结论 ----------
    # 放在最前面。用户最先要知道的是"有没有得买"，不是逐台细节。
    good0 = buckets["OK"] + buckets["DEGRADED"]
    priced0 = [(m, w) for m, w, _ in good0
               if (m.get("price") or {}).get("net") or (m.get("price") or {}).get("final")]
    print()
    print("一句话结论")
    if not good0:
        print(f"  库里 {len(pool)} 台没有一台满足这一组的门槛。")
        print(f"  这不是数据缺失 —— 是不满足（下面逐台写了原因）。")
    else:
        cheapest = min(priced0, key=lambda t: (t[0]["price"].get("final")
                                               or t[0]["price"].get("net"))) if priced0 else None
        if cheapest:
            m = cheapest[0]
            pr = m["price"]
            n_ok = len(buckets["OK"])
            n_deg = len(buckets["DEGRADED"])
            quality = "完全够用" if n_ok else "都带保留"
            print(f"  能买，最便宜 {money(pr.get('final') or pr.get('net'))} 元 —— "
                  f"{m['name']}")
            print(f"  够用的 {len(good0)} 台（{quality}），其中 {len(priced0)} 台有价格。")
            if budget:
                within = [t for t in priced0
                          if (t[0]["price"].get("final") or t[0]["price"].get("net")) <= budget]
                print(f"  你的预算 {money(budget)} 元内，可选 {len(within)} 台。")

    # 每元性能：回答"都能用的话，哪台钱花得最值"。
    # 和"最便宜"是两个问题 —— 用户常常把两者混为一谈。
    vp = value_pick(priced0, gid, r_fields)
    if vp:
        m, b, price = vp["row"][1], vp["row"][2], vp["row"][3]
        m["_bestValue"] = vp["label"]
        print(f"  {vp['label']}最高 {money(price)} 元 —— {m['name']}"
              f"（{b:,} 分 / {vp['n']} 台可比）")
        if vp.get("spread") and vp["spread"] > 1.15:
            # 注意措辞：这里是"每元性能"，不是"绝对性能"。
            # 写成"多拿 83% 性能"会让人以为机器本身强 83%，是错的。
            print(f"  换算成同样的钱，它的{vp['label'].replace('每元', '')}比这组最差的一台"
                  f"高 {round((vp['spread'] - 1) * 100)}%。")
        else:
            print(f"  够用 {len(good0)} 台，但一台都没采到价格 —— 只能确认「够不够用」，"
                  "还不能告诉你要花多少钱。")

    # ---------- 3. 能买的 ----------
    good = buckets["OK"] + buckets["DEGRADED"]
    # 统一成 (机器, 保留说明) 二元组，后面展示只用这两项
    priced = [(m, w) for m, w, _miss in good
              if (m.get("price") or {}).get("net") or (m.get("price") or {}).get("final")]

    print()
    print(f"能买的（{len(good)} 台，其中 {len(priced)} 台价格已知）")
    print()
    if not good:
        print("  一台都没有。这不是数据缺失，是库里现有的机器都不满足这一组的门槛。")
        print("  下面「不够用的」那段写了每台为什么不够 —— 照那个清单去筛，比重新找更省时间。")
    else:
        # 预算筛选：把「够用但没有价格」的单独留住 —— 它们不该因为没价格就被藏起来
        noprice = [(m, w) for m, w, _miss in good
                   if not ((m.get("price") or {}).get("net") or (m.get("price") or {}).get("final"))]
        over = []
        if budget:
            # 先只保留有价格的，再比预算。无价格的机器没有可比对象，
            # 直接比较会得到 None > int 的 TypeError。
            over = [(m, w) for m, w in priced
                    if (m["price"].get("final") or m["price"].get("net")) > budget]
            priced = [(m, w) for m, w in priced
                      if (m["price"].get("final") or m["price"].get("net")) <= budget]

        if budget and not priced:
            print(f"  预算 {money(budget)} 元内没有够用的机器。")
            if over:
                over.sort(key=lambda t: (t[0]["price"].get("final") or t[0]["price"].get("net")))
                cheapest = over[0][0]["price"]
                cheapest = cheapest.get("final") or cheapest.get("net")
                print(f"  够用的里面最便宜是 {money(cheapest)} 元，差 {money(cheapest - budget)} 元。")
            print()
        elif not priced and not noprice:
            print("  库里没有这一组够用且采到价格的机器。")

        if priced:
            show = priced if show_all else priced[:8]
            for m, w in show:
                print_machine(m, w, m.get("_verdict", "OK"))
            if len(priced) > len(show):
                print(f"  …… 还有 {len(priced) - len(show)} 台，加 --all 全看")
                print()

        if over:
            over.sort(key=lambda t: (t[0]["price"].get("final") or t[0]["price"].get("net")))
            print(f"  超预算但确实够用（{len(over)} 台，按价格升序）：")
            for m, _w in over[:5]:
                pr = m["price"]
                print(f"    {money(pr.get('final') or pr.get('net')):>10} 元  {m['name']}  [{spec_line(m)}]")
            if len(over) > 5:
                print(f"    …… 还有 {len(over) - 5} 台，加 --all 全看")
            print()

        if noprice:
            print(f"  另有 {len(noprice)} 台够用但没采到价格，进不了比较：")
            for m, _w in noprice:
                print(f"    {m['name']}  [{spec_line(m)}]")
            print()

    # ---------- 4. 不够用的 ----------
    viol = buckets["VIOLATE"]
    if viol:
        print()
        print(f"明确不够用的（{len(viol)} 台）—— 这是结论，不是「不知道」")
        # 分两档：只差一项 vs 差多项。后者说了也没用，用户不会为此改配置。
        near = [(m, why) for m, why, _ in viol if len(why) == 1]
        far = [(m, why) for m, why, _ in viol if len(why) > 1]
        if near:
            print()
            # 措辞很重要：「只差一项」不等于「买回来能补上」。
            # 笔记本的显卡换不了，内存也可能焊死 —— 这是物理事实，不是懒。
            fixable = {"code-ram": "内存或许能加装", "code-ssd": "存储可以换"}
            print(f"  只差一项的（{len(near)} 台）")
            show_n = near if show_all else near[:6]
            for m, why in show_n:
                pr = m.get("price") or {}
                p = money(pr.get("final") or pr.get("net"))
                gap = why[0]
                if "不是同一台" in gap or "无法推荐" in gap:
                    note = "这条数据要修，不是机器的问题"
                elif "核显" in gap:
                    note = "显卡换不了，这台补不上"
                elif "焊死" in gap:
                    note = "内存焊死，补不上"
                else:
                    note = "可能可以补"
                print(f"    {p:>10} 元  {m['name']}")
                print(f"                  {spec_line(m)}")
                print(f"                  差在  {gap}   →  {note}")
            if len(near) > len(show_n):
                print(f"    …… 还有 {len(near) - len(show_n)} 台，加 --all 全看")
        if far:
            print()
            print(f"  差两项以上，不用考虑（{len(far)} 台）")
            show_f = far if show_all else far[:3]
            for m, why in show_f:
                pr = m.get("price") or {}
                p = money(pr.get("final") or pr.get("net"))
                print(f"    {p:>10} 元  {m['name']}   {'；'.join(why)}")
            if len(far) > len(show_f):
                print(f"    …… 还有 {len(far) - len(show_f)} 台，加 --all 全看")

    # ---------- 5. 数据不足 ----------
    unk = buckets["UNKNOWN"]
    if unk:
        print()
        print(f"数据不足，判不了的（{len(unk)} 台）—— 这是真缺口，补上就能判")
        miss_count = {}
        for m, why, missing in unk:
            for f in missing:
                miss_count[f] = miss_count.get(f, 0) + 1
        top = sorted(miss_count.items(), key=lambda x: -x[1])
        print("  卡点  " + "，".join(f"{FIELD_WORD.get(k, k)}×{v}" for k, v in top))
        print()
        show_u = unk if show_all else unk[:5]
        for m, why, missing in show_u:
            pr = m.get("price") or {}
            p = money(pr.get("final") or pr.get("net"))
            names = "、".join(FIELD_WORD.get(f, f) for f in missing)
            print(f"  {p:>10} 元  {m['name']}   缺 {names}")
        if len(unk) > len(show_u):
            print(f"  …… 还有 {len(unk) - len(show_u)} 台，加 --all 全看")

    # ---------- 6. 这个专业最容易踩的坑 ----------
    print()
    print("最容易踩的坑")
    for line in wrap(group.get("trap", "—"), 62):
        print("  " + line)

    fus = group.get("followups") or []
    if fus:
        print()
        print("还要问自己一句")
        for q in fus:
            print("  · " + q)

    # ---------- 7. 结论上限，说清可信度 ----------
    print()
    print("─" * 72)
    n_with = sum(1 for m in pool if (m.get("price") or {}).get("net") or (m.get("price") or {}).get("final"))
    n_ok = len(good)
    print(f"这次判断基于库里 {len(pool)} 台机型（{n_with} 台有价格），"
          f"本组够用 {n_ok} 台。")
    print("库里没有的机型不代表不行 —— 它只是还没被采进来。")
    print("─" * 72)


def print_machine(m, warnings, verdict):
    """打印一台可推荐的机器。信息顺序：先价格（用户最关心），再配置，再限制。"""
    pr = m.get("price") or {}
    net = pr.get("net")
    final = pr.get("final") or net
    sub = pr.get("subsidy")

    tag = "" if verdict == "OK" else "   ⚠ 有保留"
    # 每元性能最高的一台单独标出来，省得用户自己在多台之间算比值
    if m.get("_bestValue"):
        tag += f"   ★ {m['_bestValue']}最高"
    print(f"  {money(final)} 元   {m['name']}{tag}")
    print(f"                 {spec_line(m)}")

    # 性能跑分：用户最常问"这台比那台快多少"。
    # 只报有出处的数，没查到的写"未查"，不拿估算值凑。
    perf = bench_line(m)
    if perf:
        print(f"                 性能参考  {perf}")

    # 价格拆开写 —— 只报"到手价"会让用户以为原价就是这个数，
    # 也无法判断补贴是真降还是先涨后补。
    ppt = f"划线 {money(pr.get('list'))}"
    if pr.get("discounts"):
        ppt += f" → 券后 {money(net)}"
    ppt += f" → 国补 {money(sub)} → 到手 {money(final)}"
    print(f"                 {ppt}")

    edu = m.get("eduPrice") or {}
    if edu.get("final"):
        # 教育优惠是 Mac 相对 Windows 的一个真实优势（差 1500 左右），
        # 但需要学生认证，所以单独一行给出，不混进到手价
        print(f"                 学生价    {money(edu['final'])} 元"
              f"（教育优惠，需资质认证，比上面低 {money(final - edu['final'])}）")

    cap = m.get("priceCapturedAt")
    if cap:
        print(f"                 采价 {cap[:10]}  ·  {shop_label(m.get('shopType'))}")

    # 国补资格单独说清：页面写"补贴 1500"不等于你能拿到，家电线要求 1 级能效
    if m.get("efficiency") == 1:
        print("                 国补资格  ✔ 1 级能效备案，可走家电线 15%、封顶 1500")
    elif m.get("efficiency"):
        print(f"                 国补资格  ✘ {m['efficiency']} 级能效，家电线要求 1 级 —— 页面上的补贴价拿不到")
    elif (m.get("devicePlatform") or "").lower() == "macos":
        # Mac 的能效网备案查不到，但京东页面明确标可享国补 —— 两个事实都要说清，
        # 只写"能效未查"会让人以为补贴根本没戏
        print("                 国补资格  ? 京东标可享国补（1500），但未查到能效网备案，"
              "以结算页为准")
    elif m.get("efficiencyStatus") == "conflict":
        # 「已查到但两条备案矛盾」和「没查到」是两件事，不能都说成"未查"：
        # 前者意味着页面上的补贴价很可能拿不到，用户该去问客服；后者只是我们不知道。
        print("                 国补资格  ? 能效备案冲突：同一系列查到 2 级与 1 级两条，"
              "到手价里的补贴可能不成立 —— 下单前问客服要本机备案号")
    else:
        print("                 国补资格  ? 能效等级未查，到手价里的补贴不一定成立")

    risks = warn_text(m)
    if risks:
        print("                 要留意    " + "；".join(risks))
    for w in warnings:
        print("                 保留      " + w)
    print()


def bench_line(m):
    """性能参考一行：Cinebench R23 + 3DMark TimeSpy。

    为什么要单独一行而不是塞进 spec_line：
      规格回答"够不够"，跑分回答"快多少"。学生比价时真正想知道的是
      第二个 —— 同样是 32G+RTX5050，i7-13620H 和 i5-14450HX 差多少。
    没有出处的数字一律不写，宁可这行空着。
    """
    cpu = (m.get("cpu") or {}).get("model")
    gpu = (m.get("gpu") or {}).get("model")
    parts = []
    cl = LB.cpu_line(cpu)
    if cl:
        parts.append(cl)
    gl = LB.gpu_line(gpu)
    if gl:
        parts.append(gl)
    if not parts:
        return None
    return "  ·  ".join(parts)


def shop_label(t):
    return {"self": "京东自营", "flagship": "品牌旗舰店",
            "authorized": "授权店", "thirdparty": "第三方店"}.get(t or "", "店铺类型未记")


def wrap(text, n):
    """按显示宽度折行，不拆开英文/数字词。"""
    tokens, buf = [], ""
    for ch in str(text):
        if ord(ch) > 127:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    out, line, w = [], "", 0
    for t in tokens:
        tw = dwidth(t)
        if w + tw > n and line:
            out.append(line)
            line, w = "", 0
        line += t
        w += tw
    if line:
        out.append(line)
    return out or [""]


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("major", nargs="?", help="专业名称或别名")
    ap.add_argument("--budget", type=int, default=None, help="预算（元），按到手价算")
    ap.add_argument("--all", action="store_true", help="列出全部，不截断")
    ap.add_argument("-h", "--help", action="store_true")
    args = ap.parse_args()

    if args.help or not args.major:
        print(__doc__.strip())
        return 0 if args.help else 1

    majors = load("majors.json")
    docs = {
        "pool": load("machine_pool.json")["machines"],
        "fmat": load("field_matrix.json"),
        "rules": load("prices.json").get("subsidyRules") or {},
    }

    major, level, group, how = resolve(majors, args.major)
    if not group:
        print(f"认不出「{args.major}」。")
        if isinstance(how, list) and how:
            print("你是说：" + " / ".join(how))
        else:
            total = sum(len(mlist(g)) for g in majors["groups"])
            print(f"目前覆盖 {total} 个专业。运行 python tools/pick.py --help 看用法，")
            print("或 python tools/major_lookup.py 看全部专业清单。")
        return 1

    pick_one(docs, major, level, group, how, args.budget, args.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())