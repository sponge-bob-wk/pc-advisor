# -*- coding: utf-8 -*-
"""verify_v5_e2e.py —— 端到端验证 v5 接进服务后的效果

## 测什么

v5 在对抗测试上 88.0%，但那是**离线测 adapter**。
这个脚本测的是**真实服务链路**：

    用户问句 → HTTP /api/chat → adapter 推理 → 意图判断 → 路由

## 分三组

  ① analyze 意图（v5 的核心改进）—— 必须判对
  ② 负例（不能被 analyze 抢走）
  ③ 常规意图（确认没退化）

## 判据

不是「输出里有 JSON 就算过」，而是**判断的 intent 对不对**。
"""
import json
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, r'E:\电脑涨价分析\tools\agent')
sys.stdout.reconfigure(encoding='utf-8')

import intent_layer as il

SYSMSG = il.SYS_TMPL.format(ctx="（当前讨论：zol-2103842）")
URL = "http://127.0.0.1:11435/api/chat"


def ask(text, np_=400):
    d = json.dumps({
        "model": "route-v5", "stream": False,
        "messages": [{"role": "system", "content": SYSMSG},
                     {"role": "user", "content": text}],
        "options": {"num_predict": np_},
    }).encode("utf-8")
    r = urllib.request.Request(URL, data=d,
                               headers={"Content-Type": "application/json"})
    t0 = time.time()
    o = json.loads(urllib.request.urlopen(r, timeout=180).read().decode())
    out = o["message"]["content"]
    # 从后往前找 JSON
    got = None
    for i in range(len(out) - 1, -1, -1):
        if out[i] == "{":
            try:
                got = json.loads(out[i:])
                break
            except Exception:
                continue
    return got, out, time.time() - t0


def main():
    PASS = FAIL = 0
    fails = []

    def ck(name, cond, detail=""):
        nonlocal PASS, FAIL
        if cond:
            PASS += 1
            print("  [OK ] %s" % name)
        else:
            FAIL += 1
            fails.append(name)
            print("  [FAIL] %s   %s" % (name, detail))

    # ── ① analyze 意图（v5 的核心改进）──
    print("\n[1] analyze 意图（v5 必须判对）")
    ANALYZE = [
        "有多少台有独显",
        "各品牌数量对比",
        "8000以内平均多重",
        "价格分布是什么样的",
        "性能最强的五台",
    ]
    for t in ANALYZE:
        try:
            got, out, dt = ask(t)
            intent = (got or {}).get("intent")
            ck("「%s」→ analyze" % t, intent == "analyze",
               "实际 %s（%.1fs）" % (intent, dt))
        except Exception as e:
            ck("「%s」→ analyze" % t, False, str(e)[:50])

    # ── ② 负例（不能被 analyze 抢走）──
    print("\n[2] 负例（不能被 analyze 抢走）")
    NEG = [
        ("哪个最便宜", "state_range"),
        ("换个平均点的", "state_new_need"),
        ("换一台", "refuse_switch"),
    ]
    for t, exp in NEG:
        try:
            got, out, dt = ask(t)
            intent = (got or {}).get("intent")
            ck("「%s」→ %s" % (t, exp), intent == exp,
               "实际 %s（%.1fs）" % (intent, dt))
        except Exception as e:
            ck("「%s」→ %s" % (t, exp), False, str(e)[:50])

    # ── ③ 常规意图（确认没退化）──
    print("\n[3] 常规意图（确认没退化）")
    NORMAL = [
        ("这机器多重", "ask_current"),
        ("好的", "unknown"),
        ("剩下的都列出来", "list_others"),
    ]
    for t, exp in NORMAL:
        try:
            got, out, dt = ask(t)
            intent = (got or {}).get("intent")
            ck("「%s」→ %s" % (t, exp), intent == exp,
               "实际 %s（%.1fs）" % (intent, dt))
        except Exception as e:
            ck("「%s」→ %s" % (t, exp), False, str(e)[:50])

    # ── 汇总 ──
    print()
    print("=" * 60)
    print("通过 %d / %d" % (PASS, PASS + FAIL))
    if fails:
        print("失败：")
        for x in fails:
            print("  - %s" % x)
    else:
        print("全绿。v5 端到端可用。")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
