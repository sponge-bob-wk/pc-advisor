# -*- coding: utf-8 -*-
"""seed_scenarios.py —— 不同人群/场景的意图样本（大规模）

## 用户要求

「包括不同人的场景需求也要进行训练」
「数据的话后续需要补了就补，多补一点没问题」

## 设计思路

真实用户的说话方式差异极大：

  学生       「我是学设计的，预算五千，要带去教室」
  游戏玩家   「想玩黑悟空，八千能玩吗」
  上班族     「公司报销七千，出差要轻」
  家长       「给上大学的女儿买，别太重」
  程序员     「要跑docker和虚拟机，32G够吗」
  剪辑师     「4K剪辑，一万以内」
  老年人     「字大点，好操作的」

同一种意图，说法千差万别。所以按**人群 × 意图** 交叉生成。

## 覆盖矩阵

人群：学生 / 游戏玩家 / 上班族 / 家长代买 / 程序员 / 设计剪辑 /
      教师 / 研究生 / 自由职业 / 商务出差 / 老人 / 主播

意图：ask_current / refuse_switch / state_range / state_new_need /
      analyze / list_others / unknown

每个 (人群, 意图) 至少 3 条，重点是 state_new_need（占大头）。

## 标注纪律（这次的关键）

本文件严格按 SYS_TMPL 判据标注：
  · 问当前这台某一面      → ask_current (+topic)
  · 不要这台/换台         → refuse_switch
  · 只给价位/品类范围     → state_range
  · 加新条件(用途/预算/要求) → state_new_need
  · 聚合统计(多少台/平均/分布) → analyze
  · 极值(最便宜/最强)     → analyze（已确认 analyze 支持 top/bottom）
  · 要全部剩余清单        → list_others
  · 无意义               → unknown
"""
import io
import json
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")

OUT = r"E:\电脑涨价分析\tools\agent\lora_data\scenario-seed.jsonl"

S = []   # (user, intent, topic, cot)


def a(user, intent, topic, cot):
    S.append((user, intent, topic, cot))


# ══════════════════════════════════════════════════════════════════
# 学生
# ══════════════════════════════════════════════════════════════════
a("我是学设计的，预算五千，要带去教室", "state_new_need", "none",
  "「学设计」给专业条件 +「预算五千」+「带去教室」三个条件 → state_new_need")
a("大一新生，五千左右买个能写论文的", "state_new_need", "none",
  "「大一新生」+ 预算 + 用途 → 加条件 → state_new_need")
a("学建筑的要用CAD和SU，六千够吗", "state_new_need", "none",
  "「学建筑」+ 软件需求 + 预算 → 加条件 → state_new_need")
a("我是软件工程专业的，平时也喜欢打游戏", "state_new_need", "none",
  "专业 + 爱好都是条件 → state_new_need")
a("宿舍放得下吗，一般笔记本多大", "ask_current", "build",
  "「放得下吗」问当前这台的体积 → ask_current/build")
a("图书馆用，安静吗这台", "ask_current", "noise",
  "「安静吗」问当前这台的噪音 → ask_current/noise")
a("要背去上课，这台沉不沉", "ask_current", "weight",
  "「沉不沉」问当前这台的重量 → ask_current/weight")
a("五千到七千之间的学生本", "state_range", "none",
  "只有价位区间 + 品类 → state_range")
a("四千以内", "state_range", "none",
  "只有价位 → state_range")
a("学生党平均都买多少钱的", "analyze", "price",
  "「平均都买多少钱」是聚合 → analyze/price")
a("库里多少台是四千以下的", "analyze", "price",
  "「多少台」是计数 → analyze/price")
a("便宜点的，这个太贵了", "refuse_switch", "none",
  "「太贵了」表示不要这台 → refuse_switch")
a("同学说联想好，有联想的吗", "ask_current", "none",
  "在当前语境下问另一个品牌 → ask_current")
a("把四千到五千的机器都列出来", "list_others", "none",
  "「都列出来」要清单 → list_others")
