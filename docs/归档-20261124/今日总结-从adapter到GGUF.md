# 今日总结：从 adapter 服务到官方 GGUF（2026-11-24）

> **一天走了完整的技术弯路：失败 → 绕路 → 成功 → 发现新问题。**
> 最终：v5 跑在 Ollama 上，速度快 8-14 倍，但有 1 个新发现的退化。

---

## 一、最终成果

### 1.1 v5 成功部署到 Ollama

```powershell
# 启动
$exe = "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
Start-Process $exe
# 模型已导入：route-v5:latest（8.1 GB）

# 使用（agent.py 默认已指向 route-v5）
cd E:\电脑涨价分析\tools\agent
python server.py    # → http://127.0.0.1:7860
```

### 1.2 性能对比（重大改进）

| 输入 | adapter 服务 | **Ollama + GGUF** | 提升 |
|---|---:|---:|---:|
| 好的 | 5.5s | **0.4s** | 14× |
| 这机器多重 | 18s | **1.6s** | 11× |
| 有多少台有独显 | 18s | **2.2s** | 8× |

**首次请求 9-17 秒是模型加载，之后稳定。**

### 1.3 端到端验证 11/11 全绿

```
analyze 意图 5/5 全对（v5 核心改进）
负例 3/3 没被抢走
常规意图 3/3 没退化
```

### 1.4 真实对话

```
Q: 有多少台有独显  (9.1s 首次)
A: 目前统计显示共有 129 台设备配备了独立显卡。

Q: 各品牌数量对比  (2.2s)
A: 联想以 100 台遥遥领先，华硕、惠普和机械革命分别有
   36、26 和 20 台。

Q: 7000以内打游戏  (2.2s)
A: 这台是暗影精灵11 锐龙版，到手价 6687 元，配置是
   R9-8945HX 处理器、16GB 内存、1TB 固态、RTX 5060 显卡…
```

---

## 二、关键突破：加速器 + 官方脚本

### 2.1 之前卡在哪

| 方案 | 结果 |
|---|---|
| 下载 llama.cpp 官方脚本 | ❌ hosts 把 29 个 GitHub 域名指向 127.0.0.1 |
| Ollama 0.40 吃 safetensors | ❌ 只在 macOS（MLX）支持 |
| llama-cpp-python | ❌ 无 Windows wheel |
| llama-cpp-bin | ❌ 长路径报错 |
| **自写 gguf 转换器** | ⚠️ 修 6 个坑后仍 `0xc0000409` 崩溃 |

### 2.2 加速器做了什么

**关键观察**：

```
DNS 解析: github.com → 127.0.0.1（hosts 仍生效）
HTTP 请求: ✓ HTTP 200（成功）
```

**加速器在应用层代理，绕过了 hosts 的 127.0.0.1 劫持。**
不是改 DNS，是流量重定向。

### 2.3 下载了什么（500 KB）

| 文件 | 大小 |
|---|---:|
| `convert_hf_to_gguf.py` | 14.5 KB |
| `conversion/__init__.py` | 19.8 KB |
| `conversion/base.py` | **180.3 KB** |
| `conversion/qwen.py` | 54.6 KB |
| `gguf-py/gguf/*.py` | 11 个文件 |
| `gguf-py/gguf/tensor_mapping.py` | **162.3 KB** |

**踩的坑**：第一次漏了 `tensor_mapping.py`，报
`ModuleNotFoundError: No module named 'gguf.tensor_mapping'`。
**教训：用 GitHub API 列目录，别靠猜。**

### 2.4 转换一次成功

```powershell
& 'E:\ai2026\Scripts\python.exe' convert_hf_to_gguf.py `
    'E:\models\Qwen3-4B-Instruct-2507-lora_v5' `
    --outfile 'E:\models\route-v5-official.gguf' `
    --outtype f16
# → Model successfully exported（16 秒，8.05 GB）
```

**自写转换器 vs 官方**：

| 项 | 自写 | 官方 |
|---|---|---|
| 代码量 | ~390 行 | 500 KB |
| 踩的坑 | 6 个 | 0 个 |
| 结果 | **崩溃** | ✓ 成功 |

---

## 三、发现的两个新问题

### 3.1 `num_ctx 2048` 太小（已修）

**现象**：`test_e2e` 10 个用例报 `HTTP 400`。

**错误**：

```
request (2505 tokens) exceeds the available context size (2048 tokens)
```

**根因**：推荐流程的 prompt（`ANSWER_SYS` + 数据 JSON）超过 2048 token。

**修法**：Modelfile 的 `num_ctx` 改 8192。

**⚠️ 副作用**：`num_ctx=8192` 让 Ollama 占显存 7817/8188 MiB，
**回归测试从 15 分钟涨到 30 分钟还没跑完**（已中止）。

**待优化**：可以试 4096（够用且省显存）。

### 3.2 判据泄漏（未修）

**现象**：

```
Q: 我预算六千，学设计的
A: 这台是 MateBook 14 2026，6299 元。没有用户写过优缺点，
   所以只说配置和价格。注意：这句没有说「屏幕好」「色域强」，
   也没有提「适合设计」——那是用户的事，不是我的事。
   ⚠…
