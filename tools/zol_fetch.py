# -*- coding: utf-8 -*-
"""中关村在线（ZOL）参数采集 —— 替代京东商品页取数（2026-11-16）

## 为什么换源

原采集走 `jd_fetch.py`：有头 Edge、单页、`human_pause(3~8s)`、
命中频控直接退出。一台机器要几秒到十几秒，而且**采不到完整参数** ——
京东商品详情是图片 + 动态渲染，抓下来的字段要靠人工补。

ZOL 的差异（全部实测过，不是估计）：

    列表页 200 / GBK 编码 / 一页 73~79 个产品 id / 翻页只改 list_N.html
    参数页 200 / GBK 编码 / **23~42 个参数项**，结构统一：
        <th><span id="newPmName_19">屏幕尺寸</span></th>
        <td ...><span id="newPmVal_19">14英寸</span></td>
    无需登录、无需 JS 渲染、无验证码
    一页里**同时带京东价和天猫价**（电商报价格），不用另开报价页

速度对比（实测）：ZOL 约 1.6 秒/台（含限速），京东约 5~10 秒/台且会撞频控。

38 项里包含现有采集池**没有**的：功耗(W)、电池瓦时、接口明细、
军标认证、键盘是否背光、厚度、外壳材质。

## 能拿到什么 / 拿不到什么（必须先说清，避免当成功）

拿到：
  · 型号、品牌、上市时间、产品定位
  · CPU 系列、内存类型与频率、硬盘类型与协议
  · 屏幕：尺寸/类型(OLED/IPS)/刷新率/色域/亮度/屏占比/响应时间/分辨率
  · 电池：芯数 + 瓦时、电源适配器功率
  · 接口：视频/音频/其它
  · 重量(kg)、长宽厚(mm)
  · 键盘是否背光、人脸识别、军标
  · **各平台快照价**：京东价 / 天猫价 / 媒体参考价（见下方口径说明）

拿不到 / 口径不同（必须当心）：
  · **到手价** —— ZOL 给的是快照价，不含国补/平台券。
    本项目所有金额口径原来都是「到手价」，所以这里采到的只能存进
    `priceJD` / `priceTmall` / `priceRef` 三个独立字段，
    **绝不许写进 `price`**。两个口径混进一个字段，预算判断和比价会全错。
  · 实测跑分 —— ZOL 不测，我们的 perf 分是自己跑 Cinebench/Geekbench 换算的。
  · 优缺点评价 —— ZOL 有评测文章但不结构化，不硬凑。

## 用法

    python zol_fetch.py --cat notebook --pages 2 --limit 20
    python zol_fetch.py --cat notebook --pages 10          # 全量
    python zol_fetch.py --cat desktop_pc --pages 3

输出：`data/zol/<cat>-<日期>.jsonl`，一行一台，可断点续采
（已存在的 id 会跳过，不重复请求）。
"""
import argparse
import gzip
import json
import os
import random
import re
import sys
import time
import urllib.request
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(HERE), "data", "zol")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 品类 → ZOL 列表页路径。
#
# ⚠️ 2026-11-16 实测的可用/不可用情况，别照抄别处的分类 id：
#    notebook    ✅ `notebook_index/subcate16_list_N.html` 可翻页，一页约 75 台
#    cell_phone  ✅ `cell_phone_index/subcate57_list_N.html` 可翻页
#    desktop_pc  ⚠️ 分页路径 `desktop_pc_index/subcate27_list_N.html` **恒 502**，
#                   但品类首页 `desktop_pc/` 是 200 且一次给 79 个 id。
#                   所以台式机走「首页模式」（pages=1 且不带 _list）。
#    memory      ⚠️ 分页路径同样 502，首页可用；但配件类参数结构与整机差异大，
#                   字段映射需要单独写，先不纳入自动流程。
CATS = {
    "notebook": "notebook_index/subcate16_list_%d.html",
    "cell_phone": "cell_phone_index/subcate57_list_%d.html",
}
# 「只有首页、不能翻页」的品类：直接用品类根路径
FLAT_CATS = {
    "desktop_pc": "desktop_pc/",
}

BASE = "https://detail.zol.com.cn/"


