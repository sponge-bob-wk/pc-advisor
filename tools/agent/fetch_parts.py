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
    "ssd": ("固态", "https://detail.zol.com.cn/solid_state_drive/"),
    # 🔴 2026-11-26：散热器路径是 `/cooling_product/`，不是 `/cooling/`。
    #
    #    踩的坑：`/cooling/`、`/cpu_cooler/`、`/radiator/`、`/heat_sink/`、
    #    `/cpu_heat_sink/` 全部返回 502。试了 20 多种命名都不对。
    #
    #    最后从 ZOL 报价首页（detail.zol.com.cn）的整站导航里查到的：
    #       /cooling_product/  →  散热器
    #
    #    教训：猜 URL 猜不出来时，**去站点的导航页拿真实路径**，
    #    比一个个试快得多。
    "cooler": ("散热器", "https://detail.zol.com.cn/cooling_product/"),
    "psu": ("电源", "https://detail.zol.com.cn/power/"),
    "case": ("机箱", "https://detail.zol.com.cn/case/"),
}

# 🔴 2026-11-26：**固态频道要用专门的解析器**
#
#    问题（用户反馈「ssd必须修」）：
#      · `/ssd/` 返回 502，正确路径是 `/solid_state_drive/`
#      · 但它的页面结构和硬盘频道**完全不同**：
#
#        硬盘：  <h3><a href="/hard_drives/index123.shtml" title="西数蓝盘1TB">
#                    → 一个商品一个价格，直接可解析
#
#        固态：  <li data-follow-id="p1432186">
#                  <h3><a href="/series/626/10719652_1.html"
#                         title="长江存储·致态TiPlus7100">   ← **系列**链接
#                  <span class="price-type">359-1799</span>   ← **价格是区间**
#                  <a href="/solid_state_drive/index1432186.shtml"> ← 单品链接
#
#        即：频道页是「系列视图」，一个系列含 512G/1T/2T/4T 多个容量，
#        价格是区间。装机单要的是**具体容量 + 具体价格**，不能用区间。
#
#    解法：频道页按 <li> 块解析出系列 → 逐个抓单品页拿容量和价格。
#        单品页实测能拿到「致态TiPlus7100（512GB）¥359」这种精确信息。
SSD_CHANNEL = "ssd"

# ── 服务器/工作站级配件过滤 ──
#
# 🔴 2026-11-26 体检发现：库里混着服务器卡/工作站 CPU
#     ¥88,299  NVIDIA RTX PRO 6000 Blackwell 服务器版
#     ¥86,999  NVIDIA Tesla A100 80G
#     ¥49,999  AMD Ryzen ThreadRipper Pro 5995WX
#
# 这些不该进学生装机推荐：
#   · 价格是消费级的 10-20 倍，预算根本够不着
#   · 用 ECC 显存/专业驱动，游戏性能反而更差
#   · ThreadRipper 用 sTR5 插槽，普通主板装不上
#
# ⚠️ 判据要保守，别误伤消费级：
#   「RTX 5060 / RTX 5090」都是消费卡 → 保留
#   「RTX A6000 / RTX PRO 6000 / Tesla」是专业卡 → 过滤
PRO_ONLY_PAT = {
    "gpu": re.compile(
        r"(Tesla|Quadro|RTX\s*A\d{4}|RTX\s*Pro\s*\d|RTX\s*PRO\s*\d|"
        r"服务器版|数据中心|计算卡|加速卡)", re.I),
    "cpu": re.compile(
        r"(ThreadRipper|Threadripper|Xeon|EPYC|至强|霄龙)", re.I),
}


