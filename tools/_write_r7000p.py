# -*- coding: utf-8 -*-
import json, os, shutil

D = r'E:\电脑涨价分析\data\crawl'
F8 = os.path.join(D, '2026-11-08_jd.jsonl')
FV2 = os.path.join(D, '2026-11-07_jd_v2.jsonl')
SHOTS = r'E:\电脑涨价分析\crawl\shots'
SRC = r'C:\Users\23575\AppData\Local\Temp\trae\screenshots'

# 1) 归档截图（存在才拷）
pairs = [
    ('jd-100418894814-detail.png', 'jd-联想-拯救者R7000P-20261108-商品详情.png'),
]
for s, d in pairs:
    sp = os.path.join(SRC, s)
    if os.path.exists(sp):
        shutil.copy2(sp, os.path.join(SHOTS, d))
        print('copied', d)
    else:
        print('missing shot', s)

rec = {
    "id": "jd-lenovo-legion-r7000p2026-r9-8940hx-16g-1t",
    "platform": "jd",
    "shopType": "self",
    "shopName": "联想京东自营旗舰店",
    "shopTypeEvidence": "商品页店铺区显示「联想京东自营旗舰店」，店名含「自营」",
    "platformSkuId": "100418894814",
    "brand": "联想",
    "series": "拯救者 R7000P",
    "modelHint": "拯救者 R7000P 2026（R9-8940HX/RTX5060）",
    "certModel": "Legion R7000P ADR10",
    "energyModel": "Legion R7000P ADR10",
    "rawTitle": "联想拯救者R7000P 2026 16英寸游戏本笔记本电脑 R9-8940HX 16G 1T RTX5060 2.5K 240Hz",
    "config": {
        "cpu": {"model": "R9-8940HX", "cores": 16, "threads": 32},
        "ram": {"gb": 16, "type": None, "form": None, "slots": None, "maxGB": None},
        "storageGB": 1024,
        "gpu": {"model": "RTX 5060", "vramGB": 8, "tgpBaseW": 100, "tgpMaxW": 115, "type": "discrete"},
        "screen": {
            "sizeInch": 16, "widthPx": 2560, "heightPx": 1600, "refreshHz": 240,
            "panel": None,
            "colorGamut": {"sRGB": None, "dciP3": None, "adobeRGB": None},
            "brightnessNit": None
        },
        "batteryWh": 80,
        "weightKg": 2.35,
        "ports": ["USB-C ×2", "USB-A ×3", "HDMI 2.1", "USB-C(DP)", "RJ45 10/100/1000Mbps", "雷电/USB4：无（known_absent）"],
        "efficiency": "一级"
    },
    "price": {
        "list": 14499,
        "discounts": [],
        "net": 14499,
        "subsidy": 1500,
        "final": 12999,
        "basisNote": "list=页面划线价¥14499；discounts=无；net=14499；subsidy=「已享受：国家补贴 领后减￥1500」；final=「国补领后价」¥12999；校验：14499−1500=12999 成立。",
        "deltaUnverified": False
    },
    "stock": "unknown",
    "specComplete": False,
    "missingFields": [
        "ram.type", "ram.form", "ram.slots", "ram.maxGB",
        "screen.panel", "screen.colorGamut.sRGB", "screen.colorGamut.dciP3",
        "screen.colorGamut.adobeRGB", "screen.brightnessNit"
    ],
    "url": "https://item.jd.com/100418894814.html",
    "capturedAt": "2026-11-08T10:20:00+08:00",
    "evidence": {
        "screenshot": "E:/电脑涨价分析/crawl/shots/jd-联想-拯救者R7000P-20261108-商品详情.png",
        "priceVisibleOn": "详情页",
        "energyScreenshot": "能效网备案查询结果（见 2026-11-08_log.md 记录，截图在临时目录）"
    },
    "conflicts": [{
        "field": "config.gpu.tgpMaxW",
        "values": [
            {"v": 115, "src": "京东顶部摘要「115W GPU性能释放」"},
            {"v": 100, "src": "NVIDIA 官网 RTX 5060 笔记本 GPU 基础功率上限"}
        ],
        "adopted": 115,
        "why": "tgpBaseW 记 100（NVIDIA 口径），tgpMaxW 记 115（含 Dynamic Boost 的释放上限）；两者不是同一口径，不互相否定"
    }],
    "note": "三步流程：①商品详情——商品编号100418894814、净重2.35kg、型号/认证型号/能效网型号均为 Legion R7000P ADR10、屏幕16英寸2560×1600 240Hz、电池80Wh、无雷电端口（明确无，非缺失）；插槽数量页面未填。②官网——AMD R9-8940HX 16核32线程；NVIDIA RTX5060 笔记本 8GB GDDR7。③能效网——微型计算机2025版，能效等级「1级」，备案编号 20251211-CEL0272025-378760，公告日期 2025-12-11。库存：页面仅见「23:00前付款，预计明天送达」配送文案，无有货/无货原文，记 unknown；未进结算页。",
    "confidence": "high"
}

rows8 = [json.loads(l) for l in open(F8, encoding='utf-8') if l.strip()]
rows8 = [r for r in rows8 if r['id'] != rec['id']]
rows8.append(rec)
with open(F8, 'w', encoding='utf-8', newline='') as f:
    for r in rows8:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
print('11-08 rows', len(rows8))

# 2) 统一 Y9000P 的 TGP 口径
rows2 = [json.loads(l) for l in open(FV2, encoding='utf-8') if l.strip()]
n = 0
for r in rows2:
    if r['id'] == 'jd-lenovo-legion-y9000p2026-u9-290hx-16g-1t-20261107':
        g = r['config']['gpu']
        g['tgpBaseW'] = 100
        g['tgpMaxW'] = 115
        r['conflicts'] = [{
            "field": "config.gpu.tgpMaxW",
            "values": [
                {"v": 115, "src": "页面文案「RTX5060 115W」/ 京东摘要「115W GPU性能释放」"},
                {"v": 100, "src": "NVIDIA 官网 RTX 5060 笔记本 GPU 基础功率上限"}
            ],
            "adopted": 115,
            "why": "tgpBaseW 记 100（NVIDIA 口径），tgpMaxW 记 115（含 Dynamic Boost）；与此前 R7000P 口径统一"
        }]
        n += 1
with open(FV2, 'w', encoding='utf-8', newline='') as f:
    for r in rows2:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
print('y9000p patched', n)
