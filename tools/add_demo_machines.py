# -*- coding: utf-8 -*-
"""
add_demo_machines.py — 演示前机型池扩充（2026-11-11 晚）
来源：WebSearch 搜索快照（京东 union 价格标签 + 太平洋/官网规格页），非页面直抓。
⚠️ 全部 5 台 confidence=low、priceSource=PENDING-VERIFY，演示用，后续需逐台回页面核对。
用法：python tools/add_demo_machines.py --apply
"""
import json, shutil, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POOL = ROOT / "data" / "machine_pool.json"

def base(id_, name, brand, cat):
    return {
        "id": id_, "name": name, "brand": brand, "cat": cat,
        "cpu": {}, "ram": {}, "storageGB": None, "gpu": {}, "screen": {},
        "batteryWh": None, "weightKg": None, "ports": [],
        "efficiency": None, "certModel": None, "energyModel": None,
        "efficiencyStatus": "missing", "throttle": None,
        "confidence": "low",
        "dataFrom": {
            "spec": "2026-11-11 晚 WebSearch 快照（演示扩充）",
            "price": "2026-11-11 WebSearch 快照 PENDING-VERIFY"
        },
        "price": None, "priceCapturedAt": "2026-11-11T21:50:00+08:00",
        "shopType": None, "shopName": None,
        "dataGaps": [], "cpuId": None,
        "specSource": None, "priceSource": "2026-11-11 搜索快照 PENDING-VERIFY（未回页面截图核对）",
    }

M = []

# 1. 拯救者 Y9000X 2026（Ultra 9 290HX Plus / RTX5060 / 32G / 1T）
m = base("add-y9000x-2026", "拯救者 Y9000X 2026（Ultra9-290HX Plus/RTX5060/32G/1T）", "联想", "gaming")
m["cpu"] = {"model": "Ultra 9 290HX Plus", "vendor": "Intel", "cores": 24, "threads": 24,
            "baseW": None, "maxW": None, "coreSrc": "联想社区评测 24核/5.5GHz（与 Ultra9 290HX Plus 官方 24C24T 一致）"}
m["cpuId"] = "ultra-9-290hx-plus"
m["ram"] = {"gb": 32, "type": "DDR5-6400", "config": "16GB×2", "form": None, "slots": None, "maxGB": None}
m["storageGB"] = 1024
m["gpu"] = {"model": "RTX 5060", "vendor": "NVIDIA", "vramGB": 8, "type": "discrete",
            "tgpBaseW": None, "tgpMaxW": 115, "boost": "出厂超频"}
m["screen"] = {"sizeInch": 16, "widthPx": 2560, "heightPx": 1600, "refreshHz": 240, "panel": "OLED",
               "colorGamut": {"dciP3": 100}, "brightnessNit": 1100}
m["weightKg"] = 1.98
m["ports"] = ["USB-A ×2", "USB-C ×1", "雷电4 ×1"]
m["price"] = {"list": None, "discounts": [], "net": 15499, "subsidy": None, "final": 15499,
              "basisNote": "太平洋（联想官网上架稿）：RTX5060 版补贴到手价 15499。⚠️ 划线价未抓（反推 18234 非整数不采信），PENDING-VERIFY"}
m["shopType"] = "official"
m["shopName"] = "联想官网口径"
m["dataGaps"] = ["batteryWh", "ram.form", "efficiency", "throttle", "price.list", "页面截图核对"]
m["specSource"] = "太平洋官网上架稿 + 联想社区评测双源；1.98kg / 240Hz OLED HDR1000 / 170W 释放 / 140W PD"
M.append(m)

# 2. REDMI Book Pro 14 2026（Ultra5-338H / 32G / 1T）
m = base("add-redmibookpro14-2026", "REDMI Book Pro 14 2026（Ultra5-338H/32G/1T）", "小米", "thin")
m["cpu"] = {"model": "Ultra 5 338H", "vendor": "Intel", "cores": 12, "threads": 12,
            "baseW": None, "maxW": None, "coreSrc": "太平洋产品库 12核12线程"}
m["cpuId"] = "ultra-5-338h"
m["ram"] = {"gb": 32, "type": "LPDDR5X", "config": None, "form": "soldered", "slots": 0, "maxGB": 32}
m["storageGB"] = 1024
m["gpu"] = {"model": "Intel Arc B370", "vendor": "Intel", "vramGB": 0, "type": "integrated",
            "tgpBaseW": None, "tgpMaxW": None, "boost": None, "tgpNA": True}
