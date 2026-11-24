# v5 上线尝试：GGUF 转换失败，回退 adapter 服务（2026-11-24）

> **结论：v5 用 adapter 服务跑通了（端到端 11/11），
> 但想换回 Ollama（快 3 倍）失败了 —— 自写 GGUF 转换器有未解决的 bug。**

---

## 一、最终采用的方案：adapter 服务

### 1.1 端到端验证 11/11 全绿

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

### 1.2 真实对话验证

```
Q: 有多少台有独显
A: 符合条件的共 129 台          ← 和引擎一致 ✓

Q: 各品牌数量对比
A: 按「品牌」共 15 组；联想：100 台；华硕：36 台；
   惠普：26 台；机械革命：20 台；荣耀：8 台；其余 10 组略
```

### 1.3 启动方式

```powershell
# 1. 停 Ollama（占显存）
Get-Process -Name 'ollama','llama-server','ollama app' -ErrorAction SilentlyContinue |
    Stop-Process -Force

# 2. 腾 commit（关浏览器）
Get-Process -Name 'msedge','Weixin' -ErrorAction SilentlyContinue | Stop-Process -Force

# 3. 启动（守门要求 commit >= 13 GB）
$env:PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True'
& 'E:\ai2026\Scripts\python.exe' lora_server.py
# → http://127.0.0.1:11435

# 4. 让 agent 指向它
$env:OLLAMA_URL='http://127.0.0.1:11435/api/chat'
```

**`agent.py` 的 `API` 已支持 `OLLAMA_URL` 环境变量**（11-13 就做了）。

---

## 二、速度问题（未解决）

| 输入类型 | 耗时 |
|---|---|
| 无 CoT（「好的」「换一台」） | **5.5 秒** |
| 带 CoT（「这机器多重」） | **18 秒** |

**瓶颈不在 prefill**（实测极短 system 反而更慢），而在**生成速度约 9 字符/秒**。

对比：Ollama（llama.cpp）通常 30-50 tok/s，**快 3-5 倍**。

**根因**：transformers 的 4bit 推理没有 llama.cpp 的优化。

---

## 三、GGUF 转换失败的完整记录

### 3.1 为什么必须试这条路

adapter 服务慢（18 秒），而 Ollama 只要 2-3 秒。
要换回 Ollama，必须把 v5 变成 GGUF。

### 3.2 尝试过的所有方案

| 方案 | 结果 |
|---|---|
| 下载 llama.cpp 官方转换脚本 | ❌ hosts 把 29 个 GitHub 域名指向 127.0.0.1 |
| Ollama 0.40 直接吃 safetensors | ❌ **只在 macOS（MLX）支持**，Windows 报 `MLX runtime is not available` |
| `llama-cpp-python` | ❌ 没有 Windows 预编译 wheel |
| `llama-cpp-bin` | ❌ 安装时 Windows 长路径报错 |
| **自己用 `gguf` 库写转换器** | ⚠️ 转换成功但推理崩溃 |

### 3.3 自写转换器踩的 6 个坑

| # | 坑 | 现象 | 修法 |
|---|---|---|---|
| 1 | `add_token_id` 方法不存在 | `AttributeError` | 改用 `add_bos_token_id` / `add_eos_token_id` |
| 2 | 词表大小不匹配 | `expected 151643, got 151936` | 补 `added_tokens`，再补空到 `config.vocab_size` |
| 3 | 缺 `token_type` / `scores` | `0xc0000409` 栈溢出 | 写 `add_token_types` + `add_token_scores` |
| 4 | **`rope_theta` 读不到** | 用默认 10000，实际 **5000000** | 从 `config.rope_parameters.rope_theta` 读 |
| 5 | **BFloat16 不支持** | `Got unsupported ScalarType BFloat16` | 先转 fp32 再转 fp16 |
| 6 | `add_bos_token` 缺失 | — | 补 `add_add_bos_token(False)` |

**每个坑都修了，但最终仍然 `0xc0000409`。**

### 3.4 决定性对照实验

**把未训练的基座模型（`Qwen3-4B-Instruct-2507`）也转成 GGUF，
导入 Ollama —— 同样崩溃。**

```
Error: 500 Internal Server Error: llama-server process has terminated:
exit status 0xc0000409: The system detected an overrun of a
stack-based buffer in this application.
```

**这证明问题在转换器本身，不在 adapter 合并、不在训练。**

### 3.5 还没查清的地方

