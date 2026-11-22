# 训练事故与根因：system prompt 不一致（2026-11-22 深夜）

> **这是本项目训练环节最严重的发现。**
> 前两版 adapter 能 work 是**运气**，不是设计。

---

## 一、事故现象

v3 训练完成（20.8 分钟，eval_loss 0.6022 → 0.1946，看起来很好），
但评估结果**比旧版更差**：

| adapter | 对抗测试准确率 | 平均输出 |
|---|---:|---:|
| 基座（无 adapter） | — | — |
| `lora_out`（11-18 训练） | **86.0%** | 90 字 |
| `lora_v3`（11-22 训练） | **82.0%** | **163 字** |

而且 v3 出现了**功能性崩溃**：

```
输入「好的」（应该返回 {"intent":"unknown",...}）
输出：1219 字符的思维链，无限重复同一句话，**一个 JSON 都没有**

末尾内容：
  「行，我拿去试」= ask_current（「行」是同意，「拿去试」是用途，不是换台）。
  「行，我拿去试」= ask_current（「行」是同意，「拿去试」是用途，不是换台）。
  「行，我拿去试」= ask_current（…）
```

「哈哈哈」同样返回 `None`。

---

## 二、排查过程（走了三条弯路）

### 弯路 1：怀疑 token 预算不够

v3 的输出被截断在 200 token，所以 JSON 出不来。

```
训练集思维链长度：中位 52 token，90 分位 107 token
加 JSON（约 30 token）= 82~137 token
推理预算 120 token → **90 分位样本必然截断**
```

**确实是个真 bug**，已修（`intent_layer` 120 → 400，`eval_lora` 200 → 400）。

但**修完还是不行** —— 给到 800 token 时，模型输出 1219 字符仍然没有 JSON，
只是把死循环写得更长。

### 弯路 2：怀疑训练数据有重复

统计发现训练集里 79 条完全相同的答案
（`{"intent": "ask_current", "topic": "none", "fit": true}`）。

但进一步查：**没有完全重复的（问,答）对** —— 是不同问题恰好同类答案，属正常。

### 弯路 3：怀疑 LoRA 权重发散

对比两个 adapter 的 `lora_B` 权重：

| adapter | 参数数 | min | max | std |
|---|---:|---:|---:|---:|
| `lora_out` | 17,694,720 | -0.1321 | 0.1658 | 0.0022 |
| `lora_v3` | 17,694,720 | -0.1390 | 0.1556 | 0.0022 |

**几乎完全一致，权重正常。**

---

## 三、真正的根因（决定性对比实验）

同一个 adapter，只换 system prompt：

| 组合 | 输出 |
|---|---|
| `lora_v3` + **新 SYS**（七类，含 analyze） | **无 JSON**，495 字符 |
| `lora_v3` + **旧 SYS**（六类） | **有 JSON**，252 字符 ✓ |

**问题不在模型，在 prompt。**

再对比训练和推理的 system prompt：

```
训练用 system:   56 字符
  "你是选机助手的路由器。只输出 JSON：{"intent":...,"topic":...,"fit":...}"

推理用 system: 2170 字符
  "用户在挑电脑，助手上一轮给他推荐了下面这台。现在用户接了一句话…
   【intent】七选一 ——
   - ask_current  问**这台**的细节…
   - refuse_switch 不想要这台…
   - analyze     问整个库的统计量…
   …（共 2170 字符）"

**差 39 倍，根本不是同一个 prompt。**
```

### 为什么之前两版能 work

**基座模型够强，能忽略这种不一致。**

它一直在"猜"任务是什么（从 user 那句话 + 极简 system 里推断），
恰好猜对了 —— 但这是**运气，不是设计**。

v3 之所以崩，是因为我加了 `analyze` 意图（六类→七类），
让"猜"的难度超过了临界点。

---

## 四、修复

### 4.1 训练集用和推理完全相同的 system prompt

`build_route_lora.py` 原来硬编码 56 字符的极简版：

```python
{"role": "system",
 "content": "你是选机助手的路由器。只输出 JSON："
            '{"intent":...,"topic":...,"fit":...}'},
```

改成从 `intent_layer` 直接取：

```python
def _sys_prompt():
    import intent_layer as il
    return il.SYS_TMPL.format(ctx="（当前讨论：zol-2103842）")
```

**为什么不在这里另写一份**：判据抄两遍一定会漂 ——
这个项目已经复现过 9 次（见 `audit_matrix.py` 的存在理由）。

### 4.2 验证

```
训练用 system: 2170 字符
推理用 system: 2170 字符
完全一致: ✓
```

### 4.3 顺带修的两个真 bug