m["screen"] = {"sizeInch": 14, "widthPx": 2880, "heightPx": 1800, "refreshHz": 120, "panel": "LCD",
               "colorGamut": {"srgb": 100}, "brightnessNit": 500}
m["batteryWh"] = 92
m["weightKg"] = 1.48
m["ports"] = ["USB-A ×1", "USB-C ×1", "雷电4 ×1", "HDMI 2.1"]
m["price"] = {"list": 8799, "discounts": [], "net": 7479.15, "subsidy": 1319.85, "final": 7479.15,
              "basisNote": "京东直读现价 7479.15（小米官方旗舰店，U5-338H/32+1T）；7479.15÷0.85=8799 整数 → list 为反推值。⚠️ 常卖价标签 8299 与反推 8799 不一致，PENDING-VERIFY 需回页面"}
m["shopType"] = "self"
m["shopName"] = "京东 小米官方旗舰店"
m["dataGaps"] = ["efficiency", "throttle", "页面截图核对"]
m["specSource"] = "京东直读（A1402）+ 太平洋产品库 + techmansion；92Wh/1.48kg/120Hz LCD 500nits"
M.append(m)

# 3. 荣耀 MagicBook Pro 16 2026（Ultra5-338H / 32G / 1T）
m = base("add-magicbookpro16-2026", "荣耀 MagicBook Pro 16 2026（Ultra5-338H/32G/1T）", "荣耀", "thin")
m["cpu"] = {"model": "Ultra 5 338H", "vendor": "Intel", "cores": 12, "threads": 12,
            "baseW": None, "maxW": None, "coreSrc": "太平洋产品库 12核12线程（头条表格写 16核 疑误）"}
m["cpuId"] = "ultra-5-338h"
m["ram"] = {"gb": 32, "type": "LPDDR5X-8533", "config": None, "form": "soldered", "slots": 0, "maxGB": 32}
m["storageGB"] = 1024
m["gpu"] = {"model": "Intel Arc B370", "vendor": "Intel", "vramGB": 0, "type": "integrated",
            "tgpBaseW": None, "tgpMaxW": None, "boost": None, "tgpNA": True}
m["screen"] = {"sizeInch": 16, "widthPx": 3072, "heightPx": 1920, "refreshHz": 120, "panel": "LCD",
               "colorGamut": {"srgb": 100}, "brightnessNit": 500}
m["batteryWh"] = 92
m["weightKg"] = 1.8
m["ports"] = ["USB-A ×2", "USB-C ×1", "雷电4 ×1", "HDMI 2.1"]
m["price"] = {"list": 8199, "discounts": [], "net": 6969.15, "subsidy": 1229.85, "final": 6969.15,
              "basisNote": "头条导购：338H 版京东国补后 6969；6969.15÷0.85=8199 整数 → list 反推。⚠️ 京东直读另一 338H SKU（星辰灰）标 9299/常卖9199，SKU 价差大，PENDING-VERIFY"}
m["shopType"] = "self"
m["shopName"] = "京东自营（多店）"
m["dataGaps"] = ["efficiency", "throttle", "页面截图核对", "screen 120/165Hz 两 SKU 存差异"]
m["specSource"] = "京东直读（XWC-P）+ 头条导购 + ZOL；92Wh/16寸 3K/165Hz(X7版)/120Hz(338H版)"
M.append(m)

# 4. 天选6 Pro 酷睿版（Ultra 9 275HX / RTX5070 / 16G / 2T）
m = base("add-tianxuan6pro-intel", "天选6 Pro 酷睿版（U9-275HX/RTX5070/16G/2T）", "华硕", "gaming")
m["cpu"] = {"model": "Ultra 9 275HX", "vendor": "Intel", "cores": 24, "threads": 24,
            "baseW": None, "maxW": None, "coreSrc": "京东直读 + Intel 官方 24C24T"}
m["cpuId"] = "ultra-9-275hx"
m["ram"] = {"gb": 16, "type": "DDR5-5600", "config": None, "form": "sodimm", "slots": 2, "maxGB": 64}
m["storageGB"] = 2048
m["gpu"] = {"model": "RTX 5070", "vendor": "NVIDIA", "vramGB": 8, "type": "discrete",
            "tgpBaseW": None, "tgpMaxW": 115, "boost": None}
m["screen"] = {"sizeInch": 16, "widthPx": 2560, "heightPx": 1600, "refreshHz": 165, "panel": "IPS",
               "colorGamut": {"srgb": 100}, "brightnessNit": None}
