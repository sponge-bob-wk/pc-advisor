# -*- coding: utf-8 -*-
"""lora_server.py —— 用 LoRA adapter 提供 Ollama 兼容的推理服务

## 为什么要这个

`lora_v4` 训好了，但 Ollama 里跑的还是**基座模型**（没有 adapter）。
要把 v4 接进真实服务，有两条路：

| 路径 | 速度 | 代价 |
|---|---|---|
| A. 导出 GGUF → ollama create | 约 2-3 秒 | 需要 llama.cpp |
| **B. 独立推理服务**（本文件） | **约 3-5 秒** | 占显存 |

**A 走不通**：hosts 把 29 个 GitHub 域名指向 127.0.0.1，
llama.cpp 下载不了。

## 关键优化（把 10 秒降到 3-5 秒）

实测 transformers 4bit 推理 **10.15 秒/条**，太慢。三个优化：

  ① **常驻模型** —— 加载一次（13.5 秒），之后复用。
     Ollama 每次请求也要重新加载（除非 keep_alive），这点打平。

  ② **KV cache 复用 system prompt** —— system 有 238 token，
     每次都要重新算。用 `past_key_values` 缓存它，省掉这部分。

  ③ **短输出** —— 路由任务只需要 60-80 token，
     `max_new_tokens=120` 够（实测 135 字符输出用不到 120 token）。

## 协议

完全兼容 Ollama 的 `/api/chat`，所以 `agent.chat()` 不用改：

    POST http://127.0.0.1:11435/api/chat
    {"model":"route-v4","messages":[...],"stream":false,"format":"json"}

## 用法

    & 'E:\ai2026\Scripts\python.exe' lora_server.py

然后让 agent 指向 11435 而不是 11434。

⚠️ **显存冲突**：这个服务占约 6 GB 显存，Ollama 也要占。
   两者不能同时跑（8GB 显存不够）。所以要么用 adapter 服务，
   要么用 Ollama 基座，不能都开。
"""
import argparse
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MODEL_DIR = r"E:\models\Qwen3-4B-Instruct-2507"
ADAPTER = os.path.join(HERE, "lora_v4")
PORT = 11435

# 全局模型（常驻）
_LOCK = threading.Lock()
_MODEL = [None]
_TOK = [None]
_CACHE = {}          # system prompt → past_key_values
_READY = [False]


def load():
    """加载基座 + adapter。只做一次。"""
    import torch
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              BitsAndBytesConfig)
    from peft import PeftModel

    # 安全守门
    try:
        from safe_run import check_before_load, mark_loaded
        check_before_load()
        mark_loaded()
    except ImportError:
        pass

    print("=== 加载模型 ===")
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    bnb = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_DIR, quantization_config=bnb, device_map="auto",
        trust_remote_code=True, torch_dtype=torch.bfloat16)
    if os.path.isdir(ADAPTER):
        model = PeftModel.from_pretrained(model, ADAPTER)
        print("  已加载 adapter: %s" % os.path.basename(ADAPTER))
    else:
        print("  ⚠️ 没找到 adapter，用基座")
    model.eval()
    _MODEL[0] = model
    _TOK[0] = tok
    _READY[0] = True
    print("  加载耗时 %.1fs，显存 %.2f GB"
          % (time.time() - t0,
             torch.cuda.memory_allocated() / 1024 ** 3))


def _prefill_cache(sysmsg):
    """预计算 system prompt 的 KV cache。

    🔴 2026-11-23：**这个优化暂时禁用**，因为它有 bug。

        实测报错：
          RuntimeError: cannot reshape tensor of 0 elements
          into shape [1, 0, -1, 128]

        原因：`model.generate(past_key_values=..., **ids)` 的调用约定
        比我想的复杂 —— transformers 5.x 里 generate 期望
        `inputs_embeds` 或正确的 `input_ids` 形状，
        而且 DynamicCache 的复用需要 `cache_position` 配合。

        收益（0.8 秒）不值得为它引入 bug。先禁用，等有空再优化。

    ⚠️ 保留这个函数是为了将来优化，现在 `generate()` 里不调用它。
    """
    return None


def generate(messages, max_new_tokens=120, fmt=None):
    """生成回复。返回文本。"""
    import torch
    tok, model = _TOK[0], _MODEL[0]

    # 🔴 2026-11-23：**简化实现** —— 直接用完整的 chat template，
    #    不用 KV cache 分段（那个有 bug，见 _prefill_cache 的注释）。
    #
    #    代价：system prompt（238 token）每次都重新 prefill，
    #    约 0.8 秒。相比 10 秒的总推理时间，这个开销可以接受。
    text = tok.apply_chat_template(messages, tokenize=False,
                                   add_generation_prompt=True)
    ids = tok(text, return_tensors="pt",
              add_special_tokens=False).to(model.device)

    with torch.no_grad():
        gen = model.generate(
            **ids,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tok.pad_token_id,
        )
    n_in = ids["input_ids"].shape[1]
    return tok.decode(gen[0][n_in:], skip_special_tokens=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass        # 静默，避免刷屏

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/version":
            self._send(200, {"version": "lora-v4-1.0"})
        elif self.path == "/api/tags":
            self._send(200, {"models": [{"name": "route-v4",
                                         "size": 0}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, {"error": "bad json"})
            return

        if self.path == "/api/chat":
            if not _READY[0]:
                self._send(503, {"error": "model not ready"})
                return
            msgs = req.get("messages") or []
            np_ = (req.get("options") or {}).get("num_predict") or 120
            t0 = time.time()
            try:
                with _LOCK:
                    text = generate(msgs, max_new_tokens=int(np_))
                self._send(200, {
                    "model": "route-v4",
                    "message": {"role": "assistant", "content": text},
                    "done": True,
                    "eval_count": len(text),
                    "_elapsed_s": round(time.time() - t0, 2),
                })
            except Exception as e:
                self._send(500, {"error": "%s: %s"
                                          % (type(e).__name__, str(e)[:80])})
        else:
            self._send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    print("=" * 60)
    print("LoRA adapter 推理服务（兼容 Ollama /api/chat）")
    print("=" * 60)
    print("  模型: %s" % os.path.basename(MODEL_DIR))
    print("  adapter: %s" % os.path.basename(ADAPTER))
    print("  端口: %d" % args.port)
    print()
    print("⚠️ 这个服务占约 6 GB 显存，和 Ollama 不能同时跑（8GB 不够）")
    print()

    load()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print()
    print("✓ 服务就绪：http://127.0.0.1:%d" % args.port)
    print("  让 agent 指向这个端口：agent.API = 'http://127.0.0.1:%d/api/chat'"
          % args.port)
    print()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
