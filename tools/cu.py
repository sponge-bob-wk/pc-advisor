# -*- coding: utf-8 -*-
"""computer-use MCP 的极简 stdio 客户端。

用法：
  python cu.py list_apps
  python cu.py state <pid> [--shot out.png] [--grep 关键词] [--max 8000]
  python cu.py click <pid> <element_id>
  python cu.py type <pid> <text> [element_id]
  python cu.py key <pid> <key>
  python cu.py scroll <pid> <element_id> <up|down> [pages]
  python cu.py launch <app>

每次调用独立拉起服务（桌面状态在系统里，不依赖进程常驻）。
"""
import json, os, subprocess, sys, time, threading

CLI = (r"E:\trae\Trae CN\resources\app\extensions\byted-solo.builtin-mcp"
       r"\node_modules\@byted-solo\computer-use-mcp\switch\cli.mjs")


class Client:
    def __init__(self):
        self.p = subprocess.Popen(
            ["node", CLI, "--windows-cua-v1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self.id = 0
        self.inbox = []
        self.cv = threading.Condition()
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()
        self._send({"jsonrpc": "2.0", "id": self._nid(), "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "cu-cli", "version": "0"}}})
        self._wait(1, 30)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _nid(self):
        self.id += 1
        return self.id

    def _reader(self):
        for line in self.p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            with self.cv:
                self.inbox.append(obj)
                self.cv.notify_all()

    def _send(self, obj):
        self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def _wait(self, rid, timeout=120):
        end = time.time() + timeout
        with self.cv:
            while True:
                for i, o in enumerate(self.inbox):
                    if o.get("id") == rid:
                        self.inbox.pop(i)
                        return o
                left = end - time.time()
                if left <= 0:
                    raise TimeoutError(f"等待响应超时 id={rid}")
                self.cv.wait(min(left, 1.0))

    def call(self, tool, args=None, timeout=180):
        rid = self._nid()
        self._send({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                    "params": {"name": tool, "arguments": args or {}}})
        return self._wait(rid, timeout)

    def close(self):
        try:
            self.p.kill()
        except Exception:
            pass


def render(resp, shot=None, grep=None, maxlen=8000):
    if resp.get("error"):
        print("[MCP_ERROR]", json.dumps(resp["error"], ensure_ascii=False))
        return
    out = []
    for block in resp.get("result", {}).get("content", []) or []:
        if block.get("type") == "text":
            out.append(block.get("text", ""))
        elif block.get("type") == "image":
            import base64
            path = shot or "out/cu-last.png"
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as f:
                f.write(base64.b64decode(block.get("data", "")))
            out.append(f"[image saved] {path}")
        elif block.get("type") == "image-uri":
            out.append(f"[image] {block.get('uri')}")
    body = "\n".join(out)
    if grep:
        body = "\n".join(l for l in body.splitlines() if grep in l)
    if len(body) > maxlen:
        body = body[:maxlen] + f"\n...[截断，总长 {len(body)}]"
    print(body)


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    c = Client()
    try:
        cmd = a[0]
        if cmd == "list_apps":
            render(c.call("list_apps"), maxlen=6000)
        elif cmd == "launch":
            render(c.call("launch_app", {"app": a[1]}))
        elif cmd == "state":
            pid = int(a[1])
            shot = grep = None
            maxlen = 8000
            win = None
            i = 2
            while i < len(a):
                if a[i] == "--shot":
                    shot = a[i + 1]; i += 2
                elif a[i] == "--grep":
                    grep = a[i + 1]; i += 2
                elif a[i] == "--max":
                    maxlen = int(a[i + 1]); i += 2
                elif a[i] == "--window":
                    win = int(a[i + 1]); i += 2
                else:
                    i += 1
            args = {"pid": pid}
            if win:
                # Edge 多窗口（主窗口 vs DevTools 窗口）必须指定，
                # 否则 MCP 自动挑一个，常挑到调试条窗口截出空白
                args["windowId"] = win
            if shot:
                args["disableScreenshot"] = False
            else:
                args["disableScreenshot"] = True
                args["disableDiff"] = True
            render(c.call("get_app_state", args), shot=shot, grep=grep, maxlen=maxlen)
        elif cmd == "click":
            render(c.call("click", {"pid": int(a[1]), "element_id": a[2]}))
        elif cmd == "type":
            args = {"pid": int(a[1]), "text": a[2]}
            if len(a) > 3:
                args["element_id"] = a[3]
            render(c.call("type_text", args))
        elif cmd == "key":
            render(c.call("press_key", {"pid": int(a[1]), "key": a[2]}))
        elif cmd == "scroll":
            args = {"pid": int(a[1]), "element_id": a[2],
                    "direction": a[3] if len(a) > 3 else "down"}
            if len(a) > 4:
                args["pages"] = int(a[4])
            render(c.call("scroll", args))
        elif cmd == "call":
            tool = a[1]
            raw = a[2] if len(a) > 2 else os.environ.get("CU_ARGS", "{}")
            if raw.startswith("@"):
                with open(raw[1:], encoding="utf-8") as f:
                    raw = f.read()
            args = json.loads(raw)
            render(c.call(tool, args), maxlen=6000)
        elif cmd == "batch":
            path = a[1][1:] if a[1].startswith("@") else a[1]
            with open(path, encoding="utf-8") as f:
                spec = json.load(f)
            for i, step in enumerate(spec.get("steps", []), 1):
                resp = c.call(step["tool"], step.get("args") or {},
                              timeout=step.get("timeout", 180))
                print(f"--- step {i} {step['tool']}")
                render(resp, shot=step.get("shot"), grep=step.get("grep"),
                       maxlen=step.get("max", 5000))
                if step.get("sleep"):
                    time.sleep(step["sleep"])
        else:
            print("未知命令", cmd)
    finally:
        c.close()


if __name__ == "__main__":
    main()
