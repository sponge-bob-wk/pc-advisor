# -*- coding: utf-8 -*-
"""下载 Qwen3-4B-Instruct-2507（纯文本版）到 E 盘。走 hf-mirror。"""
import os, sys, time

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

REPO = "Qwen/Qwen3-4B-Instruct-2507"
LOCAL = r"E:\models\Qwen3-4B-Instruct-2507"

from huggingface_hub import snapshot_download

t0 = time.time()
p = snapshot_download(
    repo_id=REPO,
    local_dir=LOCAL,
    allow_patterns=["*.json", "*.txt", "*.safetensors"],
    max_workers=4,
)
print("DONE", p, "%.1f s" % (time.time() - t0))