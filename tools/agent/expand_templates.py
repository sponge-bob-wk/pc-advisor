# -*- coding: utf-8 -*-
"""expand_templates.py —— 按语义维度组合扩充路由层训练集

## 为什么不能只靠手写模板

`adversarial_harvest.py` 手写了 152 条，`harvest_from_logs.py` 从 4 天真实日志
只挖出 17 条唯一句。而路由层要 500 条才够训 —— 还差 351。

手写不能无限扩：写到 300 条之后，人会自动重复自己，泛化反而变差。
正确的扩法是**按语义维度做笛卡尔积**，让覆盖面跟着维度走，而不是跟着
写作者的记忆力走。

## 组合的纪律（这是本文档最重要的部分）

组合**不是**随机拼词。每一条组合都必须能回答：

> 这句话的正确标签，是不是由「成分」唯一决定的？

能唯一决定 → 可以组合；不能 → 不许组合，交给模板或真实数据。

举例：
    「能[跑PS]吗」        → ask_current / fit=True   ✅ 成分决定标签
    「[多重]啊」          → ask_current / weight      ✅ 成分决定标签
    「[换一台]」          → refuse_switch             ✅ 成分决定标签
    「[这个]怎么样」       → 取决于"这个"指什么        ❌ 不许组合

最后一条是首版踩过的坑：`ask_current` 的判定依赖 `focus`（当前讨论哪台），
所以**只有"省略主语但主语显然是当前这台"的形态**才能组合 ——
即「多重啊」「保修多久」这类，它们在中文里几乎只能理解成问当前这台。

## 维度

| 维度 | 取值数 | 产出标签 |
|---|---|---|
| 用途词 × 三种问法 | 10 × 3 | ask_current / fit=True |
| 追问面 × 五种问法 | 10 × 5 | ask_current / topic=X / fit=False |
| 配置项 × 加条件句式 | 12 × 3 | state_new_need / fit=False |
| 价位段形态 | ~15 | state_range |
| 换台形态 | ~15 | refuse_switch |
| 应答/空 | ~12 | unknown |

## 用法

    python expand_templates.py                  # 统计能扩多少、去重后多少
    python expand_templates.py --write          # 写 lora_data/expanded-route.jsonl
    python expand_templates.py --write --run    # 顺便跑模型标出哪些是错的
"""
import argparse
import itertools
import json
import os
import re
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "lora_data")
OUT_FILE = os.path.join(OUT_DIR, "expanded-route.jsonl")

ROWS = []


def add(text, intent, topic, fit, why, axis):
    ROWS.append({"text": text, "intent": intent, "topic": topic,
                 "fit": fit, "why": why, "axis": axis})


# ── 轴 1：用途词 × 三种问法 → ask_current / fit=True ──────────────────
# 为什么标签唯一：这三种问法的主语都是"这台"，动作都是"能不能满足用途"。
# SYS_TMPL 明确写："能跑PS吗" 是 fit=true 且 intent=ask_current。
USES = ["跑PS", "做设计", "剪视频", "编程", "跑MATLAB", "打游戏", "跑CAD",
        "做三维建模", "带得动PR", "开虚拟机", "跑Python", "做表格",
        "写代码", "跑SolidWorks", "剪辑4K", "渲染"]
ASK_FORMS = ["能{0}吗", "这台{0}够吗", "{0}行不行", "够{0}吗", "{0}带得动吗"]
for use, form in itertools.product(USES, ASK_FORMS):
    add(form.format(use), "ask_current", "none", True,
        "问这台能不能满足他的用途 → fit=true", "用途×问法")

# ── 轴 2：追问面 × 五种问法 → ask_current / topic=X / fit=False ──────
# 为什么标签唯一：省略主语的追问，中文里只能理解成问当前这台；
# 且这些问法都在问"某一面"，不是在问匹配 → fit=False。
# 依据 SYS_TMPL：「省略主语的追问（"多重啊""散热行不行"）= ask_current，topic 给对应面」
FACE_Q = {
    "service": ["保修多久", "保修几年", "售后怎么样", "品控如何", "返修率高吗",
                "质量靠谱吗", "维修方便吗", "有上门服务吗"],
    "screen": ["屏幕怎么样", "屏幕素质如何", "色域多少", "分辨率多少",
               "屏幕是OLED吗", "刷新率多少", "屏幕多大", "亮度够吗"],
    "keyboard": ["键盘手感如何", "键程多长", "键盘背光吗", "键盘会打油吗",
                 "有数字小键盘吗"],
    "noise": ["风扇吵不吵", "噪音大吗", "满载会不会很响", "待机安静吗",
              "散热怎么样"],
    "build": ["做工怎么样", "手感如何", "什么材质", "外壳是金属吗",
              "屏幕会晃吗", "转轴结实吗"],
    "weight": ["多重啊", "重不重", "沉不沉", "好不好带", "多少公斤",
               "带出去累不累"],
    "battery": ["续航多久", "电池多少", "能用几个小时", "续航怎么样",
                "充电快吗"],
    "price": ["多少钱", "现在什么价", "降了吗", "有优惠吗", "国补能补多少"],
    "config": ["什么配置", "接口有哪些", "内存多大", "硬盘多少G",
               "有雷电口吗", "支持WiFi7吗", "能加内存吗"],
    "pros_cons": ["有什么缺点", "短板是什么", "值不值", "有什么坑",
                  "毛病多吗", "后悔吗", "值得买吗"],
}
for topic, qs in FACE_Q.items():
    for q in qs:
        add(q, "ask_current", topic, False,
            "省略主语的追问，topic 指向具体面；fit=false（不是问匹配）", "追问面")

