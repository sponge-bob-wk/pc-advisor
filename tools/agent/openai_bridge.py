# -*- coding: utf-8 -*-
"""openai_bridge.py —— OpenAI 兼容适配层

## 为什么需要这层

open-webui 默认会把用户提问**直接转发给模型**，这样会绕过
`agent.py` 里 7000 行的核心逻辑：

  · 意图路由（7 类）—— 判断用户是在问当前这台、提新需求、还是要换台
  · 确定性排序（core.py）—— 推荐哪台、为什么是这台
  · 22 条审计判据 —— 拦截编造数字、超证据断言、跨机型串台
  · 价格口径校正 —— 到手价 vs 平台标价
  · 专业匹配（311 个专业）—— 内存/显存/加装门槛

直接接裸模型，用户会看到"看起来像那么回事但不准"的回答 ——
这正是本项目要解决的问题。

## 这个桥做什么

  ① 实现 OpenAI 的 `/v1/chat/completions` 接口
  ② 收到请求后，把最后一条用户消息交给 `agent.run()`
  ③ 把 agent 返回的结构化结果转成 markdown
  ④ 以 OpenAI 格式流式返回给 open-webui

## 关键设计：不丢信息

agent 返回的不只是文本，还有：
  · `stage` —— 走的是哪个分支
  · `slots` —— 解析出的条件（预算/品类/专业）
  · `warnings` —— 超预算、库里没有等
  · `top` —— 候选机型

这些都转成 markdown 呈现，不隐藏。但**只展示用户关心的**，
不把审计细节（untraceable_numbers 之类）倒给用户 ——
这正是用户说的"你想表达很多严谨性，但是没必要展示给用户看"。

## 用法

    python openai_bridge.py --port 8090

然后在 open-webui 里添加连接：
    Settings → Connections → OpenAI API
    Base URL: http://127.0.0.1:8090/v1
    API Key:  (随便填，本层不校验)
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import agent
import charts
import render_card

MODEL_ID = "pc-advisor"
MODEL_NAME = "选机助手"


def build_markdown(out):
    """把 agent 的结构化输出转成用户看得懂的 markdown。

    🔴 2026-11-26 修正（用户指出）：**优先用结构化卡片，不是只用 reply 文本**。

        `agent.run()` 除了 `reply`（自由文本）还返回 `answer` ——
        那是 `answer_card()` 生成的结构化卡片：
            {"headline": "...", "rows": [{k,v}...], "gaps": [...],
             "candidates": [...], "warnings": [...]}

        第一次写这层时我只取了 reply，把结构化部分丢了 ——
        用户指出「我的输出不是要结构化输出吗」，这里补回。

        优先顺序：
          ① 有 `answer` 卡片 → 用 render_card 渲染成 markdown 表格
          ② 没有（澄清/追问/拒答等分支）→ 用 reply 纯文本
          ③ 两者都有 → 卡片 + reply 里的补充说明

    🔴 展示原则（用户明确要求）：
       「你想表达很多严谨性，但是没必要展示给用户看」
    所以不展示：审计明细、dataAsOf、poolHint、数据来源标注。
    """
    card = out.get("answer")
    reply = (out.get("reply") or "").strip()

    # ① 结构化卡片优先
    if isinstance(card, dict) and card.get("headline"):
        # 从 reply 里挑出「价格口径」这类补充说明，避免丢失
        extra = ""
        for ln in reply.split("\n"):
            s = ln.strip()
            if not s:
                continue
            if ("是到手价" in s or "平台标价" in s
                    or "不含国补" in s):
                extra = s
                break
        md = render_card.render(card, extra_note=extra)
        if md.strip():
            return md

    # ② 没有卡片 → 回退到纯文本
    if reply:
        parts = [reply]
        warns = out.get("warnings") or []
        bw = [w for w in warns if "超预算" in str(w) or "买不到" in str(w)]
        if bw:
            parts.append("")
            parts.append("> ⚠️ " + "；".join(str(x) for x in bw[:2]))
        top = out.get("top") or []
        if len(top) > 1:
            parts.append("")
            parts.append("**其他可选**：" + "、".join(top[1:4]))
        return "\n".join(parts)

    return "请说说你的预算和用途，我来帮你挑。"


def fetch_overview():
    """拿 /api/overview 的数据（可视化专用）。

    🔴 为什么走 HTTP 而不是直接 import core 自己算：

        `/api/overview` 的注释写得很清楚：
          「所有数字在这里算好，前端只负责画 ——
            前端自己 group by / 求中位数，等于把『数字零写权限』
            从模型挪到了 JS 里，一样会算错、而且没人审计。」

        这个桥接层也是「前端」的角色。如果我自己在桥里重新
        group by，就等于把同一套统计逻辑抄了第二遍 ——
        项目已经复现过 9 次「判据抄两遍必漂」。

        所以：**只取现成的，不重复算。**

    ⚠️ 7860 没起时返回 None，图表就不画（不编数据）。
    """
    url = os.environ.get("OVERVIEW_URL", "http://127.0.0.1:7860/api/overview")
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print("[bridge] overview 取不到: %r" % e, flush=True)
        return None


def run_agent(messages):
    """从 OpenAI messages 里提取用户问题，交给 agent。

    多轮：取最后一条 user 消息作为本轮问题。
    历史上下文暂时不用 —— agent 自己维护 known/seen，
    但那需要前端配合传，open-webui 不传。所以这里退化成
    「单轮 + 无记忆」，等以后用 Conversations API 再做多轮。
    """
    user_text = ""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                user_text = c
            elif isinstance(c, list):
                # OpenAI 多模态格式：[{"type":"text","text":"..."}]
                for seg in c:
                    if isinstance(seg, dict) and seg.get("type") == "text":
                        user_text = seg.get("text") or ""
                        break
            break

    if not user_text.strip():
        return "请说说你的预算和用途，我来帮你挑。"

    try:
        out = agent.run(user_text)
        md = build_markdown(out)

        # ── 可视化：问分布/占比这类问题时配图 ──
        #
        # 🔴 2026-11-26（用户要求）：「我们的可视化数据也没了」
        #
        #    原来 7860 界面有价格区间分布、品类对比这些图，
        #    换到 open-webui 后丢了。这里补回来。
        #
        #    用 Mermaid 而不是 Chart.js：open-webui 内置 Mermaid，
        #    markdown 里写 ```mermaid 代码块就会自动渲染，
        #    不需要模型调工具，也不依赖外网 CDN。
        if wants_chart(user_text):
            ov = fetch_overview()
            if ov:
                chart_md = build_chart(user_text, ov)
                if chart_md:
                    md = md + "\n\n---\n\n" + chart_md

        return md
    except Exception as e:
        # 不把堆栈倒给用户 —— 但要在服务端日志里留痕
        print("[bridge] agent 失败: %r" % e, flush=True)
        return ("抱歉，这台机器的处理出了点问题。"
                "换个说法再问一次，或者说说预算和用途，我从头帮你挑。")


def wants_chart(user_text):
    """判断这个问题要不要配图。

    判据：问的是「分布 / 占比 / 对比」这类**整体结构**的问题。
    单个统计（多少台、平均多少）不需要画图 —— 一个数字配张图反而累。

    ⚠️ 这个判断只看关键词，不看模型。因为：
       ① 桥接层要快（模型已经跑了一次，不该再跑一次）
       ② 关键词判断可以审计、可以测试，模型判断不能
    """
    t = user_text or ""
    KW = ("分布", "占比", "比例", "构成", "对比", "各区", "区间",
          "多少台", "统计", "概况", "整体", "品类")
    return any(k in t for k in KW)


def build_chart(user_text, ov):
    """按问题挑图表。ov 是 /api/overview 的返回。"""
    t = user_text or ""
    blocks = []

    # 问品类/对比/概况 → 品类分布图
    if any(k in t for k in ("品类", "对比", "概况", "整体", "分布", "占比")):
        b = charts.category_bar(ov)
        if b:
            blocks.append(b)

    # 问价格区间 → 价格分布图
    if any(k in t for k in ("价格", "价位", "区间", "预算分布")):
        b = charts.price_distribution(ov)
        if b:
            blocks.append(b)

    # 问重量 → 重量对比
    if any(k in t for k in ("重量", "多重", "沉")):
        b = charts.weight_compare(ov)
        if b:
            blocks.append(b)

    # 什么都没匹配上，但确实问的是分布类 → 给品类图兜底
    if not blocks and any(k in t for k in ("分布", "占比", "统计")):
        b = charts.category_bar(ov)
        if b:
            blocks.append(b)

    return "\n\n".join(blocks)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # 只记关键信息，不刷屏
        print("[bridge] %s" % (fmt % args), flush=True)

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/v1/models", "/models"):
            self._send_json(200, {
                "object": "list",
                "data": [{
                    "id": MODEL_ID,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "local",
                    "name": MODEL_NAME,
                }],
            })
        elif path in ("/health", "/"):
            self._send_json(200, {"status": "ok", "model": MODEL_ID})
        else:
            self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self._send_json(404, {"error": {"message": "not found"}})
            return

        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            self._send_json(400, {"error": {"message": "bad json"}})
            return

        msgs = req.get("messages") or []
        stream = bool(req.get("stream"))
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())

        text = run_agent(msgs)

        if not stream:
            self._send_json(200, {
                "id": cid,
                "object": "chat.completion",
                "created": created,
                "model": req.get("model") or MODEL_ID,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            })
            return

        # ── 流式（open-webui 默认用这个）──
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        def sse(obj):
            self.wfile.write(
                ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n")
                .encode("utf-8"))
            self.wfile.flush()

        # 首块给 role，open-webui 需要
        sse({
            "id": cid, "object": "chat.completion.chunk",
            "created": created, "model": req.get("model") or MODEL_ID,
            "choices": [{"index": 0, "delta": {"role": "assistant"},
                         "finish_reason": None}],
        })

        # 分块吐字，模拟打字机效果
        CHUNK = 24
        for i in range(0, len(text), CHUNK):
            sse({
                "id": cid, "object": "chat.completion.chunk",
                "created": created, "model": req.get("model") or MODEL_ID,
                "choices": [{"index": 0,
                             "delta": {"content": text[i:i + CHUNK]},
                             "finish_reason": None}],
            })

        sse({
            "id": cid, "object": "chat.completion.chunk",
            "created": created, "model": req.get("model") or MODEL_ID,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("=" * 60)
    print("选机助手 · OpenAI 兼容桥")
    print("=" * 60)
    print("  监听   http://%s:%d" % (args.host, args.port))
    print("  模型   %s (%s)" % (MODEL_ID, MODEL_NAME))
    print("  端点   /v1/models、/v1/chat/completions")
    print()
    print("  在 open-webui 里配置：")
    print("    Settings → Connections → OpenAI API")
    print("    Base URL: http://%s:%d/v1" % (args.host, args.port))
    print("    API Key:  （随便填）")
    print("=" * 60)
    print()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")


if __name__ == "__main__":
    main()