| 问题 | 修法 |
|---|---|
| 推理 token 预算 120 装不下 CoT（90 分位 137 token） | `intent_layer` 120 → **400** |
| `eval_lora` 的 200 也装不下 | 200 → **400**（必须和推理一致，否则测的不是真实条件） |
| `eval_lora` 写死 `lora_out`，评估不到新 adapter | 加 `--adapter` 参数 |

---

## 五、教训（重要）

### 5.1 训练和推理的 prompt 必须字节级一致

这是**微调的基本前提**，但很容易漏 —— 因为训练脚本和推理脚本是分开写的，
两边各自维护一份 prompt 模板。

**检查方法**：

```python
train_sys = json.loads(open('route-train.jsonl').readline())['messages'][0]['content']
infer_sys = intent_layer.SYS_TMPL.format(ctx='…')
assert train_sys == infer_sys   # ← 必须通过
```

**建议把这条加进 `test_chain.py` 或训练前检查。**

### 5.2 指标好看不等于模型可用

v3 的 eval_loss 0.6022 → 0.1946（降 68%），**看起来比上一版还好**
（上一版 0.580 → 0.183）。

但它在「好的」这种最简单的输入上直接崩了。

**eval_loss 只衡量"在评估集上的拟合程度"，不衡量"输出格式是否合法"。**

### 5.3 报错要往上游找

三条弯路（token、数据重复、权重）都是**在下游找原因**。
真正的根因在**最上游的输入分布**。

**下次遇到"训练后反而变差"，第一件事应该是对比训练/推理的输入。**

### 5.4 基座模型强 ≠ 可以不管一致性

前两版能 work，掩盖了这个问题整整 4 天。

**"恰好能跑"是最危险的状态** —— 它不会报错，只会在某次改动后突然崩。

---

## 六、当前状态

### 6.1 已完成

| 项 | 状态 |
|---|---|
| 训练集 system prompt 与推理一致 | ✓ 2170 字符，已验证 |
| 推理 token 预算 400 | ✓ |
| `eval_lora --adapter` 参数 | ✓ |
| `preflight_train.py` 判据修正（commit 而非物理内存） | ✓ |
| `watch_training.py` 进度解析修正（四次才对） | ✓ |
| 训练集 497 条 / 144 条带思维链 | ✓ |

### 6.2 待做（明天）

**第一步：重训**

```powershell
cd E:\电脑涨价分析\tools\agent
$py = 'E:\ai2026\Scripts\python.exe'

# 1. 检查内存
& $py preflight_train.py

# 2. 关 Ollama（占显存）
Get-Process -Name 'ollama app','ollama','llama-server' -ErrorAction SilentlyContinue |
    Stop-Process -Force

# 3. 训练（必须带这个 env，否则显存碎片化会 OOM）
$env:PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True'
& $py train_route_lora.py --out lora_v4

# 4. 评估（对比 lora_out 的 86.0%）
& $py eval_lora.py --suite adversarial --adapter lora_v4
```

**预期**：这次 system prompt 一致了，v4 应该至少不低于 86.0%，
且不会再出现「好的」返回 `None` 的问题。

**第二步：如果 v4 还不行**

说明问题不在 prompt 一致性，要考虑：
- 训练集质量（144 条 CoT 是不是教歪了）
- LoRA 秩（r=16 是否太小）
- 学习率（2e-4 是否太高）

**第三步：加一条自动检查**

在训练前断言 `train_sys == infer_sys`，防止再次踩坑。

---

## 七、本轮其他产出

### 7.1 价格可疑机器的裁决（15 台）

结论：**没有一台是真错，全是口径混用。**

```
同一台机器、同一时刻、同一页面：
  zolRef      = 8489   ← 参考报价
  merchantMin = 8404   ← 经销商价（和 zolRef 只差 1%）
  jd          = 11799  ← 京东平台价（高 39%）
```

而且 **15 台全部 `real = null`（没有到手价）** ——
数据自己标注了「非到手价，本轮未取得」。

该改的是判定规则（分级比较），不是数据。详见 `价格可疑机器裁决.md`。

### 7.2 训练进度可视化（修了四次）

日志里有**三种进度条**，前三次判据都错：

| 尝试 | 判据 | 拿到什么 |
|---|---|---|
| 1 | 取最后一个 | eval（9/9，显示"100% 完成"） |
| 2 | 分母最大 | 模型加载（398/398，显示"0%"） |
| 3 | s/it + 分母最大 | 还是加载（刚开始加载也是 s/it） |
| 4 | **剔掉 `Loading weights` 前缀 + 分组取最后** | ✓ 正确 |

**教训：数值特征（单位、分母）会撞车，语义标记（前缀）才可靠。**