m["batteryWh"] = 90
m["weightKg"] = 2.27
m["ports"] = ["USB-A ×3", "USB-C ×1", "雷电4 ×1", "HDMI 2.1", "RJ45"]
m["efficiency"] = 1  # FA608PM8940 苏宁直读 1 级（同天选6 Pro 锐龙版证据链）
m["price"] = {"list": 15599, "discounts": [], "net": 15599, "subsidy": 0, "final": 15599,
              "basisNote": "京东第三方店（京凡笔记本专营店）U9-275HX/5070/16G/2T 标价 15599。⚠️ 第三方店、国补情况未核；低配 5060/1T 第三方 13599、smzdm 页面价 10199 国补后 8312（低配）。PENDING-VERIFY"}
m["shopType"] = "thirdparty"
m["shopName"] = "京东 京凡笔记本专营店"
m["dataGaps"] = ["页面截图核对", "price 国补口径", "throttle"]
m["specSource"] = "京东直读（FX608L/FX608JM）+ 苏宁 FA608PM8940；90Wh/2.27kg/165Hz 2.5K；energyModel=FA608PM"
m["energyModel"] = "FA608PM"
M.append(m)

# 5. 华硕 无畏 Pro16 锐龙版 2026（锐龙 AI 9 H 465 / 16G / 512G）
m = base("add-wuwei16pro-2026", "华硕 无畏Pro16 锐龙版 2026（锐龙AI 9 H465/16G/512G）", "华硕", "thin")
m["cpu"] = {"model": "锐龙 AI 9 H 465", "vendor": "AMD", "cores": 10, "threads": 20,
            "baseW": None, "maxW": None, "coreSrc": "AMD 官方 10C20T"}
m["cpuId"] = "ryzen-ai-9-h465"
m["ram"] = {"gb": 16, "type": "LPDDR5X", "config": None, "form": "soldered", "slots": 0, "maxGB": None}
m["storageGB"] = 512
m["gpu"] = {"model": "AMD Radeon 890M（核显）", "vendor": "AMD", "vramGB": 0, "type": "integrated",
            "tgpBaseW": None, "tgpMaxW": None, "boost": None, "tgpNA": True}
m["screen"] = {"sizeInch": 16, "widthPx": 2560, "heightPx": 1600, "refreshHz": 165, "panel": "OLED",
               "colorGamut": None, "brightnessNit": None}
m["batteryWh"] = 80
m["weightKg"] = 1.65
m["ports"] = []
m["efficiency"] = 1  # 华硕商城列表页无畏系列均 1 级备案（官网 filter 直读语境），⚠️ 该子型号未单独直读
m["price"] = {"list": None, "discounts": [], "net": 7999, "subsidy": None, "final": 7999,
              "basisNote": "smzdm 2026-10-11：京东到手价 7999（锐龙 AI9 H465/16G/512G）。⚠️ 划线价未抓，到手价口径，PENDING-VERIFY"}
m["shopType"] = "self"
m["shopName"] = "京东"
m["dataGaps"] = ["ports", "screen.colorGamut", "brightnessNit", "efficiency 待单独直读", "页面截图核对"]
m["specSource"] = "华硕官网 filter 页（1.65kg/80Wh/2.5K 165Hz OLED）+ smzdm 价格稿"
M.append(m)

# 6. 七彩虹 隐星 P16 Pro 星风版 2026（Ultra7-251HX / RTX5060 / 32G / 1T）
m = base("add-yinxing-p16pro-xingfeng", "七彩虹 隐星P16 Pro 星风版 2026（U7-251HX/RTX5060/32G/1T）", "七彩虹", "gaming")
m["cpu"] = {"model": "Ultra 7 251HX", "vendor": "Intel", "cores": 18, "threads": 18,
            "baseW": None, "maxW": None, "coreSrc": "京东直读 + 百科 Arrow Lake HX 18核18线程（Intel ARK 6P+12E）"}
m["cpuId"] = "ultra-7-251hx"
m["ram"] = {"gb": 32, "type": "DDR5-5600", "config": None, "form": "sodimm", "slots": 2, "maxGB": None}
m["storageGB"] = 1024
m["gpu"] = {"model": "RTX 5060", "vendor": "NVIDIA", "vramGB": 8, "type": "discrete",
            "tgpBaseW": None, "tgpMaxW": 115, "boost": "满功耗 115W，接星风S1水冷底座整机 210W"}
