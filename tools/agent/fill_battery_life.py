# -*- coding: utf-8 -*-
"""fill_battery_life.py —— 从厂商官网抓续航时长

## 为什么选这个源

实测 7 个数据源，只有厂商官网有结构化的「续航小时」：

| 源 | 结构化续航 | 说明 |
|---|---|---|
| ZOL 参数页 | ❌ | 只有「电池容量 Wh」 |
| ZOL 评测页 | ⚠️ | 只有用户主观评论 |
| 太平洋 / 京东 / 苏宁 | ❌ | 无字段或 JS 渲染 |
| **厂商官网** | ✅ | **官方宣传值** |

苹果规格页实测：
    最长可达 22 小时的流媒体视频播放
    最长可达 16 小时的无线上网

## ⚠️ 关键限制：这是「厂商宣称值」

厂商续航是在**特定测试条件**下的值（如「50% 亮度、关闭无线」），
和用户实际体验有差距。

**所以写入时必须标记 `basis: vendor-claim`，不能当实测值。**

`agent.py` 在展示续航时必须说明这一点，否则用户
会以为「官方说 16 小时，实际能用 16 小时」。

## 覆盖策略

只抓「品牌官网有规格页」的机型：
  · 苹果 —— 规格页最规范
  · 荣耀 / 华为 —— 产品页有续航
  · 联想 —— 规格库

其他品牌（惠普/华硕/戴尔/机械革命等）官网结构各异，
先跳过，避免写出错误数据。

## 用法

    python fill_battery_life.py --dry
    python fill_battery_life.py
"""
import argparse
import io
import json
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

POOL = r"E:\电脑涨价分析\data\machine_pool.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    r = urllib.request.urlopen(req, timeout=timeout)
    raw = r.read()
    enc = "utf-8"
    m = re.search(rb'charset=["\']?([a-zA-Z0-9-]+)', raw[:3000])
    if m:
        enc = m.group(1).decode().lower()
    if enc in ("gb2312", "gbk"):
        enc = "gbk"
    return raw.decode(enc, "replace")


def parse_battery_hours(html):
    """从页面提取续航小时数。

    苹果的写法：
        最长可达 22 小时的流媒体视频播放
        最长可达 16 小时的无线上网
    取「视频播放」那个（通常最长）。
    """
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    # 找「最长可达 N 小时的 X」
    pats = [
        (r"最长可达\s*(\d+(?:\.\d+)?)\s*小时的?流媒体视频播放", "video"),
        (r"最长可达\s*(\d+(?:\.\d+)?)\s*小时的?视频播放", "video"),
        (r"最长可达\s*(\d+(?:\.\d+)?)\s*小时的无线上网", "web"),
        (r"最长可达\s*(\d+(?:\.\d+)?)\s*小时", "generic"),
    ]
    for pat, kind in pats:
        m = re.search(pat, text)
        if m:
            return float(m.group(1)), kind
    return None, None


# 品牌官网规格页（只收实测有续航数据的）
VENDOR_PAGES = {
    "苹果": [
        ("MacBook Pro 14", "https://www.apple.com.cn/macbook-pro/specs/"),
        ("MacBook Air", "https://www.apple.com.cn/macbook-air/specs/"),
    ],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    pool = json.load(io.open(POOL, encoding="utf-8"))
    ms = pool["machines"]

    print("=" * 70)
    print("从厂商官网抓续航（%s）" % ("演练" if args.dry else "正式"))
    print("=" * 70)
    print("  ⚠️ 这是「厂商宣称值」，测试条件与实测不同")
    print()

    # 先抓官网数据
    vendor_data = {}
    for brand, pages in VENDOR_PAGES.items():
        for name, url in pages:
            try:
                html = get(url)
                hours, kind = parse_battery_hours(html)
                if hours:
                    vendor_data[name] = (hours, kind, url)
                    print("  ✓ %-16s %s 小时（%s）" % (name, hours, kind))
                else:
                    print("  ○ %-16s 未找到续航数据" % name)
            except Exception as e:
                print("  ✗ %-16s %s" % (name, str(e)[:40]))
            time.sleep(0.8)

    print()
    if not vendor_data:
        print("没抓到任何官网数据，退出")
        return

    # 匹配机型
    print("=" * 70)
    print("匹配机型")
    print("=" * 70)
    filled = 0
    for m in ms:
        if m.get("batteryLifeH"):
            continue
        name = m.get("name") or ""
        brand = m.get("brand") or ""
        if brand != "苹果":
            continue
        # 简单匹配：含 MacBook Pro → 用 Pro 的值
        for vname, (hours, kind, url) in vendor_data.items():
            key = vname.split()[0] + " " + vname.split()[1]
            if key.lower().replace(" ", "") in name.lower().replace(" ", ""):
                m["batteryLifeH"] = hours
                m["batteryLifeBasis"] = {
                    "type": "vendor-claim",
                    "scenario": kind,
                    "source": url,
                    "note": "厂商宣称值，测试条件与实际使用不同",
                }
                df = dict(m.get("dataFrom") or {})
                df["batteryLifeH"] = "厂商官网宣称值 %s" % url
                m["dataFrom"] = df
                bf = set(m.get("backfilled") or [])
                bf.add("batteryLifeH")
                m["backfilled"] = sorted(bf)
                print("  ✓ %-30s ← %s 小时" % (name[:28], hours))
                filled += 1
                break

    print()
    print("补入 %d 台" % filled)

    if args.dry:
        print("（演练，未写盘）")
        return
    if not filled:
        print("没有可写的，退出")
        return

    import shutil
    bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(POOL, bak)
    json.dump(pool, io.open(POOL, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("已写入，备份: %s" % bak)


if __name__ == "__main__":
    main()
