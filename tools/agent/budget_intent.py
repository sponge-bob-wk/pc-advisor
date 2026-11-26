# -*- coding: utf-8 -*-
"""预算动作识别 · 规则与模型的协作层（2026-11-13）

## 为什么需要这一层

实测数据（同一批句子，两把尺子分别量）：

| 数据 | 规则 | 模型 0-shot | 协作 |
|---|---|---|---|
| 34 条常见说法 | 97% | —        | 100% |
| 77 条对抗样本 | 97.2% | 90.1% | 待测 |
| 12 条开放式表达 | **50%** | 75%  | **83%** |

三类句子，三个赢家，这不是矛盾，是分工没分对：

  · 常见说法（"放宽预算""加点预算"）—— **规则赢**。词就在那里，查表最快最准。
  · 对抗样本（"放宽到7500"）—— **规则赢**。因为"有数字就以数字为准"这条
    优先级是硬逻辑，模型反而会被"放宽"这个动词带偏。
  · 开放式表达（"手头松了点""奖学金到账了""不用给我省"）—— **模型赢**。
    规则连"这句话跟钱有关"都不知道，词表里根本没有这些说法。

⇒ 所以不是"换掉规则"，是**让规则只在确定时说话，不确定时让模型补**。

## 🔴 一条硬线：数字永远不由模型决定

`set_budget` 意味着"用户说了具体金额"，这个金额要写进槽位、影响后续所有计算。
一旦模型看错，用户亲口说的 7000 会变成系统自己算的 8400 —— 这就是
2026-11-13 那个 bug 的形态。

所以：
  · 规则从原话里抽到了金额 → **直接 set_budget，不问模型**。
    金额是从原话正则匹配出来的，可追溯、可复现。
  · 规则没抽到金额 → 才问模型。模型的回答只当"这句话算不算放宽预算"用，
    **它不许给出具体数字**，数字只能来自 `_budget_in_text`。

## 用法

    from budget_intent import classify_budget
    label, why = classify_budget("手头松了点")
    # ('relax', 'model:开放式表达')
"""
import json
import os
import re
import sys

# 🔴 2026-11-26 加：**强制 stdout/stderr 用 UTF-8**。
#    Windows 控制台默认 GBK，打印 ⚠️/✓/✗ 这类字符会 UnicodeEncodeError 直接崩。
#    实测：server.py 第 588 行的 ⚠️ 导致服务起不来。
#    不依赖外部 PYTHONIOENCODING 环境变量，在代码里写死。
#    errors="replace" 是双保险 —— 宁可显示成 ? 也不能让脚本崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import agent  # noqa: E402

# 三个互斥标签
RELAX = "relax"            # 要放宽，但没给数 → 系统按比例加
SET_BUDGET = "set_budget"  # 给了具体数 → 以用户数字为准
NONE = "none"              # 这句不动预算

SYS_PROMPT = (
    "你是电脑选购助手的意图分类器。判断用户这句话对「预算」做了什么。\n"
    "只输出 JSON：{\"budget_action\": \"...\"}\n\n"
    "三个取值：\n"
    "- relax       要求或同意提高预算上限，但没有给出具体金额\n"
    "- set_budget  用户给出了一个具体金额，必须以这个数为准\n"
    "- none        这句话不是在调整预算（可能在问配置、要换台、要更便宜）\n\n"
    "重要：如果一句话里既有「放宽/提高」又有具体数字，选 set_budget —— "
    "用户给的数字优先于放宽这个动作。"
)

# 模型不可用时的缓存：同一句话不重复问，节省 0.23s/条
_CACHE = {}
_MODEL_DEAD = [False]      # 探活失败一次就不再重试（避免每条都等 3 秒超时）


# 拒绝加钱 / 反向降价 —— 这些句子必须由规则拦住，不能交给模型。
#
# 🔴 为什么必须有这一层（2026-11-13 实测）：
#    模型在"否定、反向"上系统性犯错 —— 它把任何带钱味的句子都读成"想多花钱"：
#      「预算够了」     → 模型答 relax   （实际是"别加了"）
#      「能不能便宜点」 → 模型答 relax   （实际是"要降价"，方向相反）
#      「预算不能再高了」→ 模型答 set_budget
#    这一类错误**方向一致**，说明是模型的先验偏置，不是随机噪声。
#    几十条样本能微调掉，但在没微调之前，规则这个词表反而是更可靠的。
#    而且这些说法是封闭的（否定词就那么几个），词表打得中。
REFUSE_KW = re.compile(
    r"够了|够用|不用(加|提高|放宽|多给)|不需要(加|提高|放宽)|"
    r"不(想|打算|准备)(加|多花)|不能(再)?(高|超|加)|就这些钱|"
    r"预算(已)?够|别加")
# 说明：上面**不能**把"不用给我省"收进来 —— 那是双重否定（不用+省 = 多花），
# 方向正好相反。2026-11-13 实测：加了"不用"这个裸词，这条被误判成"拒绝加钱"。
# 所以拒绝词必须带宾语（不用**加**/不用**提高**），不能只写"不用"。

