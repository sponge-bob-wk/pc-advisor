# -*- coding: utf-8 -*-
"""社交层的判定与措辞测试（2026-11-13 新增）。

## 为什么有这个文件

用户原话：「他没有情感上的交互，现在主流功能做的倒是还不错」。
翻日志坐实了 —— 真实那一轮：
    10:25  U「好的我已经了解了，谢谢」
           A「条件没改，还是刚才那台 天选6 Pro 锐龙版（到手 ¥10299）。要我做点别的吗？」
用户已经在收尾，系统却又把同一台推了一遍。

## 这层最容易出的两种错，正好是反向的

  Ⅰ **太热情**：把纯社交句当成需求，硬塞机器（就是上面那一轮）
  Ⅱ **太热络过头**：为了"有人味"开始夸人、开始评价机器 —— 那违反宪法第 7、9 条

所以下面同时测两边：
  · 该接住的接住了（招呼 / 道谢 / 纠结 / 太贵了 / 收尾）
  · 该交出去的没吞掉（"谢谢，那 6000 的呢" 里真正要办的是后半句）
  · 说出口的话里**不许有机型评价**，也不许出现库里没有的价位
"""
import sys, io, os, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent

OK = 0
BAD = 0
FAILS = []


def ck(name, cond, note=""):
    global OK, BAD
    if cond:
        OK += 1
    else:
        BAD += 1
        FAILS.append((name, note))


# ---------- 1. 该接住的 ----------
SHOULD = [
    ("你好", "greet"),
    ("您好，在吗", "greet"),
    ("谢谢", "thanks"),
    ("好的我已经了解了，谢谢", "thanks"),      # ← 日志里真实那一轮
    ("多谢，先这样", "thanks"),               # thanks 优先于 bye
    ("先这样吧，再见", "bye"),
    ("太贵了", "frustration"),
    ("这个价我买不起", "frustration"),
    ("看不懂这些参数", "frustration"),
    ("我有点纠结", "indecision"),
    ("不知道选哪个", "indecision"),
    ("随便看看", "browse"),
    ("我对电脑没概念", "browse"),
    ("你真厉害", "praise"),
    ("嗯", "smalltalk"),
]
for t, want in SHOULD:
    got = agent.classify_social(t)
    ck("识别「%s」→ %s" % (t, want), got == want, "实际 %s" % got)

# ---------- 2. 该交出去的（带任务信号的不许被社交层吃掉） ----------
SHOULD_NOT = [
    "谢谢，那 6000 的呢",          # 后半句才是正事
    "5000 的轻薄本",
    "太贵了，有没有 4000 的",       # 带预算 → 走主流程改条件
    "换一台",
    "这台多重",
    "学设计的预算 6000",
    "打游戏，八千以内",
    "你好，帮我推荐一台上课用的",
    "对比一下这两台",
    "内存能加吗",
]
for t in SHOULD_NOT:
    ck("不吞掉「%s」" % t, agent.classify_social(t) is None,
       "被误判成 %s" % agent.classify_social(t))

# ---------- 3. 措辞纪律：不评价机器、不谄媚、不造价位 ----------
BANNED = ["值得买", "性价比", "高达", "很顶", "首选", "非常适合你", "买它",
          "您真有眼光", "问得好", "眼光不错"]
FABRICATED_PRICE = re.compile(r"(4[0-9]{3}\s*[–-]\s*5[0-9]{3}|\d{4,5}\s*元?\s*(以上|以内))")

for label in ["greet", "thanks", "bye", "praise", "frustration",
              "indecision", "browse", "smalltalk"]:
    txt = agent.social_reply(label, known={"budget": 6000, "cat": "thin"},
                             last_stage="recommend")
    hit = [w for w in BANNED if w in txt]
    ck("%s 无评价/谄媚词" % label, not hit, str(hit))
    ck("%s 不编价位" % label, not FABRICATED_PRICE.search(txt),
       FABRICATED_PRICE.search(txt).group(0) if FABRICATED_PRICE.search(txt) else "")
    ck("%s 不为空且有下一步" % label, len(txt) >= 12, txt[:30])

# 共情后面必须跟动作：情绪类回复里要有"要/可/先/选/说"这类可执行词
for label in ["frustration", "indecision", "browse"]:
    txt = agent.social_reply(label, known={})
    ck("%s 给了可执行动作" % label,
       any(w in txt for w in ["你选", "我按", "说个", "定了", "我可以", "你更"]), txt[:60])

# ---------- 4. 端到端：道谢不许再推机器 ----------
r = agent.run("谢谢", known={"budget": 6000, "cat": "thin"}, focus="thin-xiaoxin16",
              last_stage="recommend")
ck("道谢 → stage=social", r.get("stage") == "social", str(r.get("stage")))
ck("道谢 → 不再给机型", not r.get("picked"), str(r.get("picked")))
ck("道谢 → 保留 focus", r.get("focus") == "thin-xiaoxin16", str(r.get("focus")))
ck("道谢 → 回复里不出现机型名", "小新" not in (r.get("reply") or ""), (r.get("reply") or "")[:60])

# 招呼：不许进 clarify 追问
r2 = agent.run("你好")
ck("招呼 → 不追问品类", r2.get("stage") == "social", str(r2.get("stage")))
ck("招呼 → 给出下一步", "预算" in (r2.get("reply") or ""), (r2.get("reply") or "")[:40])

# 分辨力对照：同一个入口，"你好"是社会句，"你好，帮我推荐一台"是任务。
# ⚠️ CI 上（GitHub Actions）没有 Ollama，这一句会走到槽位抽取 → 连不上模型。
#    判的是什么很容易忘：**它有没有被社交层吞掉**。连不上模型反而是更强的反证 ——
#    社交层根本不碰模型，能走到"需要模型"这一步就说明它没被吞。
try:
    r3 = agent.run("你好，帮我推荐一台上课用的")
    st3 = r3.get("stage")
except Exception as e:
    st3 = "exception:%s" % type(e).__name__
ck("分辨力：招呼+需求 → 不进社交层", st3 != "social", st3)

print("\n" + "=" * 70)
print("通过 %d / %d" % (OK, OK + BAD))
if FAILS:
    print("\n失败明细：")
    for n, note in FAILS:
        print("  - %s   %s" % (n, note))
else:
    print("全绿。")
print("=" * 70)