def fetch(url, retry=2, timeout=20):
    """取一页。返回解码后的文本（ZOL 是 GBK）。

    🔴 必须试 gzip：ZOL 有时返回压缩内容，不解压会得到二进制垃圾，
       而 requests 会自动处理、urllib 不会 —— 这是换标准库最容易踩的坑。
    """
    last = None
    for i in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "gzip, deflate",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                # GBK 优先，失败再退 utf-8。errors=replace 保证不因个别脏字节整页丢掉。
                for enc in ("gbk", "gb18030", "utf-8"):
                    try:
                        return raw.decode(enc)
                    except UnicodeDecodeError:
                        continue
                return raw.decode("gb18030", errors="replace")
        except Exception as e:
            last = e
            if i < retry:
                time.sleep(1.5 * (i + 1))
    raise last


_ID_RE = re.compile(r'/(' + '|'.join(k.split('_')[0] for k in
                                    ["notebook", "desktop_pc", "cell_phone", "memory"]) +
                    r')/index(\d+)\.shtml')

# 列表页里产品链接的三种写法都见过，统一抓「品类 + 数字 id」
_LIST_ID = re.compile(r'/(notebook|desktop_pc|cell_phone|memory)/index(\d+)\.shtml')


def list_ids(cat, page):
    """从列表页拿产品 id。一页约 75~80 个。"""
    if cat in FLAT_CATS:
        url = BASE + FLAT_CATS[cat]
    else:
        url = BASE + (CATS[cat] % page)
    html = fetch(url)
    seen = []
    for m in _LIST_ID.finditer(html):
        pid = m.group(2)
        if pid not in seen:
            seen.append(pid)
    return seen


_PARAM = re.compile(
    r'<th>\s*<span id="newPmName_\d+">([^<]+)</span></th>\s*<td[^>]*>(.*?)</td>',
    re.S)
_TAG = re.compile(r'<[^>]+>')
_WS = re.compile(r'\s+')


def _clean(s):
    s = _TAG.sub(" ", s or "")
    s = s.replace("纠错", " ").replace("更多", " ")
    s = _WS.sub(" ", s).strip()
    # ZOL 在值后面挂了「>」和用途链接文字，如 "120Hz>游戏、便捷"
    s = s.strip(">").strip()
    return s


def param_url(pid):
    """参数页地址：id 前 4 位做目录。

    实测：2165921 → /2166/2165921/param.shtml
    """
    return BASE + "%s/%s/param.shtml" % (pid[:4], pid)


# ── 从标题/型号里取关键配置 ─────────────────────────────────────
# ZOL 参数表**没有**「CPU型号 / 内存容量 / 硬盘容量 / 分辨率」这四项，
# 它们只在两个地方：① 页面标题的型号串 ② product-link 的 title 属性。
# 所以必须单独解析，不能指望参数表给全。
#
# 🔴 2026-11-16 首版踩到的三个坑（都已修，写在这里免得下次重犯）：
#    ① 标题是「【惠普暗影精灵11 i9 14900HX/RTX5070/32GB/1TB/黑色参数】」——
#       末尾带「参数」两个字，直接拿来当 name 会变成「…黑色参数」。
#    ② 硬盘只写了 `\d{1,2}\s*GB`，于是 1TB 全部漏采（首跑 5 台 storageGB 全是 None）。
#    ③ CPU 型号 `i9 14900HX` 中间有空格，`\b` 在中文标点旁失效，
#       只匹配到参数表的「英特尔 酷睿i9 14代系列」——那是**系列名**不是型号。
_TITLE = re.compile(r'<title>【([^】]{2,90})】')
_CPU_MODEL = re.compile(
    r'((?:Ultra)\s?[3579]\s?[\- ]?\s?\d{3,5}\s?[A-Z]{0,3}[HSXU]?'
    r'|U[3579][\- ]?\d{3,5}\s?[A-Z]{0,3}[HSXU]?'
    r'|(?:锐龙|Ryzen)\s?[3579]\s?[\- ]?\s?\d{3,5}\s?[A-Z]{0,3}[HSXU]?'
    r'|R[3579][\- ]?\d{4,5}\s?[A-Z]{0,3}[HSXU]?'
    r'|i[3579][\- ]?\d{4,5}\s?[A-Z]{0,3}[HSXU]?'
    r'|M[1-9]\s?(?:Pro|Max|Ultra)?)')
