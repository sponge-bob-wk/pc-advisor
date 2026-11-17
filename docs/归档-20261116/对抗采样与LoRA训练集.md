# 对抗采样 → LoRA 训练集（2026-11-16）

> 目标：为「选机决策智能体」的路由层（`intent/topic/fit`）攒够可训练样本。
> 结论：**路由层 387 条**，离 500 条下限还差 113；预算层已有 1316 条，不用再补。

---

## 1. 为什么不是「随便多造点句子」

`export_lora_data.py` 原来的统计是「立即可用 117 条 / 目标 500~1000」。
直觉解法是批量生成句子，但那样有两个坑：

**坑一：假数据撑数。** 随机拼词造出来的句子，标签得靠人一条条标，
标错的代价远大于少几条。项目纪律里写着「未取证不许填数字」，
样本标签同理。

**坑二：混了标签体系。** `lora_data/` 下原本躺着 8 份 jsonl，
其中 `intent_train.jsonl` 有 1747 条 —— 看起来「够训了」，
但它标的是 **`budget_action`（预算层）**，不是路由层的 `intent/topic/fit`。
混在一起训，等于教模型输出错误结构。

所以第一步不是造数据，是**把账算清**。

---

## 2. 真实账目（`lora_status.py`）

```
① 按文件
  intent_train.jsonl       1747 条  ← 预算层
  intent_eval.jsonl         253 条
  adv-all.jsonl             152 条  ← 本轮新建
  train.jsonl                98 条
  intent_adversarial.jsonl   71 条
  adv-errors.jsonl           40 条
  auto-2026-11-13.jsonl       0 条（220 条跳过：无 assistant 内容）
  pending.jsonl               0 条（139 条跳过：待人工标注）

② 跨文件去重后：1490 条唯一问句（丢弃重复 294 条）

③ 按标签体系分开算
  预算层 budget_action          1316 条   够训
  路由层 intent/topic/fit        149 条   不够，缺 351 条到下限
```

**这才是真实缺口：路由层只有 149 条。** 之前那句「1490 条够训」是被预算层撑起来的假象。

---

## 3. 三条补数据的路，各有边界

| 路 | 工具 | 产出 | 为什么停在这 |
|---|---|---|---|
| 手写模板 | `adversarial_harvest.py` | 152 条 | 手写超过 150 条后开始自我重复 |
| 维度组合 | `expand_templates.py` | 348 条 | 只有「成分唯一决定标签」的句式能组合 |
| 真实日志 | `harvest_from_logs.py` | 17 条 | 4 天日志 399 句，去重后只剩 17 句形态可判定 |

### 3.1 手写模板：按「最小对比对」组织

不是随机造句，而是从 `intent_layer.SYS_TMPL` 的「关键区分」段反推 ——
那一段列出的每一条混淆点，都是一个真实翻车现场：

```
"换个32G的" = 加条件 → state_new_need（不是 refuse_switch）
"换一台"   = 不要了 → refuse_switch
"能跑PS吗" = fit=true 且 intent=ask_current
```

所以模板成对造：一个词之差，标签必须翻转。这类最小对比对对训练最有效。

### 3.2 维度组合：只组「成分决定标签」的句式

组合的前提是能回答：**这句话的正确标签，是不是由成分唯一决定的？**

| 能组合 | 为什么 | 不能组合 | 为什么 |
|---|---|---|---|
| 能[跑PS]吗 | 成分决定 fit=true | [这个]怎么样 | 取决于"这个"指什么 |
| [多重]啊 | 成分决定 topic=weight | 还有别的吗 | 取决于上下文 |
| [换一台] | 成分决定 refuse_switch | 5000 的轻薄本 | 可能同时是 state_range 和 state_new_need |

轴的分布：

```
用途 × 5 种问法      80 条   ask_current / fit=True
追问面 × 5 种问法    62 条   ask_current / topic=X / fit=False
配置 × 3 种加条件句式  36 条   state_new_need
价位段               77 条   state_range
品类                 24 条   state_range
换台                 18 条   refuse_switch
```

### 3.3 真实日志：只收形态能判定的

从 4 天日志（`logs/chat-*.log`，367KB，399 句）提取。
**标签用形态规则打，不用模型输出**（那是自证，会把模型的错误固化成标准答案）。

只收这几类：

| 形态 | 判定 | 为什么能确定 |
|---|---|---|
| 纯预算数字 | state_range | 没有别的读法 |
| 纯品类词 | state_range | 同上 |
| 空/标点/应答词 | unknown | 同上 |
| 明确换台（带否定/换台动词） | refuse_switch | 同上 |
| 明确列清单 | list_others | 同上 |
| 「我的要求是…」 | state_new_need | M05 事故结论 |
| 「预算提高到/控制在…」 | state_range | 真实事故结论 |

**其余 337 句全部丢弃**，包括"这个怎么样""够用吗"这类要靠上下文才能判的。
宁可少收，不许错标。