a("随便推荐一个吧", "unknown", "none",
  "没给任何条件就要求推荐 → unknown")

# ══════════════════════════════════════════════════════════════════
# 游戏玩家
# ══════════════════════════════════════════════════════════════════
a("想玩黑神话悟空，八千能玩吗", "state_new_need", "none",
  "「想玩XX」是游戏需求 + 预算 → 加条件 → state_new_need")
a("主要打游戏，预算一万", "state_new_need", "none",
  "用途 + 预算 → 加条件 → state_new_need")
a("要能开高画质的，玩3A", "state_new_need", "none",
  "「要能开高画质」是性能要求 → 加条件 → state_new_need")
a("打游戏用的，帧数越高越好", "state_new_need", "none",
  "用途 + 性能偏好 → 加条件 → state_new_need")
a("这台玩原神怎么样", "ask_current", "config",
  "「这台玩XX怎么样」是拿用途问当前这台 → ask_current + fit")
a("它显卡什么型号", "ask_current", "config",
  "「它显卡」问当前这台的配置 → ask_current/config")
a("能不能外接显示器打游戏", "ask_current", "service",
  "「能不能外接」问当前这台接口 → ask_current/service")
a("散热压得住吗，打游戏会不会烫", "ask_current", "noise",
  "「散热压得住吗」问当前这台散热 → ask_current/noise")
a("这配置打永劫无间够不够", "ask_current", "config",
  "「这配置够不够」问当前这台的性能 → ask_current/config")
a("换个显卡好点的", "refuse_switch", "none",
  "「换个...的」是换台 → refuse_switch")
a("这个帧数不行，换一台", "refuse_switch", "none",
  "「换一台」→ refuse_switch")
a("一万到一万五的游戏本", "state_range", "none",
  "价位区间 + 品类 → state_range")
a("两万以上的", "state_range", "none",
  "价位下限 → state_range")
a("游戏本平均多重", "analyze", "weight",
  "「平均多重」聚合 → analyze/weight")
a("最强的游戏本是哪台", "analyze", "none",
  "「最强的...是哪台」极值查询 → analyze")
a("多少台能跑 60 帧以上的 3A", "analyze", "config",
  "「多少台」计数 → analyze/config")
a("其他游戏本也说说", "list_others", "none",
  "「也说说」要更多清单 → list_others")
a("还有别的游戏本吗", "refuse_switch", "none",
  "「还有别的」在已经推了之后 = 换台 → refuse_switch")
a("嗯嗯", "unknown", "none", "只有语气词 → unknown")

# ══════════════════════════════════════════════════════════════════
# 上班族 / 商务
# ══════════════════════════════════════════════════════════════════
a("公司报销七千，要能出差带的", "state_new_need", "none",
  "「公司报销七千」= 预算 +「出差带」= 便携要求 → 加条件 → state_new_need")
a("经常出差，续航要能撑一天", "state_new_need", "none",
  "「续航要撑一天」是续航要求 → 加条件 → state_new_need")
a("做PPT和表格，办公为主", "state_new_need", "none",
  "「办公为主」是用途 → 加条件 → state_new_need")
a("要能开十几个网页不卡的", "state_new_need", "none",
  "「要能开十几个网页」是性能要求 → 加条件 → state_new_need")
a("这台续航几小时", "ask_current", "battery",
  "「续航几小时」问当前这台 → ask_current/battery")
a("支持Type-C充电吗", "ask_current", "service",
  "「支持Type-C」问接口 → ask_current/service")
a("多重，每天背着", "ask_current", "weight",
  "「多重」→ ask_current/weight")
a("键盘手感怎么样", "ask_current", "keyboard",
  "「键盘手感」→ ask_current/keyboard")
a("保修几年，公司要开发票", "ask_current", "service",
  "「保修几年」→ ask_current/service")
a("这个太重了，换轻的", "refuse_switch", "none",
  "「换轻的」→ refuse_switch")
a("有更薄的吗", "refuse_switch", "none",
  "「有更...的吗」= 换台 → refuse_switch")
