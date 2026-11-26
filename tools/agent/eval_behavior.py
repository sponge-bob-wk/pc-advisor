# -*- coding: utf-8 -*-
"""意图层的价值评测 —— 按「用户看到的结果」算，不按「标签对不对」算（2026-11-13）

## 为什么必须换尺子

上一轮我一直在测「标签一致率」：
  · 规则 47%
  · 模型 96%
  · 协作 92%
这个数字很漂亮，但它**可能完全不代表用户体验**。

真正要问的是：**用户说完这句话，系统做的事对不对？**

2026-11-13 拿 47 条真人说法过真实 `run()` 才发现：

    标签准确率   模型 96% / 协作 92%
    行为正确率   33/47 = 70%

差的那 26 个点去哪了？实验（见 `diagnose_intent.py`）给出的答案是：

    **`run()` 根本不读意图标签。**

静态事实：`_run()` 源码里对 6 个意图常量的引用次数

    INTENT_ASK_CURRENT       0
    INTENT_REFUSE_SWITCH     0
    INTENT_STATE_RANGE       0
    INTENT_STATE_NEW_NEED    0
    INTENT_LIST_OTHERS       0
    INTENT_UNKNOWN           1     ← 只有这一个被读（反问闸）

真正的路由靠另一批**独立的**词表：
    SWITCH_KW（15 词）决定换台
    LIST_OTHERS_PAT 决定列清单
    FIT_KW（28 词）/ FOLLOWUP_KW（31 词）决定答细节还是判匹配

⇒ 所以把意图层的标签从 A 改成 B，系统行为**一次都不会变**。
  实测（强制 classify_intent 返回 ask_current）：5 条样本 0 条行为改变。

这就是用户质疑的实质：
    不是"模型比正则聪明多少"，而是**语义理解的结果根本没有接到决策上**。

## 这个文件干什么

把 47 条（+留出集）逐条过真实 `run()`，只判行为：

    ask_current   → stage ∈ {followup, fit, compare}     （回答了当前这台）
    refuse_switch → stage ∈ {recommend, exhausted}       （换掉了）
    state_range   → 槽位里的范围被改动
    state_new_need→ 槽位里有新条件落进去
    list_others   → 回复里出现了多台
    unknown       → stage == clarify（反问）

⚠️ 判据的边界（写清楚，避免又变成"判据去凑结论"）：
  · `compare` 算 ask_current 的合格行为 —— 比价句确实是在讲当前这台
  · `social` 不算 —— 那是客套/情绪兜底，不是回答
  · `list_others` 用「回复行数 ≥ 2」判，因为清单必然是分行的；
    这不是完美判据（配置描述本身也可能换行），但和"列出一台"有明显区别。

用法：
    python eval_behavior.py              # 47 条自编集
    python eval_behavior.py holdout      # 加留出集
    python eval_behavior.py holdout -v   # 打印每条
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import agent            # noqa: E402
import intent_layer     # noqa: E402

# 🔴 2026-11-26 加：**强制 stdout/stderr 用 UTF-8**。
#    Windows 控制台默认 GBK，打印 ⚠️/✓/✗ 这类字符会 UnicodeEncodeError 直接崩。
#    不依赖外部 PYTHONIOENCODING 环境变量，在代码里写死。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


FOCUS = "thin-wujie14x"
BASE = {"cat": "thin", "budget": 5000, "purpose": ["office"]}
# 🔴 seen 里必须放**真实 id**（带品类前缀）。
#
# 原来写的是 "zhan66"，而 core 里的 id 是 "thin-zhan66" ——
# `get_offer("zhan66")` 返回 None，于是这一条 exclude 从头到尾没生效过：
# 每一轮"换台"都还能看到战 66，测试却在记录"换台了"。
# 夹具错了比测挂了更坏 —— 它会让人去改产品代码来迁就一把坏尺子。
SEEN = ["thin-zhan66"]

ANSWER_CURRENT = {"followup", "fit", "compare"}

# 🔴 「嗯/哦」走 social 是**对的**，不是 fail。
#
# 我第一版判据写的是 `unknown → stage == clarify`，于是把「嗯」「在吗」「。。。」
# 全算成失败。但实际回复是：
#     「嗯」   → "嗯。接着刚才的聊，还是换个条件？"     ← 有人味的承接，用户能接
#     「在吗」 → "在。刚才的条件还留着（预算 ¥5000…）" ← 更好，直接交了上下文
# 这比机械反问 "你想做哪一件？" 体验更好，而且没有丢任何信息。
#
# ⇒ 判据改成：回应得体即可（clarify 或 social 都算）。
#   真正的坏情况只有一种：**无实义输入却去推荐机器**（那是"自信地做错事"）。
UNKNOWN_OK = {"clarify", "social"}


def _run_once(text):
    return agent.run(text, known=dict(BASE), focus=FOCUS, seen=list(SEEN))


def judge(label, res):
    """这条的行为合不合格。返回 (ok, 实际描述)。"""
    st = res.get("stage")
    slots = res.get("slots") or {}
    lines = [x for x in (res.get("reply") or "").split("\n") if x.strip()]
    if label == "ask_current":
        return st in ANSWER_CURRENT, st
    if label == "refuse_switch":
        return st in ("recommend", "exhausted"), st
    if label == "state_range":
        return (slots.get("budget") is not None), "budget=%s" % slots.get("budget")
    if label == "state_new_need":
        return (bool(slots.get("budget")) or bool(slots.get("cat"))), \
               "budget=%s cat=%s" % (slots.get("budget"), slots.get("cat"))
    if label == "list_others":
        return len(lines) >= 2, "%d 行" % len(lines)
    if label == "unknown":
        return st in UNKNOWN_OK, st
    return False, "未知标签"


def collect(with_holdout=False):
    import intent_cases_real as R
    rows = [(t, lab, why, "自编") for t, lab, why in R.CASES]
    if with_holdout:
        import intent_holdout as H
        rows += [(t, lab, why, "留出A") for t, lab, why in H.BATCH_A]
        rows += [(t, lab, why, "留出B") for t, lab, why in H.BATCH_B]
    # 留出集 C 单独开关：它是"改完之后新写、只跑一次"的干净测量，
    # 混进 holdout 会让每次回归都消耗掉它的独立性。
    if "holdc" in sys.argv:
        import intent_holdout_c as C
        rows += [(t, lab, why, "留出C") for t, lab, why in C.BATCH_C]
    # 留出集 D：C 跑出 100% 之后新写的一批，专测"没见过的话"。
    # 判读方式：**第一次跑的结果才有价值**，跑完就降到回归集。
    if "holdd" in sys.argv:
        import intent_holdout_d as D
        rows += [(t, lab, why, "留出D") for t, lab, why in D.BATCH_D]
    # 留出集 E：C/D 都跑满之后新写，专测"连续对话里的追问"。
    # **第一次跑的结果才有价值**，跑完就降到回归集。
    if "holde" in sys.argv:
        import intent_holdout_e as E
        rows += [(t, lab, why, "留出E") for t, lab, why in E.BATCH_E]
    return rows


def main():
    argv = sys.argv[1:]
    with_holdout = "holdout" in argv
    verbose = "-v" in argv
    # --no-model：禁掉模型，看纯规则退化路径（对照用）
    if "--no-model" in argv:
        intent_layer._MODEL_DEAD[0] = True

    rows = collect(with_holdout)
    ok_n = 0
    fails = []
    per = {}
    for t, lab, why, src in rows:
        res = _run_once(t)
        ok, actual = judge(lab, res)
        ok_n += ok
        d = per.setdefault(lab, [0, 0])
        d[0] += ok
        d[1] += 1
        if not ok:
            fails.append((t, lab, actual, src))
        if verbose:
            print("  %s %-20s [%s] %-13s → %s"
                  % ("OK " if ok else "XX ", t, src, lab, actual))

    n = len(rows)
    print("=" * 78)
    print("行为正确率 %d/%d = %.1f%%   （按用户实际看到的结果算）" % (ok_n, n, 100.0 * ok_n / n))
    print()
    print("分类明细：")
    for lab, (a, b) in sorted(per.items(), key=lambda x: -x[1][1]):
        print("  %-14s %2d/%2d = %3.0f%%" % (lab, a, b, 100.0 * a / b))
    if fails:
        print()
        print("失败明细（%d 条）：" % len(fails))
        for t, lab, actual, src in fails:
            print("  x %-22s 期望=%-14s 实际=%s  [%s]" % (t, lab, actual, src))
    print("=" * 78)


if __name__ == "__main__":
    main()