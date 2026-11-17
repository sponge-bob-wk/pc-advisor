# -*- coding: utf-8 -*-
"""逐条拆解失败例：看清到底哪一行判错（诊断用，不进产品）。

用法：python diag_switch.py
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import agent
import intent_layer

FOCUS = "thin-wujie14x"
BASE = {"cat": "thin", "budget": 5000, "purpose": ["office"]}
SEEN = ["thin-zhan66"]

CASES = [
    ("要是能分期的话这台合适吗", "ask_current"),
    ("这个价有点离谱了吧", "refuse_switch"),
    ("算了不看了", "refuse_switch"),
    ("好的我知道了", "unknown"),
]

print("=" * 78)
for t, want in CASES:
    print("\n【%s】  期望=%s" % (t, want))
    # 1. 规则单判
    print("  规则 _classify_intent_rule : %s" % agent._classify_intent_rule(t, FOCUS))
    # 2. 意图层（模型协作）
    lab, why = intent_layer.classify(t, FOCUS)
    print("  意图层 classify          : %s   (%s)" % (lab, why))
    # 3. 三个前置闸
    print("  classify_social          : %s" % agent.classify_social(t))
    print("  _is_switch_request       : %s" % agent._is_switch_request(t))
    print("  refers_to_current        : %s" % agent.refers_to_current(t, FOCUS))
    print("  FIT_KW 命中              : %s" % [k for k in agent.FIT_KW if k in t])
    # 4. 真实 run
    res = agent.run(t, known=dict(BASE), focus=FOCUS, seen=list(SEEN))
    print("  → stage=%s" % res.get("stage"))
    print("  → reply=%s" % (res.get("reply") or "").replace("\n", " / ")[:110])
print("=" * 78)