a("六千到九千的商务本", "state_range", "none",
  "价位 + 品类 → state_range")
a("出差用的，一万以内", "state_range", "none",
  "用途 + 价位上限 → state_range")
a("轻薄本平均续航多少", "analyze", "battery",
  "「平均续航」聚合 → analyze/battery")
a("最轻的是哪台", "analyze", "weight",
  "「最轻的是哪台」极值 → analyze/weight")
a("多少台支持雷电4", "analyze", "service",
  "「多少台」计数 → analyze/service")
a("其余的列个表", "list_others", "none",
  "「列个表」要清单 → list_others")
a("嗯", "unknown", "none", "语气词 → unknown")

# ══════════════════════════════════════════════════════════════════
# 家长代买
# ══════════════════════════════════════════════════════════════════
a("给上大学的女儿买，别太重", "state_new_need", "none",
  "「给女儿买」+「别太重」是要求 → 加条件 → state_new_need")
a("孩子上大学用，五千左右", "state_new_need", "none",
  "用户 + 预算 → 加条件 → state_new_need")
a("给孩子买，要质量好点的", "state_new_need", "none",
  "「质量好点」是要求 → 加条件 → state_new_need")
a("女生用，好看点的", "state_new_need", "none",
  "「好看点」是外观要求 → 加条件 → state_new_need")
a("这个屏幕大不大", "ask_current", "screen",
  "「屏幕大不大」问当前这台 → ask_current/screen")
a("小姑娘能拿得动吗", "ask_current", "weight",
  "「拿得动吗」问重量 → ask_current/weight")
a("结实吗，孩子用东西不爱惜", "ask_current", "build",
  "「结实吗」问做工 → ask_current/build")
a("这个太丑了", "refuse_switch", "none",
  "「太丑」不要这台 → refuse_switch")
a("换个颜色好看的", "refuse_switch", "none",
  "「换个...」→ refuse_switch")
a("三千到五千的学生机", "state_range", "none",
  "价位 + 品类 → state_range")
a("五千以内", "state_range", "none", "价位 → state_range")
a("这个价位哪个牌子靠谱", "analyze", "none",
  "「哪个牌子」分组 → analyze")
a("各品牌各多少台", "analyze", "none",
  "「各多少台」分组计数 → analyze")
a("都给我看看", "list_others", "none",
  "「都给我看看」要清单 → list_others")
a("不知道买啥", "unknown", "none", "没给条件 → unknown")

# ══════════════════════════════════════════════════════════════════
# 程序员
# ══════════════════════════════════════════════════════════════════
a("要跑docker和虚拟机，32G够吗", "state_new_need", "none",
  "「要跑docker」是用途 +「32G够吗」是配置要求 → 加条件 → state_new_need")
a("写代码用，内存要大", "state_new_need", "none",
  "用途 + 内存要求 → 加条件 → state_new_need")
a("要能编译大型项目的", "state_new_need", "none",
  "「编译大型项目」是性能要求 → 加条件 → state_new_need")
a("做AI开发，要能跑本地模型", "state_new_need", "none",
  "「跑本地模型」是用途 → 加条件 → state_new_need")
a("这台能加内存吗", "ask_current", "config",
  "「能加内存吗」问当前这台 → ask_current/config")
a("有几个USB口", "ask_current", "service",
  "「几个USB口」问接口 → ask_current/service")
a("屏幕分辨率多少", "ask_current", "screen",
  "「分辨率」→ ask_current/screen")
a("散热怎么样，跑编译会不会降频", "ask_current", "noise",
  "「会不会降频」问当前这台 → ask_current/noise")
a("这个内存不够，换32G的", "refuse_switch", "none",
  "「换32G的」是换台 → refuse_switch")
a("七千到一万的开发本", "state_range", "none",
  "价位 + 品类 → state_range")
a("一万五左右", "state_range", "none", "价位 → state_range")
a("多少台是32G内存的", "analyze", "config",
  "「多少台」计数 → analyze/config")
