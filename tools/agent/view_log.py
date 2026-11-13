"""看最近几轮对话发生了什么。用法：
    python view_log.py            # 最近 15 条
    python view_log.py 40         # 最近 40 条
    python view_log.py --bad      # 只看有问题的
"""
import json
import os
import sys
import datetime
import glob

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")

args = [a for a in sys.argv[1:]]
only_bad = "--bad" in args or "-b" in args
nums = [a for a in args if a.isdigit()]
limit = int(nums[0]) if nums else 15

files = sorted(glob.glob(os.path.join(LOG_DIR, "chat-*.log")))
if not files:
    print("还没有日志。先跑起来聊几句。")
    sys.exit()

rows = []
for p in files[-3:]:
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass

if only_bad:
    rows = [r for r in rows if r.get("issues") or not r.get("ok", True)]

rows = rows[-limit:]
if not rows:
    print("没有匹配的记录。")
    sys.exit()

bad = sum(1 for r in rows if r.get("issues") or not r.get("ok", True))
print("最近 %d 轮，其中 %d 轮有问题" % (len(rows), bad))
print("=" * 78)

for r in rows:
    ts = (r.get("ts") or "")[11:23]
    stage = r.get("stage") or ("ERROR" if not r.get("ok", True) else "-")
    ms = r.get("ms", "?")
    flag = "!!" if (r.get("issues") or not r.get("ok", True)) else "  "
    print("%s [%s] %-9s %5sms" % (flag, ts, stage, ms))
    print("    用户: %s" % (r.get("text") or "")[:70])
    s = r.get("slots") or {}
    s = {k: v for k, v in s.items() if v not in (None, [], "")}
    if s:
        print("    槽位: %s" % json.dumps(s, ensure_ascii=False))
    if r.get("ranked"):
        print("    排序: %s" % " | ".join("%s ¥%s" % (n, p) for n, p in r["ranked"]))
    if not r.get("ok", True):
        print("    报错: %s" % (r.get("error") or "")[:160])
        if r.get("trace"):
            print("    堆栈: %s" % r["trace"].splitlines()[-1][:160])
    if r.get("issues"):
        print("    >>> 问题: %s" % "；".join(r["issues"]))
    if r.get("reply"):
        print("    回答: %s" % r["reply"][:110].replace("\n", " "))
    print("-" * 78)