def is_pro_only(name, category):
    """是不是服务器/工作站专用配件。"""
    pat = PRO_ONLY_PAT.get(category)
    if not pat:
        return False
    return bool(pat.search(name or ""))


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
    "ssd": 100,          # 最便宜 500G SATA SSD 约 150
    "psu": 100,          # 最低端电源也要 100+
    "cooler": 40,        # 最便宜风冷约 50
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
    # 🔴 2026-11-26 修（体检发现的严重 bug）：
    #    `\d+核\d+线程` 里的 `\d+` 是贪婪的，会咬到型号数字。
    #    实测 `Intel 酷睿i7 87006核心/12线程` → 切成「Intel 酷睿i7」，
    #    型号 8700 丢了，导致 3 台不同的 i7 都叫同一个名字。
    #    改成 `\d{1,2}`（核心数最多两位），就不会吃进型号。
    r"(\d{1,2}核\d{1,3}线程|\d{1,2}核心/\d{1,3}线程|"
    r"\d+相供电|相供电|"                 # 「12+1+2相供电」这类
    r"高品质|闪电传输|支持风扇启停|全平台兼容|"
    r"便携移动|大容量|超频|散热系统|风之力|"
    r"ATX主板|前板铁网|额定功率|智能温控|"
    r"电竞|游戏电源|宽幅稳定|高效节能|"
    r"笔记本硬盘|笔记本机械|台式电脑机械|"
    r"\d+转\d+MB|NVIDIA\s+Ampere|Super\s+系列|"
    r"OC\s+|DLSS|XMP|"
    # 🔴 2026-11-26：代际后缀也是卖点，不是型号的一部分
    #    实测「Intel 酷睿 i7 14700K酷睿14代」→ 切到 14700K 为止
    r"酷睿\d+代|锐龙\d+代|第\d+代)")


def _split_model_core(name):
    """处理「型号 + 核心数」粘连的情况。

    🔴 2026-11-26：ZOL 的 CPU 名字经常写成「87006核心/12线程」——
        型号 8700 和核心数 6 中间没有分隔符。
        正则没法直接分，但可以按常识拆：
          Intel 型号是 4-5 位数字（8700 / 12700 / 9100），核心数是 1-2 位。

    做法：找到「数字串 + 核」，如果数字串长度 ≥5，就认为
          「前 N-1 位是型号 + 最后 1 位是核心数」；长度 4 位则是纯型号。
    """
    n = name or ""

    def repl(m):
        digits = m.group(1)      # 粘连的数字串，如 "87006"
        suffix = m.group(2)      # "核心" 或 "核"
        if len(digits) < 5:
            # 4 位及以下：整个是型号，核心数在别处或没有
            return m.group(0)
        # 5 位以上：最后 1-2 位是核心数
        # Intel/AMD 型号常见 4 位（8700 / 5600）或 5 位（12700）
        # 优先按「型号 5 位 + 核心 1 位」拆
        for model_len in (5, 4):
            if len(digits) > model_len:
                core = digits[model_len:]
                if 1 <= len(core) <= 2:
                    return digits[:model_len] + "\u0000" + core + suffix
        return m.group(0)

    # 匹配「数字 + 核」，数字至少 4 位
    n2 = re.sub(r"(\d{4,})(核心?)(?=[/，,]|\d|$)", repl, n)
    # 用 \u0000 标记切分点，后面 clean_name 会在这里切
    return n2


def clean_name(name):
    """切掉型号名后面的推广文案。"""
    n = (name or "").strip()

    # ⓪ 先处理「型号 + 核心数粘连」（如「87006核心」→「8700\x006核心」）
    n = _split_model_core(n)
    # 有切分标记时直接切在那里（这是最可靠的分界）
    if "\u0000" in n:
        n = n.split("\u0000")[0]

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
    cand = "".join(out)
    # 🔴 2026-11-26 加保护：切开后如果不足原长 40%，说明触发点太靠前，
    #    多半是误伤型号（实测「Intel 酷睿i7 8700…」被切成「Intel 酷睿i7」）。
    if len(cand) >= 4 and len(cand) >= len(n) * 0.4:
        n = cand

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


def parse_ssd_list(html):
    """解析固态频道页 —— 按 <li> 块拿「系列名 + 价格区间 + 单品链接」。

    结构见 SSD_CHANNEL 上方的注释。
    """
    out = []
    for m in re.finditer(r'<li\s+data-follow-id="p(\d+)"[^>]*>(.*?)</li>',
                         html, re.S):
        pid, block = m.group(1), m.group(2)
        nm = re.search(r'<h3>\s*<a[^>]*title="([^"]{2,90})"', block)
        if not nm:
            continue
        name = nm.group(1).strip()
        pm = re.search(r'class="price-type"[^>]*>\s*([\d,\-]+)', block)
        rng = pm.group(1).strip() if pm else ""
        lm = re.search(r'href="(/solid_state_drive/index(\d+)\.shtml)"', block)
        single = (lm.group(1) if lm
                  else "/solid_state_drive/index%s.shtml" % pid)
        out.append({
            "name": name,
            "priceRange": rng,
            "pid": pid,
            "url": "https://detail.zol.com.cn" + single,
        })
    return out