a("内存32G的平均多少钱", "analyze", "price",
  "「平均」聚合 → analyze/price")
a("性能最强的是哪台", "analyze", "none",
  "「最强的是哪台」极值 → analyze")
a("其他32G的也列出来", "list_others", "none",
  "「也列出来」清单 → list_others")
a("??", "unknown", "none", "只有符号 → unknown")

# ══════════════════════════════════════════════════════════════════
# 设计 / 剪辑
# ══════════════════════════════════════════════════════════════════
a("做4K剪辑，一万以内", "state_new_need", "none",
  "「4K剪辑」用途 + 预算 → 加条件 → state_new_need")
a("要色准好的，做平面设计", "state_new_need", "none",
  "「色准好」是屏幕要求 + 用途 → 加条件 → state_new_need")
a("跑PR和AE，要流畅", "state_new_need", "none",
  "软件 + 性能要求 → 加条件 → state_new_need")
a("要独显，做三维渲染", "state_new_need", "none",
  "「要独显」配置要求 → 加条件 → state_new_need")
a("这块屏幕色域多少", "ask_current", "screen",
  "「色域」问屏幕 → ask_current/screen")
a("能外接4K显示器吗", "ask_current", "service",
  "「能外接4K」问接口 → ask_current/service")
a("这台渲染视频快吗", "ask_current", "config",
  "「渲染快吗」拿用途问当前这台 → ask_current + fit")
a("屏幕太小了，换大屏的", "refuse_switch", "none",
  "「换大屏的」→ refuse_switch")
a("八千到一万二的创作本", "state_range", "none",
  "价位 + 品类 → state_range")
a("一万左右", "state_range", "none", "价位 → state_range")
a("屏幕最好的几台", "analyze", "screen",
  "「最好的几台」极值 → analyze/screen")
a("色域覆盖100%的有几台", "analyze", "screen",
  "「有几台」计数 → analyze/screen")
a("其他高色域的说说", "list_others", "none",
  "「其他...说说」清单 → list_others")
a("嗯嗯嗯", "unknown", "none", "语气词 → unknown")

# ══════════════════════════════════════════════════════════════════
# 教师 / 研究生 / 科研
# ══════════════════════════════════════════════════════════════════
a("上课要用，接投影仪方便吗", "ask_current", "service",
  "「接投影仪」问接口 → ask_current/service")
a("做科研要跑数据，内存大点", "state_new_need", "none",
  "「跑数据」+ 内存要求 → 加条件 → state_new_need")
a("写论文为主，预算四千", "state_new_need", "none",
  "用途 + 预算 → 加条件 → state_new_need")
a("要能开很多文献PDF", "state_new_need", "none",
  "「开很多PDF」是使用场景 → 加条件 → state_new_need")
a("这台够跑SPSS吗", "ask_current", "config",
  "「够跑SPSS吗」拿用途问当前这台 → ask_current + fit")
a("有HDMI吗", "ask_current", "service",
  "「有HDMI吗」问接口 → ask_current/service")
a("这个屏幕看久了累眼吗", "ask_current", "screen",
  "「累眼吗」问屏幕 → ask_current/screen")
a("换个便宜点的", "refuse_switch", "none", "「换便宜的」→ refuse_switch")
a("四千到六千", "state_range", "none", "价位区间 → state_range")
a("五千以内", "state_range", "none", "价位 → state_range")
a("多少台有独立显卡", "analyze", "config",
  "「多少台」计数 → analyze/config")
a("各品类的平均价格", "analyze", "price",
  "「平均价格」+ 分组 → analyze/price")
a("全都列出来我慢慢看", "list_others", "none",
  "「全都列出来」清单 → list_others")
a("在不在", "unknown", "none", "打招呼 → unknown")

# ══════════════════════════════════════════════════════════════════
# 自由职业 / 主播
# ══════════════════════════════════════════════════════════════════
a("要直播，得能同时开obs和游戏", "state_new_need", "none",
  "「直播」+「同时开obs和游戏」是性能要求 → 加条件 → state_new_need")
