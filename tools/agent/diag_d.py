# -*- coding: utf-8 -*-
"""留出集 D 的 6 条失败例逐条拆解（诊断用）"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import agent
import intent_layer

FOCUS = "thin-wujie14x"
BASE = {"cat": "thin", "budget": 5000, "purpose": ["office"]}
SEEN = ["thin-zhan66"]

CASES = [
    ("你还有别的路吗", "refuse_switch"),
    ("就这些？", "list_others"),
    ("我不想花超过六千", "state_range"),
    ("这个屏幕不行，换个好点的", "refuse_switch"),
    ("你看着办吧", "unknown"),
    ("随便吧", "unknown"),
]

for t, want in CASES:
    print("\n" + "=" * 72)
    print("【%s】  期望=%s" % (t, want))
    print("  意图层        : %s" % (intent_layer.classify(t, FOCUS),))
    print("  规则          : %s" % agent._classify_intent_rule(t, FOCUS))
    print("  social        : %s" % agent.classify_social(t))
    print("  _is_switch    : %s" % agent._is_switch_request(t))
    print("  refers_current: %s" % agent.refers_to_current(t, FOCUS))
    print("  LIST_OTHERS   : %s" % bool(agent.LIST_OTHERS_PAT.search(t)))
    print("  _budget_in_text: %s" % agent._budget_in_text(t))
    print("  is_relax      : %s" % agent.is_relax_budget(t))
    print("  FIT 命中      : %s" % [k for k in agent.FIT_KW if k in t])
    r = agent.run(t, known=dict(BASE), focus=FOCUS, seen=list(SEEN))
    print("  → stage=%s" % r.get("stage"))
    print("  → %s" % (r.get("reply") or "").replace("\n", " / ")[:110])