_RAM = re.compile(r'(\d{1,2})\s*GB\b', re.I)
# 硬盘是 256/512/1024GB 这种三到四位数，用 1-2 位的 _RAM 匹配不到 ——
# 首版删掉这条之后，512GB 的机器 storageGB 全变 None。
_STOR_GB = re.compile(r'(\d{3,4})\s*GB\b', re.I)
_STOR_TB = re.compile(r'(\d(?:\.\d)?)\s*TB\b', re.I)

# 显卡型号：**参数表里没有**，只在页面标题的型号串里
# （实测参数表只有「显存类型/显存位宽/显卡最高功耗」，没有型号本身）。
# 没有这一项，游戏本就没法判品类、也没法做性能评估 —— 所以必须从标题补。
_GPU = re.compile(
    r'(RTX\s?\d{4}\s?(?:Ti|Super)?|GTX\s?\d{4}\s?(?:Ti)?'
    r'|Arc\s?[A-Z]?\s?\d{3}|RX\s?\d{4}\s?(?:M|S|XT|GRE)?'
    r'|核显|集显|独显)', re.I)
# 🔴 分辨率有两条路，都不在参数表里：
#    ① product-link 的 title 属性（少数页面有）
#    ② **筛选链接**：`<a href='/notebook_index/subcate16_list_p23549_1.html'>2560x1600</a>`
#       这是 ZOL 的参数筛选入口，绝大多数页面走这条。
#    首版只写了 ①，于是 6 台里 4 台分辨率是 None。
_RES_FILTER = re.compile(r'list_p\d+_1\.html["\']?\s*>(\d{3,4})\s*[x×]\s*(\d{3,4})<')
_RES = re.compile(r'title=["\']?(\d{3,4})\s*[x×]\s*(\d{3,4})')
# 参数表里如果有分辨率，用它兜底（比 product-link 更稳）
_RES_LABEL = re.compile(r'<span id="newPmName_\d+">屏幕分辨率</span>.*?<span id="newPmVal_\d+">([^<]+)<', re.S)


def parse_title(html):
    """从标题串里抽型号配置。

    例：`惠普暗影精灵11 i9 14900HX/RTX5070/32GB/1TB/黑色参数`
        → name=惠普暗影精灵11, cpu=i9 14900HX, ramGB=32, storageGB=1024
    """
    m = _TITLE.search(html)
    if not m:
        return {}
    title = m.group(1).strip()
    out = {"titleRaw": title}
    # name：去掉尾部「参数」和括号外的杂字
    nm = re.sub(r'(参数|报价|图片|论坛)$', '', title).strip()
    # 型号串是从第一个包含数字斜杠的片段开始的，前面是产品名
    nm = re.split(r'\s+(?=[A-Za-z]*\d)', nm)[0].strip()
    out["name"] = nm or None
    cm = _CPU_MODEL.search(title)
    if cm:
        out["cpu"] = _WS.sub(" ", cm.group(1)).strip()
    # 内存 / 硬盘：内存是 8/16/32/64，硬盘是 256/512/1024GB 或 1TB
    gbs = [int(x) for x in _RAM.findall(title)]
    sgb = [int(x) for x in _STOR_GB.findall(title)]
    tbs = [float(x) for x in _STOR_TB.findall(title)]
    if gbs:
        mem = [g for g in gbs if 4 <= g <= 128]
        if mem:
            out["ramGB"] = max(mem)
    if sgb:
        out["storageGB"] = max(sgb)
    elif tbs:
        out["storageGB"] = int(max(tbs) * 1024)
    # 显卡：标题里抓。抓不到不是错误（轻薄本可能只写「核显」或干脆不写）
    gm = _GPU.search(title)
    if gm:
        out["gpu"] = _WS.sub(" ", gm.group(1)).upper().replace(" ", "")
    return out


