# -*- coding: utf-8 -*-
"""fetch_parts.py —— 抓取台式机组装配件价格

## 用户需求

「台式机也可以考虑，抓取主流的零部件去进行采集价格，
  如果有人想组装台式机，就可以推荐不同组合，
  然后把每个部分列举出来价格，再汇总价格，整机多少钱」

## 数据源

ZOL 配件频道（和机型参数页同源，GBK 编码）。实测 7 个频道可用：

    CPU    https://detail.zol.com.cn/cpu/
    显卡   https://detail.zol.com.cn/vga/
    内存   https://detail.zol.com.cn/memory/
    主板   https://detail.zol.com.cn/motherboard/
    硬盘   https://detail.zol.com.cn/hard_drives/
    电源   https://detail.zol.com.cn/power/
    机箱   https://detail.zol.com.cn/case/

固态（/ssd/）和散热（/cooling/）返回 502，暂不可用。

## 页面结构（实测）

    <h3><a href="/cpu/index2018967.shtml" title="AMD Ryzen 5 9600X">
        AMD Ryzen 5 9600X</a></h3>
    <div class="price-row">
      <span class="price-tip">参考价：</span>
      <span class="price price-normal">
        <b class="price-sign">￥</b><b class="price-type">1499</b>
      </span>
    </div>

型号名和价格**在同一块里相邻**，所以按「先找 h3 的名字，再找紧跟的价格」
配对，比全局正则可靠（全局匹配会把名字和价格错位）。

## ⚠️ 数据口径

抓的是 **ZOL 参考价**（媒体价），不是电商到手价。
所以写入时必须标 `priceType: "zol-reference"`，
展示时要说"参考价"，不能说"到手价"。

## 用法

    python fetch_parts.py --dry            # 只看能抓多少
    python fetch_parts.py                  # 写进 data/parts.json
    python fetch_parts.py --category cpu   # 只抓一类
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "data", "parts.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 配件类别 → ZOL 频道页
CHANNELS = {
    "cpu": ("CPU", "https://detail.zol.com.cn/cpu/"),
    "gpu": ("显卡", "https://detail.zol.com.cn/vga/"),
    "ram": ("内存", "https://detail.zol.com.cn/memory/"),
    "motherboard": ("主板", "https://detail.zol.com.cn/motherboard/"),
    "storage": ("硬盘", "https://detail.zol.com.cn/hard_drives/"),
    "psu": ("电源", "https://detail.zol.com.cn/power/"),
    "case": ("机箱", "https://detail.zol.com.cn/case/"),
}

# 各类配件的**合理价格下限**（低于这个值就是抓错或非整件）。
#
# 🔴 2026-11-26 实测发现的问题：
#    显卡频道抓到「NVIDIA Tesla H200 141G ¥25」——
#    那是数据中心加速卡，实际几十万，¥25 明显是 ZOL 页面上的
#    占位价/询价。如果不过滤，装机单里会出现「显卡 25 元」这种离谱数字。
#
# 下限保守取「该类别最低合理价」，宁可漏也不要错：
PRICE_FLOOR = {
    "cpu": 300,          # 最低端 i3 也要 300+
    "gpu": 500,          # 最低端亮机卡 500+
    "ram": 80,           # 单条 8G DDR4 约 100
    "motherboard": 200,  # 最低端 H610 约 400
    "storage": 150,      # 500G 机械约 200
    "psu": 100,          # 最低端电源也要 100+
    "case": 60,          # 最便宜机箱约 60
}

# 型号名里混进来的推广描述，要切掉。
#
# 实测原始值（都来自 ZOL 页面 title 属性）：
#   "Intel 酷睿i3 9100F4核心/4线程，睿频至高可达4.3GHz，傲腾技术，6MB三级缓存"
#   "技嘉B860M AORUS PRO WIFI712+1+2相供电设计"
#   "瀚铠Radeon RX 9070 XT 红色超合金PRO高品质PCB板材，三风扇"
#   "金士顿叛逆者Renegade 32GB(16G*2) DDR5 6000 灯条RGB白XMP超频"
#   "西部数据黑盘 1TB 7200转 64MB SATA3（WD1003FZEX）大容量"
#   "乔思伯Z20便携移动"
#
# 这些尾巴是 ZOL 的卖点文案，不是型号。装机单里列出来又长又乱。
#
# 策略（按可靠性排序）：
#   ① 遇到中文逗号「，」直接切 —— 后面几乎所有情况都是文案
#   ② 遇到已知的卖点词切
#   ③ **中文字符超过 6 个就切**（型号名里中文一般是品牌，"乔思伯"3 字、
#      "金百达银爵"5 字、"西部数据黑盘"6 字，再长基本是文案）
NAME_CUT_HARD = re.compile(r"，")
NAME_CUT_WORDS = re.compile(
    r"(\d+核\d+线程|\d+核心/\d+线程|"
    r"\d+相供电|相供电|"                 # 「12+1+2相供电」这类
    r"高品质|闪电传输|支持风扇启停|全平台兼容|"
    r"便携移动|大容量|超频|散热系统|风之力|"
    r"ATX主板|前板铁网|额定功率|智能温控|"
    r"电竞|游戏电源|宽幅稳定|高效节能|"
    r"笔记本硬盘|笔记本机械|台式电脑机械|"
    r"\d+转\d+MB|NVIDIA\s+Ampere|Super\s+系列|"
    r"OC\s+|DLSS|XMP)")


def clean_name(name):
    """切掉型号名后面的推广文案。"""
    n = (name or "").strip()

    # ① 中文逗号直接切
    m = NAME_CUT_HARD.search(n)
    if m and m.start() >= 4:
        n = n[:m.start()]

    # ①b 「WIFI7」后面紧跟「12+1+2」这种供电规格 —— 型号名到 WIFI7 为止。
    #     实测：「技嘉B860M AORUS PRO WIFI712+1+2相供电设计」
    #     → 型号是「技嘉B860M AORUS PRO WIFI7」，后面全是卖点。
    m = re.search(r"(WIFI\s*[67])\d", n, re.I)
    if m:
        n = n[:m.end(1)]

    # ② 已知卖点词切
    m = NAME_CUT_WORDS.search(n)
    if m and m.start() >= 4:
        n = n[:m.start()]

    # ②b 括号残尾处理
    #
    #    实测两种残尾：
    #      ① 未闭合：「（WD10SPSX」        ← 右括号被别的规则切掉了
    #      ② 平衡但多余：「（WD1003FZEX）」← 括号完整，但型号名末尾挂个括号难看
    #
    #    ① 切掉「（」及之后；② 只去掉末尾那个「）」。
    if n.count("（") > n.count("）"):
        n = n[:n.rfind("（")]
    elif n.count("(") > n.count(")"):
        n = n[:n.rfind("(")]
    else:
        # 平衡的情况：把末尾的「）」去掉（型号名不需要挂括号）
        if n.endswith("）") or n.endswith(")"):
            n = n[:-1]

    # ③ 中文字数兜底：型号名里中文一般不连续超过 6 个
    #    （"西部数据黑盘"正好 6 个，"金士顿叛逆者Renegade"里的中文是 5 个）
    out = []
    cn_run = 0
    for ch in n:
        if "\u4e00" <= ch <= "\u9fff":
            cn_run += 1
            if cn_run > 6:
                break
        else:
            cn_run = 0
        out.append(ch)
    if len(out) >= 4:
        n = "".join(out)

    # ④ 括号闭合：切掉「（WD1003FZEX」这种没闭合的残尾
    if n.count("（") > n.count("）"):
        n = n[:n.rfind("（")]
    if n.count("(") > n.count(")"):
        n = n[:n.rfind("(")]

    return n.strip(" ，,、-（）()【】")


def fetch(url, retries=2):
    """抓页面，返回 gbk 解码后的 html。"""
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            raw = urllib.request.urlopen(req, timeout=25).read()
            return raw.decode("gbk", "replace")
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(2)
    raise last


def parse_items(html, category=""):
    """从频道页提取 [(型号, 参考价, 详情链接)]。

    🔴 关键：**按块配对，不要全局正则**。

       第一版想的是「全部 h3 取名字 + 全部 price 取价格，然后 zip」，
       但那会错位 —— 页面上还有筛选栏、导航里的 `<h3>`，
       数量对不上，zip 之后名字和价格就串了。

       实测的正确结构是：每个商品是一个块，
       h3（名字+链接）后面紧跟 .price-row（价格）。
       所以用 h3 定位，再从它往后 800 字符内找第一个价格。

    🔴 2026-11-26 加两道清洗（实测发现的脏数据）：

       ① **价格下限过滤** —— 显卡频道有「NVIDIA Tesla H200 ¥25」，
          那是问价占位符，实际几十万。不过滤会出现"显卡 25 元"。
       ② **型号名清洗** —— 原始值带推广文案：
          「Intel 酷睿i3 9100F4核心/4线程，睿频至高可达4.3GHz」
          装机单里列这种名字太长，切掉尾巴。
    """
    out = []
    floor = PRICE_FLOOR.get(category, 0)
    # 商品块：<h3><a href="/xxx/index1234567.shtml" title="型号名">
    pat = re.compile(
        r'<h3>\s*<a\s+href="(/[a-z_]+/index\d+\.shtml)"\s+'
        r'title="([^"]{2,90})"[^>]*>',
        re.I)
    for m in pat.finditer(html):
        link, name = m.group(1), m.group(2).strip()
        # 从 h3 之后找价格
        tail = html[m.end():m.end() + 900]
        pm = re.search(
            r'class="price-type"[^>]*>\s*([\d,]+(?:\.\d+)?)', tail)
        if not pm:
            continue
        try:
            price = float(pm.group(1).replace(",", ""))
        except ValueError:
            continue
        if price <= 0:
            continue
        # ① 价格下限
        if floor and price < floor:
            continue
        # ② 名字清洗
        name = clean_name(name)
        if len(name) < 3:
            continue
        out.append((name, price, "https://detail.zol.com.cn" + link))

    # 去重（同名同价保留一条）
    seen = set()
    uniq = []
    for name, price, link in out:
        k = (name, price)
        if k in seen:
            continue
        seen.add(k)
        uniq.append({"name": name, "price": price, "url": link})
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--category", default="")
    ap.add_argument("--sleep", type=float, default=0.8)
    args = ap.parse_args()

    targets = ({args.category: CHANNELS[args.category]}
               if args.category in CHANNELS else CHANNELS)

    print("=" * 74)
    print("抓取装机配件价格（%s）" % ("演练" if args.dry else "正式"))
    print("=" * 74)
    print("  ⚠️ 抓的是 ZOL 参考价（媒体价），不是电商到手价")
    print()

    data = {}
    if os.path.exists(OUT):
        try:
            data = json.load(io.open(OUT, encoding="utf-8"))
        except Exception:
            data = {}

    now = time.strftime("%Y-%m-%d %H:%M")
    total = 0

    for key, (cn, url) in targets.items():
        try:
            html = fetch(url)
            items = parse_items(html, key)
            print("  ✓ %-12s %2d 个  %s" % (cn, len(items), url))
            if items:
                # 只打印价格区间，不刷屏
                ps = [x["price"] for x in items]
                print("      价格区间 ¥%d – ¥%d" % (min(ps), max(ps)))
                for x in items[:2]:
                    print("        · %-42s ¥%d" % (x["name"][:40], x["price"]))
            data[key] = {
                "label": cn,
                "source": url,
                "priceType": "zol-reference",
                "fetchedAt": now,
                "items": items,
            }
            total += len(items)
        except Exception as e:
            print("  ✗ %-12s %s" % (cn, str(e)[:45]))
        time.sleep(args.sleep)

    print()
    print("  合计 %d 个配件" % total)

    if args.dry:
        print("  （演练，未写盘）")
        return
    if not total:
        print("  没抓到任何数据，不写盘")
        return

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    bak = OUT + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    if os.path.exists(OUT):
        import shutil
        shutil.copy2(OUT, bak)
        print("  备份: %s" % os.path.basename(bak))
    json.dump(data, io.open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("  已写入 %s" % OUT)


if __name__ == "__main__":
    main()
