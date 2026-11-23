# -*- coding: utf-8 -*-
"""test_compress2.py —— 更激进地压缩 prompt + 测试不同 max_length 的显存

目标：prompt + 答案 <= max_length，且显存不 OOM。

已知：
  512  → 显存 6.4 GB，成功
  1024 → OOM（928 MiB 分配失败）

所以 max_length 的可行区间在 512~1024 之间。试 640 / 768。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 更激进的压缩：砍掉例句，只留判据
TIGHT = """判断用户这句话在挑电脑时想干什么。只输出 JSON：
{"intent":"...","topic":"...","fit":true|false}

intent 七选一：
ask_current=问这台细节 | refuse_switch=不要这台 | state_range=说价位品类范围
analyze=问全库统计量 | state_new_need=加条件 | list_others=要剩余清单 | unknown=没说清

topic 十一选一：service screen keyboard noise build weight battery price config pros_cons none

fit：true=拿用途问这台行不行；false=不是

易混：
· 换一台=refuse_switch；换个32G的=state_new_need
· 有别的颜色吗/充电快吗 → ask_current
· 多重啊/散热行不行 → ask_current + 对应 topic
· 这个价算什么水平 → ask_current+price
· 哪个最便宜 → state_range
· 拿不准不要选 ask_current"""


def main():
    from transformers import AutoTokenizer
    import intent_layer as il

    tok = AutoTokenizer.from_pretrained(
        r"E:\models\Qwen3-4B-Instruct-2507", trust_remote_code=True)

    full = il.SYS_TMPL.format(ctx="（当前讨论：zol-2103842）")
    mid = open(r"E:\电脑涨价分析\tools\agent\_compressed_sys.txt",
               encoding="utf-8").read()

    variants = [("完整版", full), ("中等压缩", mid), ("激进压缩", TIGHT)]
    print("=== token 数 ===")
    for name, txt in variants:
        ids = tok(txt, add_special_tokens=False)["input_ids"]
        print("  %-8s %4d 字符 → %4d token" % (name, len(txt), len(ids)))

    n_tight = len(tok(TIGHT, add_special_tokens=False)["input_ids"])
    print()
    print("=== 激进压缩版的预算 ===")
    print("  prompt: %d token" % n_tight)
    print("  答案（90分位）: 107 token")
    print("  需要 max_length >= %d" % (n_tight + 107 + 20))
    print()
    print("  512 → %s" % ("✓ 够" if n_tight + 107 <= 512 else "✗ 不够"))
    print("  640 → %s" % ("✓ 够" if n_tight + 107 <= 640 else "✗ 不够"))
    print("  768 → %s" % ("✓ 够" if n_tight + 107 <= 768 else "✗ 不够"))

    import io
    io.open(r"E:\电脑涨价分析\tools\agent\_tight_sys.txt", "w",
            encoding="utf-8").write(TIGHT)
    print()
    print("  已写出 _tight_sys.txt")


if __name__ == "__main__":
    main()