def parse_ssd_detail(url):
    """抓固态单品页，返回 (具体型号名, 单一价格)。

    单品页的 <h1> 带容量：「致态TiPlus7100（512GB）」
    price-type 是单一价格：「359」

    🔴 2026-11-26：**固态的容量不能清洗掉**。
        实测 clean_name 把「（512GB）」当推广文案切了，
        结果装机和装机单里只写「致态TiPlus7100」，
        用户不知道是 512G 还是 4T —— 而容量是 SSD 最关键的信息。

        所以这里只做轻清洗（去掉逗号后的推广语），
        容量、接口、协议这些**保留**。
    """
    html = fetch(url)
    name = ""
    m = re.search(r'<h1[^>]*>([^<]{2,100})</h1>', html)
    if m:
        name = m.group(1).strip()
    if not name:
        m = re.search(r'<title>【([^】]{2,80})】', html)
        if m:
            name = m.group(1).strip()

    # 只切中文逗号后的推广文案，不碰括号里的容量/协议
    m = re.search(r"[，,]", name)
    if m and m.start() >= 4:
        name = name[:m.start()]
    # 去掉卖点词（「性能，低功耗」「NVMe协议，PCIe5.0」这类）
    m = NAME_CUT_WORDS.search(name)
    if m and m.start() >= 4:
        name = name[:m.start()]
    name = name.strip(" ，,、-")

    price = None
    pm = re.search(r'class="price-type"[^>]*>\s*([\d,]+)', html)
    if pm:
        try:
            price = float(pm.group(1).replace(",", ""))
        except ValueError:
            price = None
    return name, price


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
        # ③ 过滤服务器/工作站专用件
        if is_pro_only(name, category):
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
    # 🔴 2026-11-26：固态要逐个抓单品页（频道页只有系列+价格区间），
    #    所以限制抓多少个，默认 30 个（约 25 秒）。
    ap.add_argument("--ssd-max", type=int, default=60,
                    help="固态最多抓多少个系列的单品页（默认 60）")
    # 🔴 2026-11-26：固态频道是分页的，第 1 页只有 22-26 个系列。
    #    要更多型号必须翻页：/solid_state_drive/2.html、3.html …
    ap.add_argument("--ssd-pages", type=int, default=5,
                    help="固态最多翻几页（每页约 24 个系列）")
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

            # 🔴 2026-11-26：固态走专门的解析路径
            #    频道页只给系列和价格区间，要逐个抓单品页拿具体容量+价格。
            #
            #    而且频道页是**分页**的（第 1 页只有 22-26 个系列）。
            #    要更多型号得翻页：/solid_state_drive/2.html、3.html …
            if key == SSD_CHANNEL:
                groups = parse_ssd_list(html)
                # 翻页收集
                for _pg in range(2, args.ssd_pages + 1):
                    try:
                        _u = url.rstrip("/") + "/%d.html" % _pg
                        _h = fetch(_u)
                        _g = parse_ssd_list(_h)
                        if not _g:
                            break
                        groups.extend(_g)
                        print("      第 %d 页 +%d 个系列" % (_pg, len(_g)))
                    except Exception:
                        break
                    time.sleep(args.sleep)
                # 按 pid 去重（翻页可能有重复）
                _seen_pid = set()
                _uniq_g = []
                for g in groups:
                    if g["pid"] in _seen_pid:
                        continue
                    _seen_pid.add(g["pid"])
                    _uniq_g.append(g)
                groups = _uniq_g
                print("      共 %d 个系列（%d 页）"
                      % (len(groups), args.ssd_pages))
                items = []
                for g in groups[:args.ssd_max]:
                    try:
                        nm, pr = parse_ssd_detail(g["url"])
                        if not nm or not pr:
                            continue
                        # 价格下限过滤（PRICE_FLOOR 里 ssd 是 100）
                        if pr < PRICE_FLOOR.get("ssd", 100):
                            continue
                        if is_pro_only(nm, "ssd"):
                            continue
                        items.append({"name": nm, "price": pr,
                                      "url": g["url"]})
                    except Exception:
                        continue
                    time.sleep(args.sleep * 0.7)
                # 去重
                seen = set()
                uniq = []
                for it in items:
                    k = (it["name"], it["price"])
                    if k in seen:
                        continue
                    seen.add(k)
                    uniq.append(it)
                items = uniq
            else:
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
