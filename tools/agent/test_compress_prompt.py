# -*- coding: utf-8 -*-
"""test_compress_prompt.py —— 试写压缩版 SYS_TMPL 并测量 token

## 为什么必须压缩

实测：完整版 SYS_TMPL 是 2170 字符 = **1072 token**。
而 8GB 显存能承受的 max_length 上限是 512（1024 会 OOM）。
1072 > 512 → 所有 label 被 mask → loss 恒等于 0。

**必须把 prompt 压到 350 token（约 700 字符）以内**，
留 150 token 给答案（CoT 样本 90 分位 107 token）。

## 压缩原则

保留（模型必须有才能判对）：
  · 输出格式
  · 七类 intent 的**区分特征**（不是完整定义）
  · 十一类 topic 的**关键词**
  · fit 的判据
  · 最容易混的几条对照

删掉：
  · 重复的解释性文字
  · 例句（保留最关键的 2-3 条）
  · 冗余的"注意""判据"提示词
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

COMPRESSED = """判断用户这句话在挑电脑时想干什么。只输出 JSON：
{"intent":"...","topic":"...","fit":true|false}

intent 七选一：
- ask_current   问这台的细节（屏幕/保修/重量/散热/够不够用/值不值/缺点）
- refuse_switch 不要这台了，要换。带否定词（不行/太贵/换个/算了）
- state_range   说价位段/品类/范围，不指向某一台（六七千的/轻薄本/一万以内）
- analyze       问整个库的统计量（多少台/平均/分布/排行/各品牌对比），不是在问某一台
- state_new_need 加条件（要32G/要独显/要台式）——是加要求，不是拒绝这台
- list_others   要剩下的清单（剩下的/都列出来/还有几个）
- unknown       没说清（嗯/哦/在吗/纯标点）

topic 十一选一（问哪一面；不在问细节给 none）：
service售后 | screen屏幕 | keyboard键盘 | noise噪音风扇 |
build做工材质颜色 | weight重量 | battery续航电池充电 |
price价格保值 | config配置参数接口 | pros_cons优缺点 | none

fit：true = 拿用途问"这台行不行"（够用吗/能跑PS吗/撑四年不）
     false = 不是问匹配

易混对照：
· 换一台=refuse_switch；换个32G的=state_new_need
· 有别的颜色吗/充电快吗/保修几年 → ask_current（问这台的状况）
· 多重啊/散热行不行（省略主语）→ ask_current，topic 给对应面
· 这个价算什么水平 → ask_current + price（不是 analyze）
· 哪个最便宜 → state_range（是找一台机器，不是统计）
· 拿不准不要选 ask_current（会让用户的新需求被当成追问而丢掉）"""


def main():
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(
        r"E:\models\Qwen3-4B-Instruct-2507", trust_remote_code=True)

    import intent_layer as il
    full = il.SYS_TMPL.format(ctx="（当前讨论：zol-2103842）")

    for name, txt in (("完整版", full), ("压缩版", COMPRESSED)):
        ids = tok(txt, add_special_tokens=False)["input_ids"]
        print("  %-6s %4d 字符 → %4d token" % (name, len(txt), len(ids)))

    n_comp = len(tok(COMPRESSED, add_special_tokens=False)["input_ids"])
    print()
    print("=== 预算核算（max_length=512）===")
    print("  压缩版 prompt:        %d token" % n_comp)
    print("  答案（CoT 90分位）:   约 107 token")
    print("  合计:                 %d token" % (n_comp + 107))
    print("  余量:                 %d token" % (512 - n_comp - 107))
    print()
    if n_comp + 107 <= 512:
        print("  ✓ 装得下")
    else:
        print("  ✗ 还是装不下，需要继续压缩")

    # 写出来供后续使用
    import io
    io.open(r"E:\电脑涨价分析\tools\agent\_compressed_sys.txt", "w",
            encoding="utf-8").write(COMPRESSED)
    print()
    print("  已写出 _compressed_sys.txt")


if __name__ == "__main__":
    main()