```

**模型把 `ANSWER_SYS` 里的指令当成了要说的话。**

**审计部分生效**：`fabricated_words: ['色域']` 抓到了，
但没拦住整句（因为「色域」在数据里出现过，只是不该说）。

**影响范围**：测了 3 个推荐请求，**只有 1 个泄漏**（专业相关 + 无评价数据时触发）。

**这是 v5 的退化** —— 基座没这个问题。

**可能的修法**：
1. 在 `_audit` 里加「判据泄漏」检测（关键词：`那是用户的事`/`不是我的事`/`注意：这句`/`我只陈述`）
2. 训练集清理（去掉 CoT 里的判据说明）
3. 重写 `ANSWER_SYS` 措辞

### 3.3 回归结果不完整

**中止前的结果**（`num_ctx=2048` 时）：

```
通过 6 个 | 失败 12 个
断言合计: 696 / 720
```

**但 12 个失败里，10 个是 `num_ctx` 导致的 400**（已修）。

**`num_ctx=8192` 后没跑完**（太慢）。

**下次必须补跑**，确认修完 400 后还剩多少失败。

---

## 四、当前配置

### 4.1 文件

| 文件 | 用途 |
|---|---|
| `E:\models\route-v5-official.gguf` | 8.05 GB，GGUF 模型 |
| `E:\models\Modelfile-v5` | Ollama 导入配置（`num_ctx 8192`） |
| `E:\models\llama_cpp_tools\` | 官方转换工具（500 KB） |
| `E:\models\Qwen3-4B-Instruct-2507-lora_v5\` | 7.7 GB，合并后的 HF 模型 |

### 4.2 代码变更

| 文件 | 变更 |
|---|---|
| `agent.py` | `MODEL` 默认值 `qwen3.5-4b` → **`route-v5`** |
| `verify_v5_e2e.py` | 端口可配置（`E2E_URL` / `E2E_MODEL`） |
| `lora_server.py` | 版本号动态读 adapter 名 |

### 4.3 Ollama 里的模型

```
route-v5:latest         8.1 GB   ← 当前默认
qwen3-4b-text:latest    2.5 GB
qwen3.5-4b:latest       3.0 GB   ← 可回退（$env:MODEL='qwen3.5-4b'）
```

---

## 五、下次开工

### 5.1 必须做

**① 补跑回归**（`num_ctx=8192` 后没跑完）

```powershell
# 先把 num_ctx 降到 4096 提速
# 编辑 E:\models\Modelfile-v5，改 num_ctx 4096
ollama rm route-v5
ollama create route-v5 -f E:\models\Modelfile-v5

cd E:\电脑涨价分析\tools\agent
& 'E:\ai2026\Scripts\python.exe' run_all_tests.py
```

**预期**：修完 400 后，失败数应该回到 5 个左右（存量问题）。

**② 修判据泄漏**

在 `_audit` / `_f_audit` / `_bad_answer` 里加检测：

```python
LEAK_MARKERS = (
    "那是用户的事", "不是我的事", "注意：这句",
    "我只陈述", "按规则", "根据判据", "这句没有说",
)
def _leak(t):
    return any(k in t for k in LEAK_MARKERS)
```

### 5.2 可选

| 任务 | 说明 |
|---|---|
| 量化成 Q4_K_M | 8 GB → 2.5 GB，显存省一半，速度更快 |
| 补 `unknown` 样本 | 修「啊啊啊」→ refuse_switch |
| 删 `hf_to_gguf.py` | 失败的转换器，但留着当论文素材 |

### 5.3 量化命令（如果要做）

```powershell
# 官方工具支持量化
cd E:\models\llama_cpp_tools
& 'E:\ai2026\Scripts\python.exe' convert_hf_to_gguf.py `
    'E:\models\Qwen3-4B-Instruct-2507-lora_v5' `
    --outfile 'E:\models\route-v5-q4.gguf' `
    --outtype q8_0
```

**或用 Ollama 自带的量化**（需要重新导入 safetensors，Windows 不支持）。

---

