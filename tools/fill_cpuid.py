#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fill_cpuid.py —— 把已核实的 cpuId / cpu.cores / cpu.threads 填进 machine_pool.json

为什么不用 merge_patch.py：
  cpuId 与 cpu.* 在 merge_patch 的 FORBIDDEN 清单里（改了会翻转判定结论，
  必须人来决定）。本脚本就是「人来决定」的执行器：每一条填充都有
  evidence 引用，跑一遍打印全部 before/after，写盘前自动备份。

用法：
  python tools/fill_cpuid.py            # 演练
  python tools/fill_cpuid.py --apply    # 真写
"""
import json
import io
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POOL = ROOT / "data" / "machine_pool.json"

# (machine_id, 字段路径, 新值, 依据)
FILLS = [
    ("spec-parsed-jd-lenovo-thinkpade16-u5-32g-1t-authoriz", "cpuId", "ultra-5-125h",
     "京东授权店 SKU 10143602866442（认证型号 ThinkPad E16 Gen 2，『Ultra5-125H 32G 1T 2.5K』）+ 联想官方商城 b.lenovo.com.cn/product/1039930.html（Ultra 5 125H/32G/1T/2.5K 60Hz）+ bp.lenovo.com MTM 21MA0024CD（总核心数14/线程18）三处一致"),
    ("spec-parsed-jd-lenovo-thinkpade16-u5-32g-1t-authoriz", "cpu.cores", 14,
     "Intel ark 官方：Ultra 5 125H = 14C (4P+8E+2LPE)；联想官方商城同页『核心/线程数 14核心/18线程』"),
    ("spec-parsed-jd-lenovo-thinkpade16-u5-32g-1t-authoriz", "cpu.threads", 18,
     "同上"),
    ("spec-parsed-jd-dell-inspiron14plus-7440-core5-210h-1", "cpuId", "core-5-210h",
     "机型名即 Core5-210H；bench.json 已新增该芯片（Notebookcheck 聚合 R23 1771/11830）"),
    ("jd-mechrevo-jiaolong15k-r57640h-3050-16g-512g-20261109", "cpuId", "r5-7640h",
     "机型名即 R5-7640H；bench.json 已新增（gadgetversus 1730/13177 + NB 7640HS 交叉）"),
    ("jd-msi-shenying16-r8840hx-16g-512g-5060-20261110", "cpuId", "r7-8840hx",
     "机型名即 R7-8840HX；bench.json 已新增（NB 1857/25265，12C24T）"),
    ("add-adou14air", "cpuId", "r7-8840h",
     "库内 cpu.model=R7-8840H。⚠️ 该机 CPU 型号本身存疑（京东在售为锐龙AI 9 H 465，AGENTS.md P1 已记『CPU 型号也要改』），bench.json 两颗芯片均已入库，等用户拍板后只需改 cpuId 一处"),
    ("add-tianxuan-air-2026", "cpuId", "ryzen-ai-7-h350",
     "机型 cpu.model=锐龙AI 7 H 350；bench.json 已新增（NB median 1958/15879）"),
    ("add-thunde-renjian-s", "cpuId", "r7-7745hx",
     "机型 cpu.model=R7-7745HX；bench.json 已新增（NB Legion Pro 5 实测 1846/18060）。⚠️ 若用户对猎刃S conflict 选 B（R7+5070+16G）此 id 直接生效；选 A（i9-13900HX）需补 i9-13900HX 跑分后另改"),
    # ---- ram.form / ram.maxGB（merge_patch 白名单故意不放行，见脚本头注释）----
    ("spec-parsed-jd-dell-inspiron14plus-7440-core5-210h-1", "ram.form", "soldered",
     "戴尔中国官网技术规格：『16 GB, 2 个 8 GB(板载), LPDDR5x, 6400 MT/s, 双通道；32 GB, 2 个 16 GB(板载)』——LPDDR5x 板载焊死。京东参数区『插槽数量:7个』为参数区错误数据，弃用"),
    ("spec-parsed-jd-dell-inspiron14plus-7440-core5-210h-1", "ram.maxGB", 32,
     "戴尔官网：32 GB (2×16 GB 板载) 为顶配，最大即 32"),
    ("jd-huawei-matebookd16-i513420h-16g-1t-20261109", "ram.form", "soldered",
     "华为商城 vmall 官方参数（MCLG-16）：『内存类型 LPDDR4X』——LPDDR4X 物理上均为板载封装，京东自营『最大支持容量 16GB』佐证不可扩展"),
    # ---- efficiency / energyModel 修正（人工核实来源字段，留痕）----
    ("jd-lenovo-r9000p2026-r98945hx-16g-1t-5060-20261110", "efficiency", 1,
     "苏宁官方参数页（fe.suning.com/bigimages/12452114726.html）『国家能效等级 1级』，型号 Legion R9000P ADR10M 与库内 energyModel 精确一致；京东自营 SKU 10233513412046 / 10223305796338 参数区『能效网规格型号 Legion R9000P ADR10M』双 SKU 佐证"),
    ("jd-msi-shenying16-r8840hx-16g-512g-5060-20261110", "efficiency", 1,
     "什么值得买百科（转录京东自营参数，认证型号 MS-265L，R7-8840HX/RTX5060/16G/512G/2.5K240Hz 与库内完全一致）：『能效等级 一级能效』；ZOL 导购确认京东在售且参与国家能效补贴（补贴资格要求有能效备案）；微星官方手册 storage-asset.msi.com MS-265L-Crosshair-A16-HX-E8.pdf 确认 MS-265L=Crosshair A16 HX 系列。三源交叉"),
    ("jd-hp-omen11amd-r98945hx-16g-1t-5060-20261110", "efficiency", 1,
     "smzdm 转录京东参数：同认证型号 TPN-Q305 的 RTX5070/32G 版『能效等级 一级能效』；库内 5060 版京东自营参与国家能效补贴活动（笔记本评测网 bjbpc.com/tj/2297：能效补贴20% 立减，补贴资格要求能效备案）；ZOL 详解 16-ap0084AX。⚠️ 5060 版参数页未直读能效字段，判 1 级依据为同认证型号备案 + 补贴资格"),
    ("jd-colorful-jiangxingx16pro-i514450hx-16g-512g-5050-20261109", "efficiency", 1,
     "天猫七彩虹官方旗舰店基本信息『能效等级:一级』（SKU 含 i5-14450HX/RTX5060）；京东多 SKU 参数区确认『能效网规格型号:将星 X16 Pro』有备案。⚠️ 京东参数区本身未显示能效等级字段，天猫官方店为等级直读源"),
    ("add-acer-predator-helios10", "efficiency", 1,
     "smzdm 转录京东参数（认证型号 N25Q15，U7-356H/RTX5070/32G/1T）：『能效等级 一级能效』；京东自营 SKU 10223566616567 / 10225587049027 参数区确认『能效网规格型号 N25Q15』。库内记 N25Q15C 为子型号，同一备案系列"),
    ("add-laikuair16", "efficiency", 1,
     "什么值得买百科（转录京东参数）：来酷Air16 Core5 315 版『能效等级 一级能效』，认证型号 Lecoo N160A；苏宁参数页 fe.suning.com/bigimages/12453742322.html 确认库内 Core5 320 版认证型号同为 Lecoo N160A。同能效网规格型号共享备案，判 1 级（⚠️ 320 版京东参数页未直接直读，推断依据为同型号备案）"),
    ("jd-msi-shenying16-r8840hx-16g-512g-5060-20261110", "energyModel", "MS-265L",
     "京东自营参数区直读：认证型号/能效网规格型号 MS-265L（型号 Crosshair A16 HX E8WFK-250CNY）；库内原记 MS-15P4 有误（MS-15P4 为泰坦/绝影系列备案，台账中仅 MS-15T1/T2/T3 相近）"),
    ("add-yaoshi16-ultra", "efficiency", 1,
     "机械革命官方商城 mall.mechrevo.com/products/89（耀世16 Ultra U9 290HX Plus/32G/1T/5070Ti，能效网规格型号 机械革命耀世-06049C142）：『能效等级一级能效』；天猫机械革命官方旗舰店基本信息『能效等级:一级』；京东自营（能效网规格型号 机械革命耀世-06049C133）参数区未显示能效字段。⚠️ 库内 C133 与官方商城 C142 为同系列不同子型号，判 1 级"),
    ("jd-huawei-matebookd16-i513420h-16g-1t-20261109", "efficiency", 1,
     "海星智采（京东自营商品转录，i5-13420H/16G/1T/皓月银 与库内一致）：『能效等级 一级能效』；安徽政府采购云 mall.anhui.zcygov.cn（商品编号 100088433930，同配置）：『能效等级:一级能效』。双源一致"),
    # ---- 苹果 4 台（京东官方选购指南 + 能效标识实拍）----
    ("jd-apple-macbookair13-m4-16g-256g-20261109", "efficiency", 1,
     "京东官方选购指南 dir.jd.com（MacBook 2025 款指南）：『三款都运行 MacOS，能效等级都标一级能效』；微信公众号晒单实拍中国能效标识：Air 13 M4 规格型号 A3240，1级，典型能源消耗 3.0kWh A类"),
    ("jd-apple-macbookair15-m4-16g-512g-20261109", "efficiency", 1,
     "京东官方选购指南：Air 15 M4 一级能效；京东自营参数确认认证型号 A3241"),
    ("jd-apple-macbookpro14-m5-16g-512g-20261109", "efficiency", 1,
     "京东官方选购指南：MacBook Pro 14 M5 一级能效"),
    ("jd-apple-macbookpro14-m5-24g-1t-20261109", "efficiency", 1,
     "同上（同机型不同 SKU）"),
    ("jd-asus-tianxuan7pro-u7255hx-16g-1t-5060-20261110", "efficiency", 1,
     "smzdm 转录京东参数两条（FX608 认证型号：Ultra 7 251HX 版、U9 290HX Plus 版）均『能效等级 一级能效』；苏宁参数页确认库内 FX608LPG 型号、执行标准 GB 28380-2025；华硕官网 techspec。⚠️ 255HX SKU 参数页未直读能效字段，判 1 级依据为 FX608 系列同备案"),
    ("jd-mechrevo-jiaolong16pro2026-r98945hx-16g-1t-5070-20261110", "efficiency", 1,
     "smzdm 转录京东参数（蛟龙16 Pro 9955HX/RTX5070 版）：『能效等级 一级』；机械革命蛟龙16Pro 系列历史备案（747Q0）同为一级能效。库内 R9-8945HX/5070 版同系列"),
    ("add-canglong16pro", "efficiency", 1,
     "识货 shihuo.cn 两条商品参数（苍龙16 Pro 2026：9955HX/5070 版、8945HX/5060 版）均『能效等级为一级能效』；今日头条新品官宣『整机一级能效』；微信公众号测评『能效等级:一级能效』。多源一致"),
    ("jd-acer-shadowknight8-i714650hx-16g-1t-5060-20261110", "efficiency", 1,
     "微信公众号测评（转录京东自营参数，认证型号 N26L1，i7-14650HX/RTX5060/16G/2.5K180Hz）：『能效等级:一级能效』；京东自营参数确认能效网规格型号 N26L1"),
    ("jd-rog-strixscar18-u9275hx-16g-1t-5070ti-20261110", "efficiency", 1,
     "苏宁自营参数页 product.suning.com/0010336548/12447076559（G835LW275，Ultra9-275HX）：『国家能效等级:1级』；数码问答两篇转录京东参数『能效等级 一级能效』+『能效网规格型号 G835L』。双源一致"),
    ("jd-acer-shadowknight8-i714650hx-16g-1t-5060-20261110", "batteryWh", 53,
     "京东自营参数区『电池容量 53Wh』（SKU 宏碁京东自营旗舰店）+ 太平洋科技产品库 53Wh + 微信测评转录一致"),
    ("jd-acer-shadowknight8-i714650hx-16g-1t-5060-20261110", "weightKg", 2.5,
     "京东自营参数区『产品净重 2.5kg』+ 太平洋科技 2.5kg"),
    ("add-magicbookpro14", "efficiency", 1,
     "荣耀官方商城 honor.com/cn/shop/product/10086518268449（MagicBook Pro 14 2026 焕新版）：『节能认证 中国能效等级(CEL) 一级』；京东参数确认能效网规格型号 ZQC-P；北航校采京东转售页同型号。官方直读"),
    ("add-honorx14-amd", "efficiency", 1,
     "荣耀官方商城 honor.com/cn/shop/product/10086057119645（X14 锐龙版 2026，R5-6600H/16G/512G，认证型号 GDH-X）：『节能认证 中国能效等级(CEL) 一级』；京东参数确认认证型号 GDH-X。官方直读"),
    ("add-honorx14-amd", "batteryWh", 42,
     "荣耀官方商城：电池容量 42Wh(额定容量)；京东参数区『电池容量 42Wh』一致"),
    ("add-honorx14-amd", "weightKg", 1.43,
     "京东参数区『产品净重 1.43kg』+ 太平洋科技 1.43kg 一致"),
    ("add-adou14air", "efficiency", 1,
     "淘宝华硕笔记本电脑自营店基本信息『能效等级:一级』（SKU 含 a豆14 Air 2026 多配色版本）；北航校采京东转售页确认库内认证型号 M5451（锐龙AI 9 H 465 版）。⚠️ 京东主来源参数页未直读能效字段，等级直读源为华硕自营店"),
    ("jd-xiaomi-redmibookpro16-u5338h-32g-1t-20261109", "efficiency", 1,
     "京东官方选购指南 dir.jd.com（大屏高性能本指南）：Redmi Book Pro 16 2026『全金属机身、一级能效』；京东参数确认认证型号 A1602。京东官方指南直读"),
    ("add-matebook14-2026", "efficiency", 1,
     "办公招标网转录京东参数（FLMH-32，Ultra 5 32G 1T）：『能效等级 一级能效』；苏宁自营（FLMH-16，Ultra5-125H）：『国家能效等级:1级』；多边易商城/山庄润泽（FLMH-16）均一级能效。⚠️ 库内 Ultra 7 255H 子型号未直读，判 1 级依据为 FLMH 系列同备案多源一致"),
    ("add-wuwei14se-2026", "efficiency", 1,
     "海星智采（京东自营商品转录，Core5 320/16G/512G 与库内一致）：『能效等级 一级能效』；华硕商城 wap.asus.com.cn 确认型号 S3452MA"),
    ("add-wuwei14se-2026", "batteryWh", 52.5,
     "华硕商城商品参数：电池容量 52.5Wh；笔记本评测网 bjbpc『全系内置 52.5Wh』；腾讯新闻评测一致"),
    ("add-wuwei14se-2026", "weightKg", 1.29,
     "华硕商城：产品重量约 1.29kg；腾讯新闻/评测网一致"),
    ("add-xingbookproair14", "efficiency", 1,
     "天猫惠普中国官方旗舰店基本信息：『能效等级:一级』+ 能效备案号 20250326-CEL0272016-841912（SKU 含 Ultra5-225H/255H 全系）；惠普官方商城 hpstore.cn 确认库内 ku0030TU（Ultra 5 225H/16G/1T）"),
    ("add-xingbookproair14", "weightKg", 1.09,
     "惠普官方商城 hpstore.cn（ku0030TU 规格页）：机身重量约 1.09kg"),
    ("add-legion-r7000x-2026", "efficiency", 1,
     "天猫联想官方旗舰店基本信息『能效等级:一级』（SKU 碳晶黑+锐龙 7 H 255+RTX 5060+2.5K+165Hz+15.3英寸，与库内配置一致）；京东拯救者官方旗舰店参数确认能效网规格型号 Legion R7000X AHP11"),
    ("add-tianxuan-air-2026", "efficiency", 1,
     "天猫数码家电自营旗舰店基本信息『能效等级:一级』（华硕天选Air 2026 SKU）；京东官方旗舰店确认认证型号 FA401E。⚠️ 直读源为锐龙AI Max 版 SKU，库内 AI 7 H350 版为 FA401 系列同备案推断"),
    ("add-tianxuan-air-2026", "batteryWh", 73,
     "京东华硕电脑官方旗舰店参数『电池容量 73Wh』（FA401E）+ 太平洋科技开售稿『标配 73Wh 电池』"),
    ("add-acer-predator-helios9", "batteryWh", 76,
     "京东 A 宏碁电脑旗舰店/ACER宏碁旗舰店双 SKU 参数区『电池容量 76Wh』（战斧9 Neo S）"),
    ("add-canglong18pro-jd", "efficiency", 1,
     "smzdm 转录京东参数（苍龙18 Pro 9955HX/RTX5070 版）：『能效等级 一级能效』；京东官方选购指南（游戏本HX指南）：『整机释放 180-240W，一级能效』；海星智采京东自营转录『能效等级 一级能效』。三源一致"),
    ("add-canglong18pro-jd", "batteryWh", 80,
     "京东自营旗舰店参数区『电池容量 80Wh』（R9-8945HX SKU）+ 太平洋科技产品库 80Wh"),
    ("add-thunde-renjian16-13650", "efficiency", 1,
     "雷神官网商城 m.thunderobot.com 两个 SKU 商品属性直读『能效等级:一级』（i9-14900HX 版 item/1523、i7-14650HX 版 item/1519，认证型号 雷神R16）；京东自营参数确认能效网规格型号 雷神R16。库内 i5-13650HX 为同认证型号系列"),
    ("add-thunde-renjian16-13650", "batteryWh", 63,
     "京东自营参数区『电池容量 63Wh』（雷神R16 多 SKU）+ IT之家评测 63Wh"),
    # ---- E16 Gen2 电池/重量（Task #9 顺路，证据已核）----
    ("spec-parsed-jd-lenovo-thinkpade16-u5-32g-1t-authoriz", "batteryWh", 57,
     "联想官方商城 b.lenovo.com.cn/product/1039930.html（Ultra 5 125H/32G/1T/2.5K 配置页）：电池 57Wh；psref E16 Gen 2 同规格 57Wh 交叉"),
    ("spec-parsed-jd-lenovo-thinkpade16-u5-32g-1t-authoriz", "weightKg", 1.77,
     "联想官方商城同配置页：约 1.77 kg"),
    # ---- ram.form=sodimm 组（有插槽即非焊死；merge_patch 白名单不含 form，故在此人工填充）----
    ("spec-parsed-jd-asus-wuwei16-2026-r7h260-16g-512g-202", "ram.form", "sodimm",
     "华硕官网 M1607HA techspec：板载 8/16GB DDR5 + 1×DDR5 SO-DIMM 扩展槽；京东 slots=1。混合形态，判定语义=可加内存"),
    ("jd-mechrevo-kuangshix-i514450hx-rtx5050-32g-1t-20261109", "ram.form", "sodimm",
     "京东 10234898973363（i5-14450HX SKU）：插槽数量 2个 DDR5；拆机帖与港零售商规格页双 SO-DIMM 最高 64GB 交叉"),
    ("spec-parsed-jd-lenovo-xiaoxinpro16-u5-336h-24g-1t-20", "ram.form", "sodimm",
     "京东 JDG 电竞店同 SKU（Pro 16 IPH11，336H 24G）：插槽数量 2个 max 96GB；IT之家发布稿『32GB 内存(预留插槽)』交叉"),
    ("spec-parsed-jd-lenovo-xiaoxinpro16-r7h255-24g-1t-yih", "ram.form", "sodimm",
     "京东同系列 SKU（小新Pro16c AHP10R，R7-H255 24G）：插槽数量 2个 max 96GB"),
    ("spec-parsed-jd-lenovo-xiaoxinpro16-r7h255-24g-1t-fla", "ram.form", "sodimm",
     "同上（小新Pro16c AHP10R 系列）"),
    # ---- efficiency 补齐（第二轮）----
    ("add-omen-pro15-jd", "efficiency", 1,
     "smzdm 转录京东参数（Ultra7 251HX/RTX5060/16G/512G 版）：『能效等级 一级能效』+ 认证型号 TPN-I145；微信公众号测评转录京东参数同样『能效等级:一级能效』。双源一致"),
    ("add-omen-pro15-jd", "batteryWh", 70,
     "京东自营参数页（暗影精灵 Pro 15 多 SKU）：『电池容量 70Wh』"),
    ("add-acer-predator-helios9", "efficiency", 1,
     "天猫 acer宏碁旗舰店参数区：『能效等级:一级』（SKU 含 U9-275HX/RTX5070 与 i7-14650HX/RTX5060 两版本）；京东宏碁笔记本旗舰店确认认证型号 N25Q15/N25Q15C。⚠️ 京东主来源未直读等级字段，等级以天猫官方店直读为准"),
    ("add-tianxuan6pro", "efficiency", 1,
     "苏宁官方参数页直读天选6 Pro 酷睿版 FA608PM8940：『国家能效等级:1级』；门门网转录苏宁同 FA608PM8940『能效等级:1级』；京东锐龙版 SKU 确认库内能效网规格型号 FA608P 且参与国补15%（国补资格要求能效1/2级）；同门天选7 Pro（FX608）已直读 1 级。⚠️ 库内锐龙版 FA608P 未直读等级，判 1 级依据为 FA608 系列同备案 + 酷睿版直读 + 国补资格"),
    # ---- batteryWh 补齐（第三轮）----
    ("jd-mechrevo-jiguangx-cw-i714650hx-32g-1t-5060-20261109", "batteryWh", 80,
     "太平洋产品库（i7-14650HX/32GB/1TB/RTX5060/2.5K/300Hz 与库内完全同配置）：『电池容量 80Wh』；百度百科极光X潮玩版词条 80Wh；smzdm wiki 同规格 80Wh。三源一致"),
    ("jd-msi-xingying15max-i713620h-rtx5050-32g-1t-20261109", "batteryWh", 60,
     "微星海外官网（Cyborg 15 Max C13W，i7-13620H+RTX5050 同核心配置）：『Battery 3-Cell 60 Whrs』；百度百科星影15 Max 词条『内置 60Whr 电池』；今日头条/微信公众号星影15 Max HX 2026（2.5K 165Hz）评测『主机内置60Wh电池』+ 官方宣传图 60Whr；太平洋两篇 2.5K 版均 60Wh。整个星影15 Max 2026 系列同模具 60Wh，多源一致"),
    ("jd-msi-xingying15max-i713620h-rtx5050-32g-1t-20261109", "weightKg", 2.0,
     "微星海外官网（Cyborg 15 Max C13W，同核心配置）：『Weight (W/ Battery) 2 kg』；国内 240H 版太平洋标注约 2kg 交叉。⚠️ 国内 5050 版未单独直读，按同模具"),
    ("jd-hasee-zhanshen-t8-i713650hx-16g-1t-5060-20261109", "batteryWh", 53,
     "IT之家：战神T8 新增 2.5K 180Hz 版（i7-14700HX/RTX5060）『内置 53Wh 电池』；太平洋同屏幕规格（2.5K 180Hz）Ultra7 270HX 版攻略 53Wh；京东直读库内同 CPU 的 165Hz 版 53.35Wh 交叉。T8 系列 CV36 模具电池 53~53.35Wh"),
    ("add-matebook14-2026", "batteryWh", 70,
     "华为商城 vmall 直读（MateBook 14 酷睿 Ultra，FLMH-16，2026-03 上市）：『电池容量 70Wh (额定容量)』；华为消费者官网 specs 同 70Wh"),
    ("add-matebook14-2026", "weightKg", 1.31,
     "华为商城 vmall 直读（FLMH-16）：『机身重量 约1.31kg(含电池)』。⚠️ 直读为 Ultra 5 125H 版，库内 255H 版同模具未单独直读"),
    ("add-xingbookproair14", "batteryWh", 60,
     "ZOL 2026 轻薄本横评：星Book Pro Air 14『60Wh电池容量』；网易暑促横评、腾讯新闻评测均 60Wh + 100W 氮化镓。三媒体一致"),
    ("add-adou14air", "batteryWh", 70,
     "华硕官网 techspec 直读（a豆14 Air 2026 M5451UA）：『电池 70WHrs,4S1P,4 芯锂离子电池』；华硕商城列表页同 M5451『70Wh大电池』。⚠️ 百度百科 75Wh 为 2024 老款 M5406，不适用"),
    ("add-adou14air", "weightKg", 0.99,
     "华硕官网 techspec 直读（M5451UA）：『重量(约) 约 0.99kg』；华硕商城列表页『轻约 990g』交叉"),
    # ---- weightKg 补齐（第三轮）----
    ("spec-parsed-jd-lenovo-xiaoxinpro16-r7h255-24g-1t-fla", "weightKg", 1.76,
     "联想官方 wiki 配置表：小新Pro 16 2025锐龙版『重量 约1.76kg』；联想社区同 1.76kg、80Wh 交叉"),
    ("spec-parsed-jd-lenovo-thinkbook14plus-u5-225h-16g-51", "weightKg", 1.42,
     "京东自营参数页直读（Ultra5-225H SKU）：『产品净重 1.42kg』。⚠️ 联想官方 wiki 仅给 1.5-2.0Kg 范围、第三方店标 1.5kg，取同 CPU SKU 自营直读值"),
    ("jd-mechrevo-kuangshix-i514450hx-rtx5050-32g-1t-20261109", "weightKg", 2.2,
     "苏宁自营直读（i5-14450HX/RTX5050/2.5K 180Hz 同核心配置）：『净重 2.2千克』；今日头条笔吧评测室实测 2.27kg 交叉（含公差）"),
    ("jd-huawei-matebookd16-i513420h-16g-1t-20261109", "weightKg", 1.7,
     "京东自营参数页直读（V20R102 SKU，56Wh 电池版本）：『产品净重 1.7kg』；华为官网 specs 56Wh 版本『约 1.68 kg』交叉"),
    # ---- weightKg 补齐（第四轮）----
    ("jd-mechrevo-jiaolong16pro2026-r98945hx-16g-1t-5070-20261110", "weightKg", 2.25,
     "京东直读（R9-8945HX/RTX5070/白/300Hz 与库内同配置）：『产品净重 2.25kg』；微信公众号健圣测评规格表 80Wh/2.25kg；新浪科技发布稿『整机重约2.25kg』。三源一致"),
    ("add-acer-predator-helios9", "weightKg", 2.3,
     "京东 ACER宏碁旗舰店直读（U9-275HX/RTX5070/2.5K 240Hz）：『产品净重 2.3kg』；Acer宏碁电脑旗舰店同 2.3kg。⚠️ 宏碁笔记本旗舰店 N25Q15C SKU 标 2.5kg 为少数派，取官方旗舰店双店一致值"),
    ("add-canglong18pro-jd", "weightKg", 2.8,
     "京东自营直读（R9-8945HX/RTX5060/2.5K 灰）：『产品净重 2.8kg』；机械革命官网宣传『18英寸至轻2.8kg』交叉。⚠️ 机械革命商城页标 2.2kg 与 18 寸机身明显不符，疑似串页错误，弃用"),
    ("add-omen-pro15-jd", "weightKg", 2.52,
     "京东直读（TPN-I145，Ultra7 255HX/RTX5070/180Hz）：『产品净重 2.52kg』；smzdm wiki 同规格（Ultra7 251HX/5060）2.52kg 交叉"),
    # ---- E16 Core5-320 CPU 规格纠错（2026-11-11 晚，Intel ark 官方实证）----
    ("spec-parsed-jd-lenovo-thinkpade16-core5-320-16g-512g", "cpu.cores", 6,
     "Intel ark 官方（sku 246018）：Core 5 320 = Wildcat Lake，6核（2P+0E+4LPE）6线程，最高 4.6GHz，15W/35W，Q2'26；IT之家发布稿『6 核心 6 线程』交叉。⚠️ 原值 14核18线程 来自联想社区评测，系第一代 Ultra 5 125H 规格错套"),
    ("spec-parsed-jd-lenovo-thinkpade16-core5-320-16g-512g", "cpu.threads", 6,
     "同上：Intel ark 官方『總執行緒數 6』+ IT之家『6 核心 6 线程』"),
    ("spec-parsed-jd-lenovo-thinkpade16-core5-320-16g-512g", "batteryWh", 48,
     "IT之家发布稿：E16 2026 酷睿5 320 版『电池容量 48Wh』；京东 ThinkBook笔记本旗舰店多 SKU 直读『电池容量:48Wh』一致。⚠️ 原值 64 来源不明，官方双源 48"),
    ("spec-parsed-jd-lenovo-thinkpade16-core5-320-16g-512g", "weightKg", 1.71,
     "IT之家发布稿：『重 1.71kg，厚 17.05mm』；京东多 SKU 直读『产品净重 1.71kg』一致。⚠️ 原值 1.63 来源不明"),
]


def get_path(m, path):
    if "." not in path:
        return m.get(path)
    a, b = path.split(".")
    return (m.get(a) or {}).get(b)


def set_path(m, path, v):
    if "." not in path:
        m[path] = v
        return
    a, b = path.split(".")
    m.setdefault(a, {})[b] = v


def main():
    apply = "--apply" in sys.argv
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    index = {m["id"]: m for m in pool["machines"]}
    changed = 0
    for mid, path, val, ev in FILLS:
        m = index.get(mid)
        if m is None:
            print(f"[!] 机器不存在: {mid}")
            continue
        before = get_path(m, path)
        if before == val:
            print(f"  · {mid} {path} = {val!r}（已一致，跳过）")
            continue
        print(f"  · {mid[:52]}")
        print(f"      {path}: {before!r} -> {val!r}")
        print(f"      依据: {ev[:100]}{'...' if len(ev) > 100 else ''}")
        if apply:
            set_path(m, path, val)
        changed += 1
    print(f"\n共 {changed} 处待写" + ("（演练未写盘）" if not apply else ""))
    if not apply:
        return 0
    bak = POOL.with_name("machine_pool.json.bak-%s" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(POOL, bak)
    pool["meta"]["updated"] = datetime.now().strftime("%Y-%m-%d")
    POOL.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已备份 -> {bak.name}")
    print(f"已写入（请跑 python tools/coverage.py 验证基线 381）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
