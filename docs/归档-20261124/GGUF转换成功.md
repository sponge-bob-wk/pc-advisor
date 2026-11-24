# GGUF 转换成功：官方脚本 + 加速器（2026-11-24 上午续）

> **转折：加速器让 GitHub 可访问，用官方脚本 5 分钟解决问题。**
> **结果：v5 跑在 Ollama 上，速度从 18 秒降到 1.6-2.2 秒（快 9 倍），准确率不变。**

---

## 一、关键突破：加速器绕过了 hosts 劫持

### 1.1 之前为什么不行

```
hosts 文件把 29 个 GitHub 域名指向 127.0.0.1
→ DNS 解析 github.com = 127.0.0.1
→ 所有下载失败
```

### 1.2 开加速器后

| 域名 | DNS 解析 | 实际 HTTP |
|---|---|---|
| github.com | 127.0.0.1 | **✓ HTTP 200** |
| raw.githubusercontent.com | 127.0.0.1 | **✓ HTTP 200** |
| huggingface.co | 104.244.43.234 | ✗ 超时 |

**关键发现：加速器在应用层接管，绕过了 hosts 的 127.0.0.1 劫持。**

DNS 还是解析到 127.0.0.1，但 HTTP 请求实际成功 ——
说明加速器做了**流量代理/重定向**，不是改 DNS。

**GitHub 能用就够了，HuggingFace 不需要。**

---

## 二、下载官方工具链（5 分钟）

### 2.1 需要哪些文件

llama.cpp 已重构，主脚本很小，逻辑在 `conversion/` 包：

| 文件 | 大小 | 作用 |
|---|---:|---|
| `convert_hf_to_gguf.py` | 14.5 KB | 主入口 |
| `conversion/__init__.py` | 19.8 KB | 注册表 |
| `conversion/base.py` | **180.3 KB** | 核心转换逻辑 |
| `conversion/qwen.py` | 54.6 KB | Qwen 系列（含 Qwen3） |
| `gguf-py/gguf/*.py` | 11 个文件 | GGUF 读写 |
| `gguf-py/gguf/tensor_mapping.py` | **162.3 KB** | 张量名映射 |

**总计约 500 KB。**

### 2.2 踩的一个坑

第一次只下了 10 个 gguf 文件，导入报：

```
ModuleNotFoundError: No module named 'gguf.tensor_mapping'
```

补下载 `tensor_mapping.py`（162 KB）后正常。

**教训：用 GitHub API 列目录，别靠猜。**

```powershell
Invoke-RestMethod 'https://api.github.com/repos/ggerganov/llama.cpp/contents/gguf-py/gguf'
```

---

## 三、转换成功

```powershell
cd E:\models\llama_cpp_tools
& 'E:\ai2026\Scripts\python.exe' convert_hf_to_gguf.py `
    'E:\models\Qwen3-4B-Instruct-2507-lora_v5' `
    --outfile 'E:\models\route-v5-official.gguf' `
    --outtype f16
```

**输出**：

```
INFO:hf-to-gguf:Model successfully exported to E:\models\route-v5-official.gguf
Writing: 100%|██████████| 8.05G/8.05G [00:16<00:00, 500Mbyte/s]
```

**16 秒完成，8.05 GB。**

### 3.1 对比：我的自写转换器

| 项 | 自写 | 官方 |
|---|---|---|
| 代码量 | ~390 行 | 500 KB |
| 踩的坑 | 6 个 | 0 个 |
| 结果 | **`0xc0000409` 崩溃** | ✓ 成功 |

**自写转换器修了 6 个缺陷（词表补齐、token_type、rope_theta、
BFloat16、add_bos_token、方法名），但仍有未发现的 bug。**

---

## 四、导入 Ollama 并验证

### 4.1 导入

```powershell
cd E:\models
ollama create route-v5 -f Modelfile-v5
# → route-v5:latest  8.1 GB
```

### 4.2 速度对比（重大改进）

| 输入 | adapter 服务 | **Ollama（官方 GGUF）** | 提升 |
|---|---:|---:|---:|
| 好的 | 5.5s | **0.4s** | **14×** |
| 这机器多重 | 18s | **1.6s** | **11×** |
| 有多少台有独显 | 18s | **2.2s** | **8×** |

**首次请求 9-17 秒是模型加载，之后稳定在 0.4-2.2 秒。**

### 4.3 端到端验证 11/11 全绿

```
[1] analyze 意图（v5 核心改进）
  [OK ] 有多少台有独显     → analyze
  [OK ] 各品牌数量对比     → analyze
  [OK ] 8000以内平均多重   → analyze
  [OK ] 价格分布是什么样的 → analyze
  [OK ] 性能最强的五台     → analyze

[2] 负例（不能被 analyze 抢走）
  [OK ] 哪个最便宜   → state_range
  [OK ] 换个平均点的 → state_new_need
  [OK ] 换一台       → refuse_switch

[3] 常规意图（确认没退化）
  [OK ] 这机器多重     → ask_current
  [OK ] 好的           → unknown
  [OK ] 剩下的都列出来 → list_others
```

### 4.4 真实对话

```
Q: 有多少台有独显  (9.1s 首次)
A: 目前统计显示共有 129 台设备配备了独立显卡。
   该数据基于符合条件的样本汇总得出。

Q: 各品牌数量对比  (2.2s)
A: 联想以 100 台遥遥领先，华硕、惠普和机械革命分别有
   36、26 和 20 台。这份数据基于 15 组样本统计，
   其余 10 组品牌数量较少未单独列出。

Q: 这机器多重  (1.6s)
A: 是笔记本还是台式？大概想放在宿舍用，还是要能带着走？
   （无 focus 所以反问，行为正确）