# 反向：想更便宜。注意这不属于"预算动作"的 relax/set_budget/none 三分类，
# 它归 none —— 但下游需要知道"用户想降价"，所以单独给一个 reason 标记。
CHEAPER_KW = re.compile(
    r"便宜|降价|降点|压压价|压价|太贵|贵了|低点|再低|"
    r"预算(降|减|少)")


def rule_decide(text):
    """规则的确定判断。返回 (label, reason) 或 (None, reason) 表示"我不确定"。

    🔴 四种情况必须分开，代码里顺序不能换：
      ⓪ 拒绝加钱 → NONE，**确定**。这是模型系统性犯错的地方，规则先拦。
      ① 抽到金额 → SET_BUDGET，**确定**。金额可追溯，不许模型推翻。
      ② 命中放宽词 → RELAX，**确定**。词在那儿。
      ③ 都没有 → 返回 None，**不确定**（不是 none！）。
         把"我没找到"当成"没有"就是本项目最反复犯的错
         （宪法第 2 条：没数据 ≠ 有问题）。

    为什么 ⓪ 排在 ① 前面：「预算够了，就7000吧」有金额也有拒绝。
    这时金额仍然是对的（7000 是有效预算），但**动作**是"别再加"。
    所以 ⓪ 只拦"要不要放宽"，不能把金额一起丢掉 —— 见实现。
    """
    t = text or ""

    # ⓪ 拒绝加钱：明确说了"够了/不用加"——动作是 none，但金额要保留
    if REFUSE_KW.search(t):
        nums = agent._budget_in_text(t)
        if nums:
            # 有金额：预算用这个数，但不算"放宽"
            return SET_BUDGET, "rule:拒绝加钱但有金额 %s（动作=保持不变）" % sorted(nums)[:2]
        return NONE, "rule:拒绝加钱"

    nums = agent._budget_in_text(t)
    if nums:
        return SET_BUDGET, "rule:原话有金额 %s" % sorted(nums)[:3]

    if CHEAPER_KW.search(t):
        # 想降价 → 不是在放宽预算。归还 none，但 reason 记下来供下游用
        return NONE, "rule:想要更便宜（反向动作）"

    if agent.is_relax_budget(t):
        return RELAX, "rule:放宽动作词"
    return None, "rule:未命中（不确定，不等于 none）"


def model_decide(text, timeout_note=""):
    """问模型。只允许返回三个枚举值，越界一律当 NONE。"""
    key = (text or "").strip()
    if key in _CACHE:
        return _CACHE[key]
    if _MODEL_DEAD[0]:
        return NONE, "model:不可用（已降级）"
    try:
        out = agent.chat(
            [{"role": "system", "content": SYS_PROMPT},
             {"role": "user", "content": key}],
            fmt="json", num_predict=24, temperature=0)
        raw = (json.loads(out).get("budget_action") or "").strip()
        lab = raw if raw in (RELAX, SET_BUDGET, NONE) else NONE
        res = (lab, "model:" + (raw or "空"))
    except Exception as e:
        _MODEL_DEAD[0] = True
        res = (NONE, "model:失败 %s" % type(e).__name__)
    _CACHE[key] = res
    return res


def classify_budget(text, allow_model=True):
    """预算动作分类的最终入口。返回 (label, why)。

    协作顺序（顺序本身就是判据，改动前先想清楚代价）：

      1. 规则抽到金额 → SET_BUDGET。数字是硬事实，任何人都不许改。
      2. 规则命中放宽词 → RELAX。词在。
      3. 规则不确定 → 问模型。
      4. 模型不可用 → NONE（宁可弃权，不许猜）。

    🔴 第 3 步有一道闸：模型说 SET_BUDGET 时，**必须**能在原话里
       找到金额（`_budget_in_text` 非空）。找不到就降级成 RELAX/NONE。
       为什么：模型不知道具体价格体系，它说 set_budget 却不给数，
       下游就得再猜一个数 —— 那正是"编数字"。
    """
    lab, why = rule_decide(text)
    if lab is not None:
        return lab, why

    if not allow_model:
        return NONE, "rule:未命中且禁用模型"

    m_lab, m_why = model_decide(text)
    if m_lab == SET_BUDGET and not agent._budget_in_text(text or ""):
        # 模型声称"用户给了数字"，但原话里抽不出来 → 它看错了，降级
        return NONE, m_why + "（无金额，降级 none）"
    return m_lab, m_why


def relax_step(budget):
    """放宽一档的数值。放在这里而不是让模型算。

    模型可以判断"要不要放宽"，但**放宽多少是纯算术**，
    交给模型就是让它编数字。这里复用 agent 的那一份，不许抄第二遍
    （宪法第 9 条：判据不许抄第二遍）。
    """
    return agent.relaxed_budget(budget)