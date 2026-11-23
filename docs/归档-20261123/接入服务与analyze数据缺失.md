# 把 v4 接进服务 + 发现 analyze 数据缺失（2026-11-23 下午）

> **一句话**：v4 训练成功并接进了推理服务，
> 但发现 `analyze` 意图的训练样本数是 **0**，模型压根没学过。

---

## 一、v4 训练结果（今天上午）

| 指标 | v3（坏的） | **v4** |
|---|---:|---:|
| train_loss | 恒等于 0 | **2.859 → 0.134** |
| eval_loss | 0.6022 → 0.1946 | **0.5608 → 0.1968** |
| 耗时 | 20.8 分钟 | 27.9 分钟 |
| 对抗测试 | 82.0% | **84.0%** |
| 「好的」的输出 | **1219 字符死循环，无 JSON** | **52 字符，JSON 正常** |

**关键验证**：

```
好的    → {"intent": "unknown", "topic": "none", "fit": false}  ✓
哈哈哈  → 短思维链 + JSON  ✓
谢谢    → {"intent": "unknown", ...}  ✓
```

**v3 的死循环彻底消失。** 这是昨天两个 bug（system prompt 不一致 +
label 全被 mask）修复后的直接结果。

---

## 二、把 v4 接进推理服务

### 2.1 为什么不能导出 GGUF

| 路径 | 速度 | 可行性 |
|---|---|---|
| A. 导出 GGUF → `ollama create` | 2-3 秒 | ❌ **走不通** |
| B. 独立 adapter 服务 | 3-11 秒 | ✅ 已实现 |

**A 走不通的原因**：`hosts` 文件把 **29 个 GitHub 域名指向 127.0.0.1**，
llama.cpp 下载不了。

### 2.2 `lora_server.py` 的实现

**协议完全兼容 Ollama 的 `/api/chat`**，所以 `agent.chat()` 不用改：

```
POST http://127.0.0.1:11435/api/chat
{"model":"route-v4","messages":[...],"stream":false,
 "options":{"num_predict":120}}
```

实现要点：

| 点 | 做法 |
|---|---|
| 常驻模型 | 加载一次（13.5 秒），之后复用 |
| 单线程锁 | `_LOCK` 保证并发安全（GPU 不能并行推理） |
| 安全守门 | 加载前调 `safe_run.check_before_load()` |
| 错误返回 | 500 带真实异常信息（不吞） |

### 2.3 实测速度

| 输入 | 耗时 | 输出 |
|---|---:|---|
| 「好的」 | **3.71s** | `{"intent":"unknown",...}` 52 字符 |
| 「换一台」 | **3.20s** | `{"intent":"refuse_switch",...}` |
| 「这机器多重」 | **10.78s** | 带 CoT + JSON |
| 「有多少台有独显」 | **11.52s** | 带 CoT + JSON |

**规律**：模型判成"简单"的输入不输出 CoT（3 秒），
判成"需要分析"的输入输出 CoT（10-11 秒）。

### 2.4 踩的坑

#### 坑 1：venv python 会派生子进程

```
pid=26652  E:\ai2026\Scripts\python.exe  内存 3 MB   ← launcher
  └─ pid=27132  C:\...\Python312\python.exe  内存 28 MB  ← 真正的解释器
```

**`venv` 的 `python.exe` 只是个 launcher**，它转发给系统 Python 执行。
所以看到两个进程是正常的，不是重复实例。

#### 坑 2：KV cache 复用有 bug

我原本想缓存 system prompt 的 KV cache（省 0.8 秒/请求），但：

```
RuntimeError: cannot reshape tensor of 0 elements
into shape [1, 0, -1, 128]
```

`model.generate(past_key_values=..., **ids)` 的调用约定比我想的复杂 ——
transformers 5.x 里 `DynamicCache` 的复用需要 `cache_position` 配合。

**已禁用**（`_prefill_cache` 保留但返回 None）。
收益 0.8 秒不值得为它引入 bug。

#### 坑 3：端口被旧实例占用

第一次启动时，一个用**系统 Python**（没有 CUDA 4bit）的旧实例占了 11435，
新的 venv 实例因端口冲突无法绑定，导致全部请求 500。

**排查方法**：`Get-NetTCPConnection -LocalPort 11435` 看 OwningProcess，
再用 `Get-CimInstance Win32_Process` 看命令行。

---

## 三、发现 analyze 意图数据缺失（重要）