踩到一个坑：日志里真出现过 `"1"`（用户手滑），被旧规则标成 `state_range`。
已加 `RE_BARE_NUM`：1-2 位纯数字一律判不了。

---

## 4. 标签校验：三方对照，找出的是代码缺陷

`build_route_lora.py` 合并后逐条核对三方：

1. 训练集标签
2. `agent._is_fit_question`（代码里的匹配度判据）
3. `intent_layer.fit_by_rule`（词表兜底判据）

结果：**可疑 1 条，代码词表漏 42 条。**

### 4.1 代码词表漏 42 条 —— 这是架构的实证

`_is_fit_question` 的 CAP 词表只有 13 个词：

```
合适/适合/能不能/可不可以/够用/够不够/能跑/能打/带得动/玩得动/跑得动/行不行/行吗
```

而 `SYS_TMPL` 自己举的 fit=true 例子包含「适合做设计吗 / 撑四年不 / 编程够吗」。
两相对照，词表漏了整整三类：

1. **「能+任意动词」** —— 能做设计吗 / 能剪视频吗 / 能编程吗 / 能开虚拟机吗
2. **「够+任意动词」** —— 够跑PS吗 / 够做设计吗
3. **「撑N年 / 用到毕业」** —— 能撑四年吗 / 够我用到毕业吗

这是**枚举式判据的固有限制**：动词是开放集合，词表永远列不完。
而模型能泛化 —— 这正是本项目「模型为主、规则兜底」的实证依据。

**处理方式：不在训练集里改标签。** 标签是对的，是规则漏了。

### 4.2 可疑 1 条 —— M05 事故的已知冲突

```
「我的要求是能跑设计软件」
  训练集标签：state_new_need / fit=False
  _is_fit_question：True
  fit_by_rule：True
```

两套规则**只看能力词、不看主语**，所以判 True。
但 M05 的真实结论是「我的要求是…」= 给条件 → state_new_need。

**训练集保留 `state_new_need / False`**，这是设计结论，不是笔误。
`build_route_lora.py` 会把这条单独点名打印，避免下一个人以为标签写错。

---

## 5. 产物

| 文件 | 条数 | 说明 |
|---|---:|---|
| `lora_data/route-train.jsonl` | **353** | 训练集 |
| `lora_data/route-eval.jsonl` | **34** | 评估集（按 user 哈希稳定切分 10%，与训练集交集 0） |
| `lora_data/adv-all.jsonl` | 152 | 手写模板原始产出 |
| `lora_data/adv-errors.jsonl` | 40 | 其中模型判错的（训练价值最高） |
| `lora_data/expanded-route.jsonl` | 348 | 维度组合产出 |
| `lora_data/log-route.jsonl` | 17 | 真实日志产出 |

格式（与 `export_lora_data.py` 一致，三段式 messages）：

```json
{"messages":[
  {"role":"system","content":"你是选机助手的路由器。只输出 JSON：{\"intent\":...,\"topic\":...,\"fit\":...}"},
  {"role":"user","content":"换个台式的"},
  {"role":"assistant","content":"{\"intent\": \"state_new_need\", \"topic\": \"none\", \"fit\": false}"}
],"_meta":{"why":"「换个X的」是加条件，不是拒绝当前这台"}}
```

标签分布：

```
ask_current    160   refuse_switch   31   state_range   120
state_new_need  56   list_others     10   unknown        10
```

topic 覆盖（ask_current 才有意义）：`pros_cons 14 / price 10 / service 8 / screen 8 /
config 7 / build 6 / weight 6 / keyboard 5 / noise 5 / battery 5`。

---

## 6. 还差 113 条，缺在哪

```
list_others    10 条   ← 最缺
unknown        10 条   ← 次缺
refuse_switch  31 条
```

`list_others` 和 `unknown` 的句式本身就少（用户很少说"都列出来"），
靠模板扩不动。可选的补法：

1. **真实日志继续攒** —— 现在只有 4 天。用得越多，`harvest_from_logs.py` 能挖的越多。
2. **`harvest_samples.py` 全量跑** —— 243 条采集池跑一遍约 8-10 分钟，需要人工标注。
3. **接受 387 条先训一版** —— 略低于 500 下限，但六类标签全覆盖、topic 十类全覆盖，
   可以先跑一版看效果，再决定要不要补。

---

## 7. 复跑

```bash
cd tools/agent

python adversarial_harvest.py --write --only-errors   # 手写模板 + 只收模型错的
python expand_templates.py --write --run             # 维度组合 + 跑模型看判错率
python harvest_from_logs.py --write                  # 真实日志提取
python build_route_lora.py --write                   # 合并去重 + 标签校验 + 切分
python lora_status.py                                # 看总账
```

`expand_templates.py --run` 实测模型正确率 **67.5%（235/348）**，
判错 113 条 —— 这些判错样本的训练价值最高，是 LoRA 真正要学的东西。