m["screen"] = {"sizeInch": 16, "widthPx": 2560, "heightPx": 1600, "refreshHz": 300, "panel": "IPS",
               "colorGamut": {"srgb": 100}, "brightnessNit": 500}
m["batteryWh"] = 60
m["weightKg"] = 2.05
m["ports"] = ["USB-A ×2", "USB-C ×2", "HDMI 2.1", "RJ45"]
m["price"] = {"list": 13299, "discounts": [], "net": 11304.15, "subsidy": 1994.85, "final": 11304.15,
              "basisNote": "京东常卖价 11304.15（32G+1T 升级版，七彩虹游戏本旗舰店）；11304.15÷0.85=13299 整数 → 国补后价坐实（红旗口径①，list 为反推）。16G 版 10199.15÷0.85=11999 同坐实。首发口径 11499/国补后 9999（ZOL/vgover 测评），与常卖价有差，PENDING-VERIFY"}
m["shopType"] = "official"
m["shopName"] = "京东 七彩虹游戏本旗舰店"
m["dataGaps"] = ["efficiency", "throttle", "页面截图核对", "price 首发价与常卖价口径差异"]
m["specSource"] = "京东直读（认证型号 隐星 P16 Pro 星风版）+ 百度百科词条；60Wh/2.05kg/2.5K 300Hz 500nits/280W 氮化镓"
M.append(m)

# 7. 拯救者 R9000P 2026 32G 版（R9-8945HX / RTX5060 / 32G / 1T）
m = base("add-r9000p-2026-32g", "拯救者 R9000P 2026 32G版（R9-8945HX/RTX5060/32G/1T）", "联想", "gaming")
m["cpu"] = {"model": "R9-8945HX", "vendor": "AMD", "cores": 16, "threads": 32,
            "baseW": None, "maxW": None, "coreSrc": "京东直读 + AMD Zen4 16C32T/5.4GHz"}
m["cpuId"] = "r9-8945hx"
m["ram"] = {"gb": 32, "type": "DDR5", "config": None, "form": "sodimm", "slots": 2, "maxGB": None}
m["storageGB"] = 1024
m["gpu"] = {"model": "RTX 5060", "vendor": "NVIDIA", "vramGB": 8, "type": "discrete",
            "tgpBaseW": None, "tgpMaxW": 115, "boost": "CPU 120W / GPU 115W 释放"}
m["screen"] = {"sizeInch": 16, "widthPx": 2560, "heightPx": 1600, "refreshHz": 240, "panel": "IPS",
               "colorGamut": {"dciP3": 100}, "brightnessNit": None}
m["batteryWh"] = 80
m["weightKg"] = 2.55
m["ports"] = ["USB-A ×3", "USB-C ×2", "HDMI 2.1", "RJ45 2.5G"]
m["price"] = {"list": None, "discounts": [], "net": 16999, "subsidy": None, "final": 16999,
              "basisNote": "京东第三方店（星想联笔记本旗舰店）R9-8945HX/32G/1T/5060 常卖 16999；dir.jd 参考 17881.18；联想官方 wiki：32G 版比基础款贵约 3700（与 16G 版 final 12854 相洽）。⚠️ 第三方店、国补未计，PENDING-VERIFY"}
m["shopType"] = "thirdparty"
m["shopName"] = "京东 星想联笔记本旗舰店"
m["dataGaps"] = ["efficiency", "throttle", "页面截图核对", "price 国补口径"]
m["specSource"] = "京东直读（Legion R9000P ADR10 认证型号）+ 联想官方 wiki；80Wh/2.55kg/2.5K 240Hz DCI-P3"
M.append(m)

# 8. ThinkBook 16p 2026（Ultra7-255HX / RTX5060 / 32G / 1T）
m = base("add-thinkbook16p-2026", "ThinkBook 16p 2026（U7-255HX/RTX5060/32G/1T）", "联想", "gaming")
m["cpu"] = {"model": "Ultra 7 255HX", "vendor": "Intel", "cores": 20, "threads": 20,
            "baseW": None, "maxW": None, "coreSrc": "Intel ARK Arrow Lake HX 20核20线程（8P+12E）"}
m["cpuId"] = "ultra-7-255hx"
m["ram"] = {"gb": 32, "type": "DDR5", "config": None, "form": "sodimm", "slots": 2, "maxGB": 96}
m["storageGB"] = 1024
m["gpu"] = {"model": "RTX 5060", "vendor": "NVIDIA", "vramGB": 8, "type": "discrete",
            "tgpBaseW": None, "tgpMaxW": 115, "boost": "整机性能释放 170-200W"}