# ── 轴 3：配置项 × 加条件句式 → state_new_need / fit=False ────────────
# 为什么标签唯一：SYS_TMPL 明确「"换个32G的" = 加条件 → state_new_need
# （不是 refuse_switch）」。这三种句式都是"提要求"，不是"否掉当前这台"。
SPECS = ["32G内存", "独显", "1T硬盘", "台式", "大屏", "轻一点", "OLED屏",
         "高刷", "雷电口", "长续航", "16寸", "2K屏"]
COND_FORMS = ["换个{0}的", "要{0}的", "得有{0}"]
for spec, form in itertools.product(SPECS, COND_FORMS):
    add(form.format(spec), "state_new_need", "none", False,
        "「换个X的/要X的/得有X」是加条件，不是拒绝当前这台", "配置×加条件")

# 「我的要求是…」形态（M05 真实事故）
REQ_ITEMS = ["轻薄", "大屏", "独显", "32G内存", "长续航", "安静", "能跑设计软件",
             "不要封装内存", "屏幕好", "轻一点"]
for it in REQ_ITEMS:
    add("我的要求是%s" % it, "state_new_need", "none", False,
        "「我的要求是…」是给条件，不是追问当前这台（M05 事故）", "我的要求")

# ── 轴 4：价位段 / 品类 → state_range ────────────────────────────────
PRICE_FORMS = [
    "{0}", "{0}左右", "{0}以内", "{0}上下", "预算{0}", "预算 {0} 左右",
    "{0}到{1}", "{0}-{1}", "预算{0}到{1}", "{0}块钱", "{0}元以内",
]
PRICES = [("3000", "4000"), ("4000", "5000"), ("5000", "6000"),
          ("6000", "7000"), ("7000", "8000"), ("8000", "10000"),
          ("10000", "15000")]
for form in PRICE_FORMS:
    if "{1}" in form:
        for lo, hi in PRICES:
            add(form.format(lo, hi), "state_range", "none", False,
                "只说了价位段，没有别的读法", "价位段")
    else:
        for lo, _hi in PRICES:
            add(form.format(lo), "state_range", "none", False,
                "只说了价位段，没有别的读法", "价位段")

CAT_WORDS = ["轻薄本", "游戏本", "商务本", "台式机", "一体机", "工作站",
             "超极本", "二合一"]
for c in CAT_WORDS:
    add(c, "state_range", "none", False, "只说了品类范围", "品类")
    add("要%s" % c, "state_range", "none", False, "只说了品类范围", "品类")
    add("想买%s" % c, "state_range", "none", False, "只说了品类范围", "品类")

# ── 轴 5：换台 → refuse_switch ───────────────────────────────────────
SWITCH_FORMS = ["换一台", "换一个", "换个别的", "再换一台", "重新推荐",
                "这个不行", "不太喜欢", "太丑了", "太贵了", "算了",
                "再看看别的不", "再给我看几个", "能看看别的吗", "不合适",
                "换别的吧", "这台不要", "不要这台", "还有别的吗"]
for s in SWITCH_FORMS:
    add(s, "refuse_switch", "none", False, "不要当前这台，想换台", "换台")

# 「还有更X的吗」= 换台（4f2 事故）
BETTER = ["便宜", "轻", "强", "好", "大", "快", "省电"]
for b in BETTER:
    add("还有更%s的吗" % b, "refuse_switch", "none", False,
        "「还有更X的吗」= 当前这台不满足 → 换台（4f2 事故）", "还有更X")

# ── 轴 6：列清单 → list_others ───────────────────────────────────────
LIST_FORMS = ["剩下的都列出来", "还有几个", "都给我看看", "全部列一下",
              "把剩下的发我", "其余的都列出来", "列个清单", "都列出来看看"]