a("做短视频剪辑加配音", "state_new_need", "none",
  "用途描述 → 加条件 → state_new_need")
a("在家办公，不常带出门", "state_new_need", "none",
  "使用场景说明 → 加条件 → state_new_need")
a("这台能同时接两个显示器吗", "ask_current", "service",
  "「接两个显示器」问接口 → ask_current/service")
a("麦克风收音怎么样", "ask_current", "noise",
  "「收音」问麦克风 → ask_current/noise")
a("摄像头清晰度如何", "ask_current", "screen",
  "「摄像头」问配置 → ask_current")
a("这个不行，卡", "refuse_switch", "none",
  "「卡」不满意 → refuse_switch")
a("九千到一万三的创作本", "state_range", "none",
  "价位 + 品类 → state_range")
a("一万左右", "state_range", "none", "价位 → state_range")
a("多少台适合直播", "analyze", "config",
  "「多少台」计数 → analyze/config")
a("性能排行前十", "analyze", "none",
  "「排行」极值 → analyze")
a("剩下的都给我", "list_others", "none",
  "「都给我」清单 → list_others")
a("。。。", "unknown", "none", "只有标点 → unknown")

# ══════════════════════════════════════════════════════════════════
# 装机/台式（人群视角）
# ══════════════════════════════════════════════════════════════════
a("学生党攒机，五千能配个能打游戏的吗", "state_new_need", "none",
  "「攒机」是组装动作 + 预算 + 用途 → 加条件 → state_new_need")
a("宿舍用，组一台不打游戏的", "state_new_need", "none",
  "「组一台」是组装动作 → 加条件 → state_new_need")
a("给爸妈配台看视频的电脑", "state_new_need", "none",
  "「配台」是组装动作 → 加条件 → state_new_need")
a("想做视频剪辑，台式机能自己装吗", "state_new_need", "none",
  "「自己装」是组装动作 → 加条件 → state_new_need")
a("公司要采购十台办公主机", "state_new_need", "none",
  "「采购办公主机」是台式需求 → 加条件 → state_new_need")
a("我这台台式能加显卡吗", "ask_current", "config",
  "「我这台台式」指当前 + 问配置 → ask_current/config")
a("台式机噪音大不大", "ask_current", "noise",
  "「噪音大不大」问台式 → ask_current/noise")
a("台式机换个体积小点的", "refuse_switch", "none",
  "「换个」→ refuse_switch")
a("台式机六千到八千", "state_range", "none",
  "价位 + 品类 → state_range")
a("台式机一万以内", "state_range", "none",
  "价位 + 品类 → state_range")
a("台式机库里有多少台", "analyze", "none",
  "「多少台」计数 → analyze")
a("台式机平均多少钱", "analyze", "price",
  "「平均」聚合 → analyze/price")


def make(user, intent, topic, cot, with_cot):
    gold = {"intent": intent, "topic": topic, "fit": False}
    js = json.dumps(gold, ensure_ascii=False)
    content = ("%s。\n%s" % (cot, js)) if with_cot else js
    return {
        "messages": [
            {"role": "user", "content": user},
            {"role": "assistant", "content": content},
        ],
        "_meta": {"src": "scenario-seed", "has_cot": with_cot},
    }


def main():
    random.seed(20261128)
    rows = []
    print("=" * 76)
    print("生成人群/场景专项种子")
    print("=" * 76)
    print()

    from collections import Counter
    cnt = Counter(s[1] for s in S)

    for user, intent, topic, cot in S:
        rows.append(make(user, intent, topic, cot, random.random() < 0.45))

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_cot = sum(1 for r in rows
                if len(r["messages"][1]["content"].split("\n")) > 1)
    print("  各意图:")
    for k, v in cnt.most_common():
        print("    %-16s %3d 条" % (k, v))
    print()
    print("  合计 %d 条（带思维链 %d = %.0f%%）"
          % (len(rows), n_cot, 100.0 * n_cot / len(rows)))
    print("  写出 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
