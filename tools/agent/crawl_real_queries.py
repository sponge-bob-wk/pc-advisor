# -*- coding: utf-8 -*-
"""crawl_real_queries.py —— 从公开网页捞「真实用户选机提问」，产出待标注语料。

为什么做这个
    现有 route-train.jsonl 1381 条全是合成/改写句，而且 100% 带同一台焦点机器
    （"（当前讨论：zol-2103842）"）。真实用户第一句话是**没有焦点**的，模型
    从没见过 "（当前没有在讨论任何机器）" —— 这是结构性缺口，得用真话补。

数据从哪来
    直连贴吧/知乎/ZOL 详情页都被防刷挡（JS 挑战 / 滑块 / 登录墙），
    但 360 搜索的 site: 限定查询能正常返回这些站的**帖子标题**。
    而贴吧帖子标题本身就是用户的原话（"预算5000配个电脑求大神配置"）。
    所以走「搜索引擎 → 标题」这条路，比硬爬省事，也不会拿到被注入干扰的文本。

产出
    lora_data/crawl/raw-YYYYMMDD.jsonl    {text, site, url, query}
    lora_data/crawl/raw-YYYYMMDD.txt      人眼过一遍用

用法
    python crawl_real_queries.py --pages 3
"""
import argparse
import io
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "lora_data", "crawl")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ---------------------------------------------------------------- 站点 × 关键词
# site 限定：只在「有人真的在问选机问题」的站里搜
SITES = [
    "tieba.baidu.com",       # 配电脑吧/配置吧/显卡吧/笔记本吧 —— 密度最高
    "zhidao.baidu.com",      # 百度知道 —— 整句提问
    "zhihu.com",             # 知乎问题标题
    "bbs.zol.com.cn",        # ZOL 论坛
    "post.smzdm.com",        # 什么值得买
    "bbs.nga.cn",            # NGA 硬件区
    "chiphell.com",          # Chiphell
    "bbs.hupu.com",          # 虎扑数码
]

QUERIES = [
    # 预算 + 用途（最高频，对应 state_new_need / state_range）
    "预算 配电脑 求配置", "预算 装机 配置单", "五千 配电脑", "六千 主机 配置",
    "八千 配电脑 打游戏", "一万 电脑配置", "三千 装机",
    # 笔记本
    "笔记本 推荐 预算", "游戏本 推荐 大学生", "轻薄本 推荐 办公",
    "笔记本 怎么选 小白", "笔记本 5000 推荐",
    # 用途
    "剪辑 电脑配置 推荐", "建模 电脑 配置", "深度学习 显卡 配置",
    "编程 笔记本 求推荐", "设计专业 笔记本 推荐",
    # 问当前这台（ask_current）
    "这套配置 怎么样", "配置单 求点评", "这个配置 有坑吗", "这套配置 能玩",
    "笔记本 散热 怎么样", "这台 值不值",
    # 换台 / 否定（refuse_switch）
    "不要 联想 求推荐", "不想 用 这个 牌子 换",
    # 统计 / 对比（analyze）
    "哪个牌子 笔记本 好", "性价比最高 游戏本", "最便宜 游戏本",
    "显卡 对比 选哪个", "cpu 对比 选哪个",
    # 加条件（state_new_need 的追加句）
    "要 独显 预算", "不要 独显 办公", "内存 32g 够用吗", "固态 1t 够吗",
]


# ---------------------------------------------------------------- 第二轮：贴吧原生说法
# 第一轮用「空格分隔的 AND 查询」，后 5 个站全 0 —— 360 只稳定索引了
# tieba / zhidao / zhihu 三个站，而且多词 AND 会把结果卡到没有。
# 第二轮改成**用户真会打出来的整句**，少用空格，只打这三个站。
QUERIES_V2 = [
    "求个配置单", "求配置单大佬", "想配台电脑", "配台电脑大概多少钱",
    "装机配置单推荐", "小白求配置", "萌新求配置", "预算不够怎么配",
    "笔记本求推荐", "买什么笔记本好", "大学生买什么笔记本",
    "游戏本怎么选", "轻薄本和游戏本怎么选", "笔记本买多大内存",
    "这配置能玩吗", "这个配置合理吗", "配置有没有问题",
    "这套配置值不值", "求大佬看看配置", "配置单求修改",
    "显卡怎么选", "显卡选哪个好", "cpu和显卡怎么搭配",
    "剪视频要什么配置", "做设计买什么电脑", "学计算机买什么笔记本",
    "跑代码用什么笔记本", "打游戏配什么电脑",
    "电脑多少钱一台", "组装一台电脑要多少钱", "自己装机难吗",
    "台式机和笔记本哪个好", "买整机还是自己装",
]