# ── 价格：ZOL 参数页**聚合同一台机器的各平台价** ──────────────────
# 实测 2026-11-16 的真实结构（表格两列，平台名和价格分开）：
#     <a id="param-list-b2c-jd" ...>京东商城</a> <p class="info-link">www.jd.com</p>
#     </div> <div class="price-cell"><span class="price"> ￥13198
#     <a ...>天猫商城</a> <p class="info-link">www.tmall.com</p>
#     </div> <div class="price-cell"><span class="price"> ￥13999
#
# 🔴 这里有一个**必须避开的陷阱**（首版差点踩进去）：
#    页面下方「相关产品」区也有「参考价：￥16999」，但它前面挂的是别的机型
#    （实测是 ROG 幻X 2025、惠普暗影精灵 MAX）。用「参考价」全局搜索，
#    会把这些**竞品的价格**当成本机的价写进库 —— 那是最坏的一类数据错误：
#    数字看着像真的，来源却是另一台机器。
#    ⇒ 只认「平台域名 + 紧随其后的 price」这一种结构，且限制跨度；
#      不用全局的「参考价：」搜索。
#
# ⚠️ 口径：这是 ZOL 抓的**快照价**，不含国补/平台券/Plus 价，
#    不等于京东实时到手价。所以分成 priceJD / priceTmall 独立字段，
#    **绝不写进 `price`** —— `price` 在本项目里唯一含义是"到手价"。
_PRICE_JD = re.compile(
    r'www\.jd\.com</p>.{0,400}?<span class="price">\s*[￥¥]\s*([\d,]{3,7})', re.S)
_PRICE_TM = re.compile(
    r'www\.tmall\.com</p>.{0,400}?<span class="price">\s*[￥¥]\s*([\d,]{3,7})', re.S)


def _price(pat, html):
    """取第一个价格并转 int。取不到返回 None —— 不许猜一个像样的数。"""
    m = pat.search(html)
    if not m:
        return None
    try:
        v = int(m.group(1).replace(",", ""))
    except ValueError:
        return None
    # 300~60000 之外的一律当解析错误（可能是型号里的数字被误抓）
    return v if 300 <= v <= 60000 else None


def parse_params(html):
    """解析参数表 → {参数名: 值}。"""
    got = {}
    for name, block in _PARAM.findall(html):
        n = _clean(name)
        v = _clean(block)
        if n and n not in got:
            got[n] = v
    return got


def _num(s, pat):
    m = re.search(pat, s or "")
    return m.group(1) if m else None


