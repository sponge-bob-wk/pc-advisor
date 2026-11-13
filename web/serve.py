# -*- coding: utf-8 -*-
"""
前端 + 对话代理：把 web/index.html 跑在 8088，并把 /api/* 转发给本地选机服务（7860）。

为什么需要它：
  页面是 file:// 打开的，浏览器不允许它直接跨域调 127.0.0.1:7860。
  这个进程给页面和模型服务放在同一个源上，顺带补齐 CORS，
  所以页面里那句 fetch('/api/chat') 不用改、也不用给 agent/server.py 动刀。

用法：
  python web/serve.py            # 或双击 web/启动-带对话.bat
  浏览器打开 http://127.0.0.1:8088
"""
import json
import os
import sys
import urllib.request

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
UPSTREAM = os.environ.get("AGENT_UPSTREAM", "http://127.0.0.1:7860")
PORT = int(os.environ.get("WEB_PORT", "8088"))

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ---------- helpers ----------
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(self, code, data, ctype):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    # ---------- http ----------
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self._proxy("GET")
        if path in ("/", "/index.html"):
            path = "/index.html"
        rel = path.lstrip("/")
        if ".." in rel or rel.startswith("/"):
            return self._send(403, "forbidden", "text/plain; charset=utf-8")
        fp = os.path.join(BASE, rel)
        if not os.path.isfile(fp):
            return self._send(404, "not found: " + rel, "text/plain; charset=utf-8")
        ctype = MIME.get(os.path.splitext(fp)[1].lower(), "application/octet-stream")
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_POST(self):
        if urlparse(self.path).path.startswith("/api/"):
            return self._proxy("POST")
        self._send(404, "not found", "text/plain; charset=utf-8")

    def _proxy(self, method):
        body = None
        if method == "POST":
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
        req = urllib.request.Request(
            UPSTREAM + self.path, data=body, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type") or "application/json; charset=utf-8"
            self._send(200, data, ctype)
        except Exception as e:
            print("[proxy] %s %s -> %s: %s" % (method, self.path, type(e).__name__, e), flush=True)
            self._json(502, {
                "error": "agent_down",
                "detail": str(e)[:200],
                "hint": "先启动选机服务：双击 tools/agent/启动测试.bat（或 python tools/agent/server.py）",
            })


def main():
    print("=" * 58)
    print("电脑涨价分析 · 工作台（含对话）")
    print("页面   http://127.0.0.1:%d" % PORT)
    print("上游   %s  （选机智能体，未启动时对话会退回规则模式）" % UPSTREAM)
    print("按 Ctrl+C 停止")
    print("=" * 58)
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    except OSError as e:
        print("端口 %d 起不来：%s" % (PORT, e))
        print("换个端口：WEB_PORT=8089 python web/serve.py")
        sys.exit(1)


if __name__ == "__main__":
    main()