# ---------------------------------------------------------------- 第三轮：补薄弱意图
# 前两轮 90% 落在 state_new_need —— 因为论坛标题天然是"求配置"。
# 这一轮专门去捞 analyze / refuse_switch / state_range 的真人说法。
QUERIES_V3 = [
    # analyze：数量/排行/极值
    "游戏本排行榜", "笔记本销量排行", "最贵的笔记本", "最便宜的笔记本",
    "哪个牌子笔记本最多", "笔记本性价比排行", "显卡性能排名",
    "有多少款游戏本", "笔记本平均价格",
    # refuse_switch：明确排除
    "不要联想买什么", "不想买华为笔记本", "除了苹果还有什么", "换掉这个牌子",
    "不要这个牌子推荐", "不想用AMD",
    # state_range：只报价位
    "六七千的笔记本", "一万左右的电脑", "两千到三千的电脑", "三千五左右",
    "五千上下的台式",
    # 换机/低预算/身份场景（补真实说法，不补意图）
    "一千块能配什么电脑", "二手笔记本值得买吗", "电脑太卡了想换",
    "医学生买什么笔记本", "会计专业买什么电脑", "建筑学笔记本推荐",
    "考研买什么笔记本", "上网课买什么电脑", "女生轻薄本推荐",
    "背去图书馆的笔记本", "笔记本多重合适",
]

# ---------------------------------------------------------------- 第四轮：拉时效
# 前三轮抓回来一堆 2018-2023 的老帖（"GTX960 配什么"）。
# 这一轮只打近两年的硬件名词和说法，把语料的时效拉回来。
QUERIES_V4 = [
    "2026装机配置单", "2026配电脑推荐", "5060装机配置", "5060ti配置单",
    "5070游戏本推荐", "5080装机", "锐龙9000装机", "9700x配置",
    "国补笔记本推荐", "国补后买笔记本", "现在装机合适吗", "现在配电脑划算吗",
    "ddr5装机", "2k显示器推荐", "4k显示器推荐", "itx小机箱装机",
    "2026游戏本推荐", "2026轻薄本推荐", "开学季笔记本推荐", "双十一装机",
]


# ---------------------------------------------------------------- 过滤
DOMAIN = re.compile(
    r"电脑|笔记本|台式|主机|整机|一体机|游戏本|轻薄本|工作站|装机|配置|"
    r"显卡|CPU|处理器|内存|固态|硬盘|显示器|主板|电源|散热|机箱|独显|核显|"
    r"rtx|gtx|锐龙|酷睿|i5|i7|i9|r5|r7|macbook|thinkpad|拯救者|天选|暗影精灵",
    re.I)

BUY = re.compile(
    r"配|装机|预算|推荐|选|买|换|多少钱|值得|性价比|求|怎么样|行不行|够不够|"
    r"合适|划算|多少|贵|便宜|对比|哪个|有没有|要不要|该不该|建议|纠结|犹豫|"
    r"能玩|带得动|跑得动|够用",
    re.I)

# 售后/教程/广告 —— 混进来会把标签带歪
DROP = re.compile(
    r"蓝屏|开不了机|无法开机|重装系统|装系统|驱动|教程|下载安装|报错|黑屏|"
    r"死机|中毒|杀毒|维修|拆机|清灰|激活|破解|注册表|怎么设置|密码|忘记|"
    r"回收|二手出|出售|转让|招聘|兼职|代理|加盟|招商|批发|采购|报价单|"
    r"参数|评测|开箱|图赏|排行榜|资讯|新闻|发布会|驱动下载",
    re.I)