m["screen"] = {"sizeInch": 16, "widthPx": 2560, "heightPx": 1600, "refreshHz": 240, "panel": "IPS",
               "colorGamut": {"dciP3": 100}, "brightnessNit": None}
m["batteryWh"] = 85
m["weightKg"] = None
m["ports"] = ["雷电4 ×2", "USB-A", "HDMI", "SD读卡器"]
m["price"] = {"list": 15999, "discounts": [], "net": 15999, "subsidy": 0, "final": 15999,
              "basisNote": "联想官方 wiki：U7-255HX/32G/1T/RTX5060/2.5K 240Hz 参考价 15999；另一 wiki 基础创作版 14999 起；国补未计。PENDING-VERIFY"}
m["shopType"] = "official"
m["shopName"] = "联想官方口径"
m["dataGaps"] = ["weightKg", "efficiency", "throttle", "页面截图核对", "price 国补口径"]
m["specSource"] = "联想官方 wiki（85Wh/内存最大96GB/100% DCI-P3 出厂校色/双雷电4+SD读卡器）"
M.append(m)

# 9. 蛟龙16 Pro 2026 旗舰版（R9-8945HX / RTX5070 Ti / 32G / 1T）
m = base("add-jiaolong16pro-5070ti", "蛟龙16 Pro 2026 旗舰版（R9-8945HX/RTX5070Ti/32G/1T）", "机械革命", "gaming")
m["cpu"] = {"model": "R9-8945HX", "vendor": "AMD", "cores": 16, "threads": 32,
            "baseW": None, "maxW": 115, "coreSrc": "微信健圣规格表 16C32T/5.4GHz/115W"}
m["cpuId"] = "r9-8945hx"
m["ram"] = {"gb": 32, "type": "DDR5-5600", "config": None, "form": "sodimm", "slots": 2, "maxGB": None}
m["storageGB"] = 1024
m["gpu"] = {"model": "RTX 5070 Ti", "vendor": "NVIDIA", "vramGB": 12, "type": "discrete",
            "tgpBaseW": None, "tgpMaxW": 140, "boost": "140W 满功耗（微信健圣规格表 + 头条双源）"}
m["screen"] = {"sizeInch": 16, "widthPx": 2560, "heightPx": 1600, "refreshHz": 300, "panel": "IPS",
               "colorGamut": {"srgb": 100}, "brightnessNit": 500}
m["batteryWh"] = 80
m["weightKg"] = 2.25
m["ports"] = ["USB-A ×3", "USB-C ×2（后置 140W PD）", "Mini DP 2.1", "HDMI 2.1", "RJ45 千兆"]
m["price"] = {"list": 14499, "discounts": [{"name": "国补", "amount": 1500, "evidence": "微信健圣：页面价 14499，国补后 12999"}],
              "net": 12999, "subsidy": 1500, "final": 12999,
              "basisNote": "微信健圣 2026-11 规格表：页面价 14499 → 国补后 12999 → Plus 会员到手 12636.53（final 取国补后 12999）。⚠️ dir.jd 指南参考价 14999 与此不一致，PENDING-VERIFY"}
m["shopType"] = "self"
m["shopName"] = "京东自营（国补 SKU）"
m["dataGaps"] = ["efficiency", "throttle", "页面截图核对", "price dir.jd 14999 与 14499 口径差"]
m["specSource"] = "微信健圣规格表（80Wh/2.25kg/280W SiC/300Hz 500nits 100%sRGB ΔE<1）+ 头条/搜狐双源；⚠️ 5070Ti 版色域 100%sRGB，5070 版才是 100% DCI-P3，两版屏幕不同易混"
M.append(m)

def apply():
    data = json.loads(POOL.read_text(encoding="utf-8"))
    machines = data["machines"] if isinstance(data, dict) else data
    exist = {m_["id"] for m_ in machines}
    new = [m for m in M if m["id"] not in exist]
    bak = POOL.with_name(f"machine_pool.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(POOL, bak)
    if isinstance(data, dict):
        data["machines"] = machines + new
    else:
        data = machines + new
    POOL.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已备份 -> {bak.name}")
    for m in new:
        print(f"  + {m['id']} | final={m['price']['final']}")
    print(f"新增 {len(new)} 台（跳过已存在 {len(M)-len(new)}）")

if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply()
    else:
        for m in M:
            print(m["id"], "|", m["name"], "| final:", m["price"]["final"])