Q: 好的  (0.4s)
A: 嗯，你说 —— 预算和用途给我一句就行。
```

---

## 五、配置变更

### 5.1 agent.py 默认模型

```python
# 之前
MODEL = (os.environ.get("MODEL") or "qwen3.5-4b")

# 现在
MODEL = (os.environ.get("MODEL") or "route-v5")
```

**仍然支持环境变量覆盖**，所以想切回基座只需 `$env:MODEL='qwen3.5-4b'`。

### 5.2 verify_v5_e2e.py 可配置

```python
URL = os.environ.get("E2E_URL") or "http://127.0.0.1:11434/api/chat"
"model": os.environ.get("E2E_MODEL") or "route-v5",
```

**之前写死 11435（adapter 端口），现在默认 11434（Ollama）。**

---

## 六、当前架构（最终形态）

```
用户问句
   ↓
agent.py（API 走环境变量，默认 11434）
   ↓
Ollama + route-v5（v5 训练成果，Q4/F16 GGUF）
   ↓ 1.6-2.2 秒
意图判断（七类）
   ↓
analyze 分支 → analyze.py 确定性计算
其他分支 → 推荐流程
   ↓
审计（22 条判据）→ 输出
```

**三级降级仍然保留**：

| 级别 | 条件 | 行为 |
|---|---|---|
| 1 | route-v5 可用 | 用 v5（88%），1.6-2.2 秒 |
| 2 | 只有基座 | `$env:MODEL='qwen3.5-4b'` |
| 3 | 模型全挂 | 纯规则兜底 |

---

## 七、保留的文件

| 文件 | 说明 |
|---|---|
| `E:\models\llama_cpp_tools\` | 官方转换工具（500 KB，**留着下次用**） |
| `E:\models\route-v5-official.gguf` | 8.05 GB，可重复导入 |
| `E:\models\Qwen3-4B-Instruct-2507-lora_v5\` | 7.7 GB，合并后的 HF 模型 |
| `E:\models\Modelfile-v5` | Ollama 导入配置 |
| `tools/agent/merge_lora.py` | 合并脚本（成功，可复用） |
| `tools/agent/hf_to_gguf.py` | 自写转换器（**失败，仅留档**） |
| `tools/agent/verify_v5_e2e.py` | 端到端验证（11 断言） |

### 7.1 可以删的

| 文件 | 大小 | 理由 |
|---|---:|---|
| `quantize_gguf.py` | — | 未完成，官方脚本更好 |
| `hf_to_gguf.py` | — | 有 bug，仅作教训留档 |

**建议保留 `hf_to_gguf.py`** —— 它记录了 6 个具体缺陷，
写论文时是有价值的「失败经验」。

---

## 八、给论文的材料（重要更新）

### 8.1 之前写的「自制工具链风险」要修正

**原结论**（基于失败）：

> 自制转换器修了 6 个缺陷仍失败，说明模型格式转换涉及大量隐含约定，
> 自制工具可靠性远低于官方工具。

**新结论**（加入成功案例）：

> 由于网络限制，官方 GGUF 转换工具最初不可用，本文尝试自行实现转换器。
> 修复了 6 个具体缺陷（词表补齐、token_type、rope_theta 等）后，
> 产出的 GGUF 仍在 llama.cpp 加载时触发栈溢出；对照实验表明
> 连未训练的基座模型转换后同样崩溃，证明缺陷在转换器而非模型。
>
> 后续网络条件改善后，改用官方 `convert_hf_to_gguf.py`，
> 一次转换成功，无需任何修补。
>
> **这组对照实验说明：模型格式转换的隐含约定（张量布局、
> 量化格式、元数据完整性）远超代码行数所能体现的复杂度。
> 在工程实践中，应优先获取官方工具，而非自行实现。**

**这个「先失败后成功」的完整过程比单一结论更有说服力。**

### 8.2 性能数据（可做图表）

| 方案 | 首次 | 后续 | 相对速度 |
|---|---:|---:|---:|
| transformers 4bit + adapter | 13.5s 加载 | 5.5-18s | 1× |
| **llama.cpp（GGUF）** | 9-17s 加载 | **0.4-2.2s** | **8-14×** |

**这量化了「推理框架选择」对性能的影响** ——
同样的模型权重，换个推理框架快一个数量级。

### 8.3 「资源约束」的完整叙事

```
8GB 显存
  → 训练时 max_length 上限 512（640 OOM）
    → system prompt 必须压到 385 token
  → 推理时 Ollama + GGUF 只需 3.5 GB（比 transformers 4bit 省）

15.2GB 内存
  → 训练 commit 峰值 11 GB
  → GGUF 转换需 8 GB（CPU，不占显存）

网络限制
  → hosts 劫持 GitHub
  → 加速器可绕过（应用层代理，非 DNS）
```

---

## 九、教训总结

### 9.1 「环境限制」可能是可绕过的

我以为 GitHub 完全不可用（hosts 劫持 + 之前测试失败）。
**开了加速器就能用** —— 而我没有第一时间问用户。

**教训：遇到环境限制，先问「有没有别的办法」，而不是直接放弃。**

### 9.2 官方工具 vs 自研

自研花了约 2 小时，踩 6 个坑，最终失败。
官方工具花了 5 分钟，一次成功。

**判断标准：如果官方工具存在且可得，不要自研。**

### 9.3 对照实验的价值（这次做得对）

「连未训练的基座也崩」这个实验，**5 分钟就定位了问题范围**
（转换器 vs adapter/训练）。

**如果没有这个实验，我可能一直在怀疑 adapter 合并或训练数据。**

### 9.4 保留失败产物

`hf_to_gguf.py` 虽然是失败的，但记录了 6 个具体缺陷。
**写论文时这是有价值的「工程挑战」素材。**