## 六、给论文的材料

### 6.1 「先失败后成功」的完整对照实验（最有价值）

> 由于网络限制，官方 GGUF 转换工具最初不可用，本文尝试自行实现转换器。
> 修复了 6 个具体缺陷后（词表补齐 151936、`token_type` 元数据、
> `rope_theta` 从 `rope_parameters` 读取（5000000 而非默认 10000）、
> BFloat16 转换、`add_bos_token`、方法名修正），
> 产出的 GGUF 仍在 llama.cpp 加载时触发 `0xc0000409` 栈溢出。
>
> **对照实验**：将未训练的基座模型同样转换，导入后同样崩溃 ——
> 证明缺陷在转换器而非模型。
>
> 后续网络条件改善后，改用官方 `convert_hf_to_gguf.py`，
> **一次转换成功，无需任何修补**（16 秒，8.05 GB）。
>
> **结论**：模型格式转换的隐含约定（张量布局、量化格式、
> 元数据完整性）远超代码行数所能体现的复杂度。
> 自制转换器修复了 6 个显式缺陷仍有未发现的隐式缺陷，
> 而官方工具零缺陷。
> **在工程实践中，应优先获取官方工具，而非自行实现。**

### 6.2 「推理框架选择」的量化影响

| 方案 | 首次 | 后续 | 相对 |
|---|---:|---:|---:|
| transformers 4bit + PEFT adapter | 13.5s | 5.5-18s | 1× |
| **llama.cpp（GGUF）** | 9-17s | **0.4-2.2s** | **8-14×** |

**同样的模型权重，换推理框架快一个数量级。**
这说明「私有化部署」的性能瓶颈往往不在模型本身，
而在推理引擎的优化程度。

### 6.3 「资源约束」的完整链

```
8GB 显存
  ├─ 训练: max_length 上限 512（640 就 OOM）
  │   └─ system prompt 必须压到 385 token
  ├─ 推理: num_ctx 8192 会让 Ollama 占 7817 MiB（几乎满载）
  │   └─ 副作用：测试速度慢一倍
  └─ 量化是唯一出路（Q4_K_M: 8GB → 2.5GB）

15.2GB 内存
  ├─ 训练 commit 峰值 11 GB（必须关浏览器）
  ├─ GGUF 转换需 8 GB（CPU，不占显存）
  └─ 已蓝屏两次，加了守门

网络限制
  ├─ hosts 劫持 29 个 GitHub 域名
  └─ 加速器可绕过（应用层代理，非 DNS 层）
```

### 6.4 诚实的局限性说明

> 本系统在 8 GB 显存的消费级硬件上完成训练与部署，
> 过程中遇到三类资源约束（显存、内存、网络），
> 均已通过工程手段解决，但留下了可观测的性能代价：
>
> 1. **prompt 压缩**：为适配 512 token 的 max_length，
>    路由层 system prompt 从 2170 字符压缩到 562 字符，
>    损失了部分意图定义的细节描述。
> 2. **上下文窗口受限**：`num_ctx` 需在「够用」和「省显存」间权衡，
>    8192 会导致显存接近满载并拖慢测试。
> 3. **未量化部署**：当前使用 F16 GGUF（8 GB），
>    若量化到 Q4_K_M（2.5 GB）可显著降低显存占用，但未验证精度损失。

---

## 七、今日时间线

| 时间 | 事件 |
|---|---|
| 08:10 | 开工，检查状态 |
| 08:15 | 启动 adapter 服务（v5） |
| 08:20 | 端到端 11/11 全绿（adapter） |
| 08:25 | 实测速度 5.5-18 秒，太慢 |
| 08:30 | 决定换回 Ollama |
| 08:35 | 试 5 种 GGUF 方案，全失败 |
| 08:40 | 自写转换器，修 6 个坑 |
| 08:45 | 仍报 0xc0000409 |
| 08:50 | 对照实验：基座也崩 → 确认转换器问题 |
| 08:55 | 回退 adapter 服务 |
| 09:05 | **开加速器，GitHub 可用** |
| 09:10 | 下载官方工具链 |
| 09:15 | **转换成功** |
| 09:20 | 导入 Ollama，速度 0.4-2.2 秒 |
| 09:30 | 端到端 11/11 全绿（Ollama） |
| 09:35 | 完整回归：696/720（12 失败） |
| 09:45 | 定位 `num_ctx 2048` 太小 |
| 09:50 | 改 8192，E01 修好 |
| 10:00 | 发现判据泄漏 |
| 10:20 | 回归跑 30 分钟未完（8192 太慢），中止 |
| 10:25 | 归档 |
