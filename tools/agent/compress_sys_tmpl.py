# -*- coding: utf-8 -*-
"""compress_sys_tmpl.py —— 把 SYS_TMPL 压到 512 token 预算内

## 为什么必须压缩（2026-11-23 事故）

昨天修「训练/推理 system prompt 不一致」时，把训练集 system
从 56 字符改成 2170 字符（和推理一致）。但 `max_length` 还是 512。

结果：
  system prompt = 1055 token > max_length 512
  → `_prep` 里 n_prefix = min(len(prefix_ids), len(full_ids)) = 全部
  → labels = [-100] * 全部 → **没有可学习的 token**
  → loss 恒等于 0、grad_norm 恒等于 0 → 训练完全空转

## 硬约束（实测）

`probe_maxlen.py` 在 8GB 显存上测：
  max_length=512 → 成功（显存峰值 11.76 GB，借用了统一内存）
  max_length=640 → OOM
  max_length=768 → OOM

**512 是硬上限，没有余量。**

所以 prompt 必须压到 512 - 107（CoT 90分位）- 20（余量）= **385 token 以内**。

## 压缩版设计

从 1055 token 压到约 210 token，砍掉：
  · 冗长的解释性文字
  · 大部分例句（只留最关键的易混对照）
  · "注意""判据""⚠️"这类提示词

保留（模型判对必须有的）：
  · 输出格式
  · 七类 intent 的**区分特征**
  · 十一类 topic 的关键词
  · fit 的判据
  · 6 条最易混的对照
"""
import io
import sys

sys.stdout.reconfigure(encoding='utf-8')

P = r'E:\电脑涨价分析\tools\agent\intent_layer.py'
src = io.open(P, encoding='utf-8').read()

NEW_SYS = '''SYS_TMPL = """判断用户这句话在挑电脑时想干什么。只输出 JSON：
{{"intent":"...","topic":"...","fit":true|false}}

intent 七选一：
ask_current=问这台细节 | refuse_switch=不要这台 | state_range=说价位品类范围
analyze=问全库统计量 | state_new_need=加条件 | list_others=要剩余清单 | unknown=没说清

topic 十一选一：service screen keyboard noise build weight battery price config pros_cons none

fit：true=拿用途问这台行不行；false=不是

易混：
· 换一台=refuse_switch；换个32G的=state_new_need
· 有别的颜色吗/充电快吗/保修几年 → ask_current
· 多重啊/散热行不行 → ask_current + 对应 topic
· 这个价算什么水平 → ask_current+price
· 哪个最便宜 → state_range（是找一台机器，不是统计）
· 拿不准不要选 ask_current

{ctx}"""
'''

# 找到 SYS_TMPL 的起止
start = src.find('SYS_TMPL = """')
if start < 0:
    print("✗ 找不到 SYS_TMPL")
    sys.exit(1)
end = src.find('"""', start + len('SYS_TMPL = """')) + 3

old = src[start:end]
print("原 SYS_TMPL: %d 字符" % len(old))

new_src = src[:start] + NEW_SYS.rstrip('\n') + src[end:]
io.open(P, 'w', encoding='utf-8', newline='\n').write(new_src)

print("新 SYS_TMPL: %d 字符" % len(NEW_SYS))
print("已写入 %s" % P)