for s in LIST_FORMS:
    add(s, "list_others", "none", False, "要把剩余候选列出来", "列清单")

# ── 轴 7：应答/空 → unknown ──────────────────────────────────────────
ACKS = ["嗯", "哦", "在吗", "。。。", "?", "好", "嗯嗯", "在", "可以",
        "ok", "收到", "谢谢"]
for s in ACKS:
    add(s, "unknown", "none", False, "没有可执行的信息", "应答")

# ── 轴 8：价格位置的两条路（必须分开）────────────────────────────────
# 「这个价算什么水平」→ state_range（要代码实算排名）
# 「它保值吗」→ ask_current / price（机器属性）
for s in ["这个价算什么水平", "这价格贵不贵", "同价位比怎么样", "这个价格合理吗",
          "算便宜吗", "这价位有更好的吗"]:
    add(s, "state_range", "price", False,
        "问价格在市场上的位置 → 必须代码实算排名，不是问这台机器", "价格位置")
for s in ["它保值吗", "二手能卖多少", "残值怎么样", "几年后还值钱吗",
          "贬值快吗"]:
    add(s, "ask_current", "price", False,
        "保值/残值是机器属性，留在追问，别被价格通道抢走", "保值")

# ── 轴 9：整机泛问 → ask_current / pros_cons ─────────────────────────
for s in ["这台怎么样", "怎么样", "好用吗", "值得买吗", "评价如何",
          "口碑怎么样"]:
    add(s, "ask_current", "pros_cons", False,
        "整机泛问走 intro 引导，topic=pros_cons", "整机泛问")


def norm(s):
    return re.sub(r"[\s，。？！、；：,.?!;:]+", "", s or "").lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--run", action="store_true", help="跑模型标出哪些判错")
    args = ap.parse_args()

    # 去重（组合会撞出重复句）
    seen, uniq = set(), []
    for r in ROWS:
        k = norm(r["text"])
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    print("组合产出 %d 条 → 去重后 %d 条" % (len(ROWS), len(uniq)))
    print()
    print("按轴分布：")
    for k, v in Counter(r["axis"] for r in uniq).most_common():
        print("  %-16s %d" % (k, v))
    print()
    print("按标签分布：")
    for k, v in Counter(r["intent"] for r in uniq).most_common():
        print("  %-16s %d" % (k, v))

    if args.run:
        sys.path.insert(0, HERE)
        import intent_layer as L
        focus = "spec-legion-y9000p-2026"
        ok = err = 0
        errs = []
        for i, r in enumerate(uniq, 1):
            try:
                p = L.route(r["text"], focus)
            except Exception:  # noqa: BLE001
                continue
            good = (p.get("intent") == r["intent"]
                    and (r["intent"] != "ask_current" or r["topic"] == "none"
                         or p.get("topic") == r["topic"])
                    and bool(p.get("fit")) == bool(r["fit"]))
            if good:
                ok += 1
            else:
                err += 1
                errs.append((r, p))
            if i % 50 == 0:
                print("  … %d/%d" % (i, len(uniq)))
        print()
        print("模型正确 %d / %d = %.1f%%" % (ok, len(uniq), 100.0 * ok / max(len(uniq), 1)))
        print("判错 %d 条（这些才是训练价值最高的）" % err)
        if errs:
            print()
            print("判错样例（前 20）：")
            for r, p in errs[:20]:
                print("  %-26s 期望 %s/%s/%s → 实得 %s/%s/%s"
                      % (r["text"][:24], r["intent"], r["topic"], r["fit"],
                         p.get("intent"), p.get("topic"), p.get("fit")))

    if args.write:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            for r in uniq:
                gold = json.dumps({"intent": r["intent"], "topic": r["topic"],
                                   "fit": r["fit"]}, ensure_ascii=False)
                f.write(json.dumps({
                    "messages": [
                        {"role": "system", "content": "你是选机助手的路由器。只输出 JSON："
                                                      '{"intent":...,"topic":...,"fit":...}'},
                        {"role": "user", "content": r["text"]},
                        {"role": "assistant", "content": gold},
                    ],
                    "_meta": {"axis": r["axis"], "why": r["why"],
                              "labelBy": "维度组合（成分唯一决定标签）"},
                }, ensure_ascii=False) + "\n")
        print()
        print("写出 %d 条 → %s" % (len(uniq), OUT_FILE))
    elif not args.write:
        print()
        print("（演练模式。加 --write 写出；加 --run 跑模型看判错率）")


if __name__ == "__main__":
    main()