# 站点后缀清洗
SUFFIX = [
    re.compile(r"【[^】]{1,12}吧】[_\-\s]*百度贴吧.*$"),
    re.compile(r"[_\-\s]*百度贴吧.*$"),
    re.compile(r"[_\-\s]*百度知道.*$"),
    re.compile(r"[_\-\s]*知乎.*$"),
    re.compile(r"[_\-\s]*(?:ZOL|中关村在线).*$"),
    re.compile(r"[_\-\s]*NGA玩家社区.*$"),
    re.compile(r"[_\-\s]*Chiphell.*$"),
    re.compile(r"[_\-\s]*虎扑.*$"),
    re.compile(r"[_\-\s]*什么值得买.*$"),
    # 360 结果里贴吧后缀常被截断成 "_电脑吧" / "_配置吧"
    re.compile(r"_[^_\s]{1,10}吧\s*$"),
    re.compile(r"^\s*【[^】]{1,12}】\s*"),
    re.compile(r"#[^#]{1,12}#"),          # 话题标签
]

MIN_LEN, MAX_LEN = 5, 60


def clean_title(s):
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
          .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">"))
    s = re.sub(r"\s+", " ", s).strip()
    for p in SUFFIX:
        s = p.sub("", s).strip()
    return s.strip(" _-|·、,，")


def keep(t):
    if not (MIN_LEN <= len(t) <= MAX_LEN):
        return False
    if not DOMAIN.search(t):
        return False
    if not BUY.search(t):
        return False
    if DROP.search(t):
        return False
    if re.match(r"^[\sA-Za-z0-9\-+/\.]+$", t):     # 纯型号
        return False
    return True


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=25) as r:
                html = r.read().decode("utf-8", "ignore")
            if len(html) < 8000 and "验证" in html:
                raise RuntimeError("blocked")
            return html
        except Exception as e:                     # noqa: BLE001
            last = e
            time.sleep(2.0 * (i + 1))
    raise last


# ---------------------------------------------------------------- 源：360 搜索
def crawl_so_search(sites, queries, pages, want):
    got, seen = [], set()
    for site in sites:
        for q in queries:
            if len(got) >= want:
                return got
            for pn in range(1, pages + 1):
                if len(got) >= want:
                    return got
                full = "site:%s %s" % (site, q)
                url = "https://www.so.com/s?q=%s&pn=%d" % (
                    urllib.parse.quote(full), pn)
                try:
                    html = fetch(url)
                except Exception as e:             # noqa: BLE001
                    print("    ! %s / %s p%d %s" % (site, q, pn, str(e)[:30]))
                    break
                hits = re.findall(
                    r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
                n = 0
                for href, raw in hits:
                    t = clean_title(raw)
                    if not t or not keep(t):
                        continue
                    key = re.sub(r"[\s，。？！,.?!、]", "", t)
                    if key in seen:
                        continue
                    seen.add(key)
                    got.append({"text": t, "site": site, "url": href[:200],
                                "query": q})
                    n += 1
                print("    %-18s %-22s p%d  +%-3d (累计 %d)"
                      % (site, q[:20], pn, n, len(got)))
                time.sleep(random.uniform(1.2, 2.2))
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--max", type=int, default=1200)
    ap.add_argument("--sites", default="", help="逗号分隔，只跑指定站")
    ap.add_argument("--preset", default="v1", choices=("v1", "v2", "v3", "v4"),
                    help="v1=站内 AND；v2=贴吧原生；v3=补薄弱意图；v4=拉时效")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    out = args.out or os.path.join(OUT_DIR, "raw-%s.jsonl" % stamp)

    sites = args.sites.split(",") if args.sites else SITES
    queries = {"v1": QUERIES, "v2": QUERIES_V2, "v3": QUERIES_V3,
               "v4": QUERIES_V4}[args.preset]
    if args.preset in ("v2", "v3", "v4") and not args.sites:
        sites = ["tieba.baidu.com", "zhidao.baidu.com", "zhihu.com"]
    print("=" * 72)
    print("真实提问采集 · %d 站 × %d 关键词 × %d 页 (preset=%s)" %
          (len(sites), len(queries), args.pages, args.preset))
    print("=" * 72)

    rows = crawl_so_search(sites, queries, args.pages, args.max)

    with io.open(out, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    txt = os.path.splitext(out)[0] + ".txt"
    with io.open(txt, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write("%s\t%s\n" % (r["text"], r["site"]))

    print("\n" + "=" * 72)
    print("共 %d 条" % len(rows))
    print("JSONL %s" % out)
    print("TXT   %s" % txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