### 3.1 现象

`lora_server` 跑通后测试：

```
「有多少台有独显」→ 判成 ask_current（应该 analyze）
```

### 3.2 排查

查训练集的 intent 分布：

```
ask_current      187
state_range      125
state_new_need    94
refuse_switch     50
list_others       26
unknown           15
**analyze           0**   ← 一条都没有
```

**整个项目里没有任何 analyze 训练样本。**

### 3.3 根因

`analyze` 意图是 **2026-11-21** 加的。当时做了什么：

| 做了 | 没做 |
|---|---|
| ✅ `analyze.py`（393 行引擎） | ❌ **训练样本** |
| ✅ `agent.py` 的 0b2 分支 | ❌ **`LABELS` 元组** |
| ✅ 规则兜底正则 | |
| ✅ `test_analyze.py`（27 断言） | |

**只写了代码，没喂数据。** 模型压根没见过这个类别，
只能把统计问句归到最像的 `ask_current`。

### 3.4 这是一个典型的「规则兜底掩盖了模型缺陷」

`agent.py` 里有一层规则兜底：

```python
if not _want_analyze:
    _want_analyze = bool(re.search(
        r"多少台|几台|平均|中位数|分布|各品牌|各品类|按品牌|"
        r"最强|最弱|最贵|最便宜|排行|排名|占比|统计|对比一下各", user_text))
```

**所以线上一直能用** —— 规则命中了就进 analyze 分支。

但这是**规则在工作，不是模型在工作**。违背了「模型为主、规则兜底」的架构原则：
规则变成了主力，模型完全缺失。

而且规则的覆盖面有限，比如「帮我数数带独显的」不含上面任何关键词，
就会漏掉。

### 3.5 修法：补 63 条种子数据

新增 `seed_analyze.py`：

| 类型 | 条数 | 内容 |
|---|---:|---|
| 正例 | 55 | 覆盖 8 种聚合操作（count/mean/median/min/max/groupby/distribution/top） |
| 负例 | 8 | 「这个价算什么水平」「哪个最便宜」这类**容易误判成 analyze** 的 |

**为什么必须有负例**：没有它们，模型会把 `analyze` 用滥 ——
「这个价算什么水平」会被判成统计（实际是问当前这台）。

负例覆盖的易混场景：

| 问句 | 正确类别 | 为什么不是 analyze |
|---|---|---|
| 这个价在市场上算什么水平 | `ask_current` + price | 问的是**这一台**的价位 |
| 哪个最便宜 | `state_range` | 是在找**一台**机器，不是要统计数字 |
| 有便宜点的吗 | `state_new_need` | 提新要求，不是统计 |
| 换个平均点的 | `state_new_need` | 「平均」在这里是形容词 |
| 性能最强的这台怎么样 | `ask_current` + pros_cons | 「最强」形容当前这台 |

### 3.6 结果

训练集 **497 → 553 条**：

| intent | 条数 | 占比 |
|---|---:|---:|
| ask_current | 190 | 34.4% |
| state_range | 127 | 23.0% |
| state_new_need | 96 | 17.4% |
| refuse_switch | 50 | 9.0% |
| **analyze** | **49** | **8.9%** |
| list_others | 26 | 4.7% |
| unknown | 15 | 2.7% |

**`analyze` 占 8.9%**，接近经验底线（每个类别不低于最大类的 1/5）。

同时 `LABELS` 元组加了 `"analyze"` —— 这个元组用于校验样本合法性，
不加的话新样本会被当非法丢掉。

---

## 四、下次开工：重训

**必须重训**，否则 `analyze` 永远判不对（模型没见过）。

### 4.1 前置检查

```powershell
cd E:\电脑涨价分析\tools\agent
$py = 'E:\ai2026\Scripts\python.exe'

# 1. 关掉 adapter 服务（占 6 GB 显存）
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object {
    (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -like '*lora_server*'
} | Stop-Process -Force

# 2. 关掉 Ollama（也占显存）
Get-Process -Name 'ollama app','ollama','llama-server' -ErrorAction SilentlyContinue |
    Stop-Process -Force

# 3. 检查内存
& $py safe_run.py
```

**如果 commit 不足 13 GB**，关浏览器/微信，或重启。

### 4.2 重训

```powershell
$env:PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True'
& $py train_route_lora.py --out lora_v5
```

预计 **30 分钟**（553 条比 497 条多一些）。

