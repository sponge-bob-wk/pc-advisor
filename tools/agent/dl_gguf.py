# -*- coding: utf-8 -*-
"""下载 Qwen3-4B-Instruct-2507 的 Q4_K_M GGUF，用于 Ollama 部署（不走训练）。"""
import os, sys, time
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from huggingface_hub import hf_hub_download

REPO = "unsloth/Qwen3-4B-Instruct-2507-GGUF"
FN = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
DST = r"E:\models\gguf"

os.makedirs(DST, exist_ok=True)
t0 = time.time()
print("start", REPO, FN, flush=True)
p = hf_hub_download(repo_id=REPO, filename=FN, local_dir=DST)
sz = os.path.getsize(p) / 1024 ** 3
print("DONE %s  %.2f GB  %.0f s" % (p, sz, time.time() - t0), flush=True)