# -*- coding: utf-8 -*-
"""冒烟测试：直接以 stdio 拉起 computer-use-mcp，握手并列出工具。"""
import json, subprocess, sys, time, threading

CLI = r"E:\trae\Trae CN\resources\app\extensions\byted-solo.builtin-mcp\node_modules\@byted-solo\computer-use-mcp\switch\cli.mjs"

p = subprocess.Popen(
    ["node", CLI],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", bufsize=1,
)

msgs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "probe", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]
for m in msgs:
    p.stdin.write(json.dumps(m) + "\n")
p.stdin.flush()

result = {"tools": []}


def reader():
    for line in p.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("id") == 2:
            for t in obj.get("result", {}).get("tools", []):
                result["tools"].append(t.get("name"))


t = threading.Thread(target=reader, daemon=True)
t.start()
time.sleep(8)
p.kill()

print("TOOLS:", json.dumps(result["tools"], ensure_ascii=False))
err = p.stderr.read()[:800] if p.stderr else ""
print("STDERR:", err)