def to_record(cat, pid, html, url):
    """把 38 项参数映射成本项目用的字段。

    ⚠️ 每个字段映射都写清来源，不靠猜。拿不准就留 None ——
       本项目宪法第 2 条：没数据 ≠ 有问题，宁可空着也不许填一个像样的假值。
    """
    p = parse_params(html)
    t = parse_title(html)
    # 分辨率优先参数表 → 再退筛选链接 → 最后退 product-link
    res_label = _RES_LABEL.search(html)
    res_f = _RES_FILTER.search(html)
    res_t = _RES.search(html)
    screen_res = None
    if res_label:
        screen_res = _clean(res_label.group(1))[:16] or None
    if not screen_res and res_f:
        screen_res = "%s×%s" % res_f.groups()
    if not screen_res and res_t:
        screen_res = "%s×%s" % res_t.groups()

    rec = {
        "source": "zol",
        "sourceId": pid,
        "sourceUrl": url,
        "cat": {"notebook": None, "desktop_pc": "desktop",
                "cell_phone": "phone", "memory": "upgrade"}.get(cat),
        "capturedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        # 名称：优先标题解析出的产品名（已去掉型号串和「参数」尾巴），退型号
        "name": t.get("name") or p.get("产品型号"),
        "brand": (t.get("name") or "")[:2] if t.get("name") else None,
        # CPU：参数表的「CPU型号」最准（台式机有、笔记本没有），
        #      标题解析次之，「CPU系列」是最粗的一层（只有"酷睿i9 14代"这种）
        "cpu": p.get("CPU型号") or t.get("cpu") or p.get("CPU系列"),
        "gpu": t.get("gpu") or p.get("显卡型号"),
        "ramGB": t.get("ramGB"),
        "storageGB": t.get("storageGB"),
        "ramType": p.get("内存类型"),
        "storageType": p.get("硬盘描述"),
        "screenSize": _num(p.get("屏幕尺寸"), r'(\d{1,2}(?:\.\d)?)\s*英寸'),
        "screenPanel": p.get("屏幕类型"),
        "screenHz": _num(p.get("屏幕刷新率"), r'(\d{2,3})\s*Hz'),
        "screenRes": screen_res,
        "gamut": p.get("DCI-P3色域") or p.get("sRGB色域"),
        "brightness": p.get("亮度"),
        "batteryWh": _num(p.get("电池类型"), r'(\d{2,3})\s*瓦时'),
        "adapterW": _num(p.get("电源适配器"), r'(\d{2,3})\s*W'),
        "weightKg": _num(p.get("笔记本重量"), r'(\d+(?:\.\d+)?)\s*Kg'),
        "thicknessMm": _num(p.get("厚度"), r'(\d+(?:\.\d+)?)\s*mm'),
        "keyboardBacklit": ("是" if "背光" in (p.get("键盘描述") or "") else None),
        "faceId": ("是" if "支持" in (p.get("人脸识别") or "") else None),
        "milSpec": ("是" if "MIL-STD" in (p.get("安全性能") or "") else None),
        "portsVideo": p.get("视频接口"),
        "portsAudio": p.get("音频接口"),
        "os": p.get("操作系统"),
        "launchDate": p.get("上市时间"),
        "positioning": p.get("产品定位"),
        # 价格：两路分开存，口径不同不许混（见 _PRICE_JD 处注释）
        "priceJD": _price(_PRICE_JD, html),
        "priceTmall": _price(_PRICE_TM, html),
        "priceBasis": "zol聚合快照（非实时到手价，不含国补/券）",
        "_raw": p,
    }
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", default="notebook", choices=list(CATS) + list(FLAT_CATS))
    ap.add_argument("--pages", type=int, default=1, help="列表页翻几页（每页约75个id）")
    ap.add_argument("--limit", type=int, default=0, help="最多采几台，0=不限")
    ap.add_argument("--delay", type=float, default=1.2, help="每次请求间隔秒（下界）")
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(
        DATA_DIR, "%s-%s.jsonl" % (args.cat, datetime.now().strftime("%Y-%m-%d")))

    # 断点续采：已采过的 id 跳过，不重复请求
    done = set()
    if os.path.exists(out_path):
        for line in open(out_path, encoding="utf-8"):
            try:
                done.add(json.loads(line)["sourceId"])
            except Exception:
                pass
        print("已存在 %d 条，跳过它们" % len(done))

    ids = []
    for pg in range(1, args.pages + 1):
        try:
            got = list_ids(args.cat, pg)
            print("列表页 %d: %d 个 id" % (pg, len(got)))
            ids += [x for x in got if x not in ids]
        except Exception as e:
            print("列表页 %d 失败: %s" % (pg, e))
        time.sleep(random.uniform(args.delay, args.delay + 0.8))

    todo = [i for i in ids if i not in done]
    if args.limit:
        todo = todo[:args.limit]
    print("待采 %d 台（列表共 %d，已采 %d）" % (len(todo), len(ids), len(done)))

    ok = fail = 0
    fh = open(out_path, "a", encoding="utf-8")
    try:
        for n, pid in enumerate(todo, 1):
            url = param_url(pid)
            try:
                html = fetch(url)
                rec = to_record(args.cat, pid, html, url)
                if not rec.get("name"):
                    print("  [%d/%d] %s 无名称，跳过" % (n, len(todo), pid))
                    fail += 1
                    continue
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                ok += 1
                print("  [%d/%d] %-42s cpu=%-16s %sG/%sG %s %sKg"
                      % (n, len(todo), (rec["name"] or "")[:40],
                         (rec["cpu"] or "-")[:16], rec.get("ramGB") or "-",
                         rec.get("storageGB") or "-",
                         (rec.get("screenRes") or "-"),
                         rec.get("weightKg") or "-"))
            except Exception as e:
                fail += 1
                print("  [%d/%d] %s 失败: %s" % (n, len(todo), pid, e))
            time.sleep(random.uniform(args.delay, args.delay + 0.8))
    finally:
        fh.close()

    print("\n完成：成功 %d / 失败 %d" % (ok, fail))
    print("输出：%s" % out_path)


if __name__ == "__main__":
    main()