### 4.3 验证

```powershell
# 对抗测试（对比 v4 的 84.0% 和 lora_out 的 86.0%）
& $py eval_lora.py --suite adversarial --adapter lora_v5

# 关键：确认 analyze 判对了
& $py -c "
import intent_layer as il
for t in ('有多少台有独显','各品牌数量对比','这个价算什么水平'):
    il._CACHE.clear()
    print(t, '->', il.route(t, focus='zol-2103842').get('intent'))
"
```

**期望**：
- 「有多少台有独显」→ `analyze`
- 「各品牌数量对比」→ `analyze`
- 「这个价算什么水平」→ `ask_current`（负例，不能被 analyze 抢走）

---

## 五、待解决的遗留问题

### 5.1 速度（P1）

| 输入类型 | 耗时 |
|---|---|
| 简单（无 CoT） | 3.2-3.7 秒 |
| 带 CoT | 10-11 秒 |

**CoT 让推理慢 3 倍**，对话体验会明显变差。

**可能的优化方向**：

| 方向 | 预期效果 | 代价 |
|---|---|---|
| CoT 只在低置信度时触发 | 大部分请求 3 秒 | 需要置信度估计 |
| 缩短 CoT 训练样本 | 输出变短、变快 | 可能损失判断质量 |
| 导出 GGUF（要解决 hosts 问题） | 2-3 秒 | 要能下载 llama.cpp |
| 减小 max_new_tokens | 略快 | 长 CoT 会被截断 |

**建议**：先重训看 `analyze` 效果，速度问题单独一轮优化。

### 5.2 v4 比 lora_out 低 2 个点

84.0% vs 86.0%。可能原因：
- 训练集变化（新增对抗样本 + 压缩后的 prompt）
- 压缩后的 prompt 信息量减少

**重训 v5 时一起看**，如果还是低，要对比分析那几条判错的。

### 5.3 两个服务不能同时跑

adapter 服务占 6 GB 显存，Ollama 也占。**8 GB 显存装不下两个。**

**当前选择**：
- 训练 → 停 adapter 服务、停 Ollama
- 推理 → 停 Ollama，只开 adapter 服务

---

## 六、本轮新增的文件

| 文件 | 作用 |
|---|---|
| `lora_server.py` | adapter 推理服务（兼容 Ollama API） |
| `seed_analyze.py` | 生成 analyze 意图训练样本 |
| `lora_data/analyze-seed.jsonl` | 63 条种子数据（55 正 + 8 负） |
| `build_route_lora.py`（改） | SOURCES 加 analyze-seed，LABELS 加 analyze |

---

## 七、给论文用的材料

这一轮有**三个可写的点**：

### 7.1 「规则兜底掩盖模型缺陷」的实证

`analyze` 意图的代码写好了、测试通过了、线上也能用 ——
但**模型从未学过这个类别**，全靠规则兜底。

**这是「测试通过 ≠ 能力具备」的典型例子**，
可以写进论文的「模型能力评估方法」一节。

**论证链**：
1. `test_analyze.py` 27 个断言全绿
2. 线上问「有多少台有独显」能得到正确答案
3. 但直接测模型（绕过规则）→ 判成 `ask_current`
4. 查训练集 → `analyze` 样本数 = 0

**结论**：功能测试只验证"系统能给对答案"，不验证"模型具备该能力"。
必须**单独测模型层**。

### 7.2 CoT 的推理代价量化

| 输入 | 耗时 | 是否输出 CoT |
|---|---:|---|
| 好的 | 3.20s | 否 |
| 换一台 | 3.71s | 否 |
| 这机器多重 | 10.78s | 是 |
| 有多少台有独显 | 11.52s | 是 |

**CoT 让推理慢约 3 倍**（3.2s → 10.8s）。

这是「思维链蒸馏」的**真实代价**，可以作为论文里的权衡讨论。

### 7.3 8GB 显存的硬约束

实测 `max_length` 上限：

| max_length | 结果 |
|---|---|
| 512 | ✓ 成功（显存峰值 11.76 GB，借用统一内存） |
| 640 | ✗ OOM |
| 768 | ✗ OOM |

**512 是硬上限**，这直接决定了 prompt 必须压到 385 token 以内。

这个约束链（显存 → max_length → prompt 长度 → 信息量）
是「在消费级硬件上做私有化部署」的真实工程约束，可以写进论文的
「部署可行性分析」一节。