| 可疑点 | 说明 |
|---|---|
| **tensor 顺序** | 标准 GGUF 是 `output_norm` 在前，我的是 `token_embd` 在前 |
| **F16 格式** | 能跑的标准模型都是 Q4_K_M（file_type=15），我的是 F16（file_type=1） |
| **tensor 字节布局** | 我只验证了 shape，没验证数据是否正确 |
| **缺少的元数据** | `general.type` / `general.version` 等 |

**最可疑的是「F16 在 Windows 上的兼容性」** ——
能跑的模型全是量化过的。

### 3.6 如果下次要继续

**优先级 1：写量化器，生成 Q4_K_M 而非 F16**

```python
# Q8_0 量化（最简单）
# 每 32 个 float → 1 个 fp16 scale + 32 个 int8
scale = max(abs(x)) / 127
q = round(x / scale)
```

`gguf` 库支持 `add_tensor(name, arr, raw_dtype=GGMLQuantizationType.Q8_0)`。

**优先级 2：对齐 tensor 顺序**

按标准 GGUF 的顺序写（`output_norm` → `token_embd` → `blk.0.*` → …）。

**优先级 3：找一台能上网的机器**

如果能访问 GitHub，直接下载 `llama.cpp/convert_hf_to_gguf.py`，
5 分钟解决问题。**这是最省事的路。**

---

## 四、清理

失败的产物已删除：

```
route-v5-f16.gguf    7.50 GB  → 删
base-test-f16.gguf   7.50 GB  → 删
Ollama 里的 route-v5 / base-test  → 删
```

**释放 15 GB 磁盘。**

保留的文件：

| 文件 | 用途 |
|---|---|
| `merge_lora.py` | 合并 adapter 到基座（成功，可复用） |
| `hf_to_gguf.py` | 自写转换器（有 bug，但代码留着备查） |
| `quantize_gguf.py` | 量化器（未完成，骨架留着） |
| `E:\models\Qwen3-4B-Instruct-2507-lora_v5` | 合并后的完整模型（7.7 GB，GGUF 转换的输入） |
| `verify_v5_e2e.py` | 端到端验证脚本（11 断言） |

---

## 五、当前可用状态

### 5.1 两条路

| 方案 | 速度 | 准确率 | 状态 |
|---|---|---|---|
| **adapter 服务（推荐）** | 5.5-18 秒 | **88.0%** | ✅ 可用 |
| Ollama 基座（现状） | 2-3 秒 | 84.0%（无 v5） | ✅ 可用 |

**两者不能同时跑**（8 GB 显存不够）。

### 5.2 启动脚本建议

写一个 `start_v5.py` 自动处理：停 Ollama → 检查内存 → 启动服务。

**（TODO：下次做）**

---

## 六、给论文的材料

### 6.1 「消费级硬件上的部署约束」—— 这是个真实的工程故事

**完整约束链**：

```
8 GB 显存
  → max_length 上限 512（640 就 OOM）
    → system prompt 必须压到 385 token
      → 只能保留七类意图的区分特征，砍掉详细定义

15.2 GB 内存
  → 训练 commit 峰值 11 GB
    → 必须关浏览器/微信才能训练
    → 必须加守门防蓝屏（已经蓝屏两次）

推理速度
  → transformers 4bit 约 9 字符/秒（18 秒/次）
    → 想换 llama.cpp 快 3-5 倍
      → 但 GGUF 转换在无网络环境下做不了
```

**这不是"配置问题"，是真实的资源约束。**

### 6.2 「自制工具链的风险」—— 诚实的负面结果

> 由于无法访问 GitHub，官方 GGUF 转换工具不可用。
> 本文尝试用 `gguf` 库自行实现转换器，修复了 6 个具体缺陷
> （词表补齐、token_type、rope_theta、BFloat16 等），
> 但最终产出的 GGUF 在 llama.cpp 加载时仍触发 `0xc0000409`
> 栈溢出。对照实验表明，连未训练的基座模型转换后同样崩溃，
> 证明缺陷在转换器而非模型本身。
>
> **结论：模型格式转换涉及大量隐含约定（张量布局、量化格式、
> 元数据完整性），自制转换器的可靠性远低于官方工具。
> 在资源受限环境下，应优先保障「可运行」而非「最优性能」。**

**这段可以写进论文的「局限性」或「工程挑战」一节**，
比只报喜不报忧更可信。

### 6.3 「降级策略」的设计

系统现在有**三级降级**：

| 级别 | 条件 | 行为 |
|---|---|---|
| 1 | adapter 服务可用 | 用 v5（88%），18 秒 |
| 2 | 只有 Ollama 基座 | 用 qwen3.5-4b（84%），2-3 秒 |
| 3 | 模型全挂 | 纯规则兜底 |

**这是「模型为主、规则兜底」原则的完整体现。**
