# 电脑选机助手（对话式）

> 用户说清预算和用途，系统给出一台具体机型、到手价，以及这台机器在**你这个用途下的短板**。
> 核心不是"信息量大"，是**可核对**：回答里每个数字都能追回数据或用户原话，追不回去的会被出口审计拦下重写。

当前数据：**223 台**可推荐机型（243 台机型池，其中 162 台有第三方跑分）· 数据快照 2026-11-02

**模型**：基于 Qwen3-4B-Instruct 做 LoRA 微调（`route-v5`），路由层意图判断准确率 **88.0%**（对抗测试 44/50）

---

## 这个仓库里有什么

| 路径 | 是什么 |
|---|---|
| `tools/agent/` | **主产品**：对话式选机助手（`core.py` 排序内核 + `agent.py` 组装与审计 + `server.py` + `index.html`） |
| `data/` | 所有子系统的数据源。**只读**，见 [CONTRIBUTING](CONTRIBUTING.md) |
| `tools/pick.py` 等 | 另一个子系统：按专业静态匹配（243 台 / 311 专业），与主产品不通用 |
| 根目录 `.html` | 数据看板 / 行情页，只读展示 |
| `docs/` | PRD、部署协作、数据口径、历史归档 |

> ⚠️ 仓库里有三个"看起来都像选电脑"的东西，别搞混：先看 [`项目地图.md`](项目地图.md)。
> `tools/agent/` 之外的东西和主产品**数据口径与代码完全不通用**。

---

## 一、跑起来（Windows 双击即可）

**双击项目根目录的 `启动助手.bat`**，从菜单里选一种模式就行：

```
1  本机自用      http://127.0.0.1:7860
2  局域网共享    同 WiFi 的设备都能连
3  公网测试链接  生成 https 链接，发给测试同学
```

不想看菜单，直接用：

| 双击这个 | 效果 |
|---|---|
| `一键公网链接.bat` | 直接生成公网链接 + 复制到剪贴板 |
| `停止服务.bat` | 停服务 + 隧道，释放端口 |
| `环境检查.bat` | 只检查 Ollama / 端口 / 隧道，不启动 |

**先决条件**：装好 Ollama 并导入模型（见下节）。
依赖只有 Python 3.10+ 标准库，**不用 pip install**。

### 模型准备（重要）

本系统用微调后的 `route-v5` 模型。有两种方式：

**方式 A：直接用基座（最快，准确率 84%）**

```bash
ollama pull qwen3.5-4b
```

**方式 B：用微调后的 v5（准确率 88%）**

模型文件较大（8 GB），未入库。需要时联系维护者获取 `route-v5-official.gguf`，然后：

```bash
# 1. 把 GGUF 放到 E:\models\route-v5-official.gguf
# 2. 用仓库里的 Modelfile 导入
cd E:\models
ollama create route-v5 -f Modelfile-v5

# 3. 验证
ollama list   # 应看到 route-v5:latest
```

> `Modelfile-v5` 模板见 `docs/归档-20261124/GGUF转换成功.md`。
> **不导入 v5 也能跑** —— 系统会自动降级到基座。

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `MODEL` | `route-v5` | Ollama 里的模型名（没有 v5 就设为 `qwen3.5-4b`） |
| `OLLAMA_URL` | `http://127.0.0.1:11434/api/chat` | 模型地址（Ollama 不在本机时改这里） |
| `PORT` | `7860` | 端口 |
| `HOST` | 空 | `0.0.0.0` = 允许外部访问 |
| `ACCESS_CODE` | 空 | 设了就要求访问码，公网暴露时**必须设** |

```bash
# 例：让别人连、且要访问码
ACCESS_CODE=mycode123 python server.py --lan

# 例：没有 v5 模型，用基座
MODEL=qwen3.5-4b python server.py
```

### 不想用启动器 / 其他系统

```bash
cd tools/agent
python server.py              # 只本机
python server.py --lan        # 允许局域网
```

> 详细说明（三种模式的区别、故障排查、换端口）：[`docs/启动与部署.md`](docs/启动与部署.md)

---

## 二、让别人用起来（三种方式，按场景选）

> 目标场景：**组内同学和测试同学都不装模型，直接连发起人这台机器。**

### 方式 A：同一局域网（最快，零配置）

```bash
python server.py --lan
# 打印出 http://192.168.x.x:7860 —— 同 WiFi 的人直接打开
```

### 方式 B：临时公网链接（推荐给测试同学，5 分钟）

用 Cloudflare 的临时隧道，不用账号、不用服务器、自带 https：

```bash
# 装一次（Windows: winget install Cloudflare.cloudflared 或下 exe）
cloudflared tunnel --url http://127.0.0.1:7860
# 输出形如 https://random-words-1234.trycloudflare.com
```

把 `https://xxx.trycloudflare.com/?k=访问码` 发出去即可。

⚠️ 临时隧道**每次重启都换域名**，而且**你关电脑/睡眠就断**。

### 方式 C：固定域名（长期给组内用）

Cloudflare Named Tunnel（需要自己的域名），或 frp + 一台小服务器。做法见
[`docs/部署与协作.md`](docs/部署与协作.md)。

### 三条必须知道的限制

1. **并发是 1**。本机一张显卡，多人同时提问会排队；第 2 个人会明显变慢，不是 bug。
2. **你的机器必须醒着**。睡眠/关机 = 链接失效（方式 B/C 都一样）。
3. **访问码别贴公开群**。它的作用只是"别让陌生人拿你的显卡当免费 API"。

---

## 三、架构：模型为主，规则兜底

这是本项目最重要的设计原则。

```
用户问句
   ↓
【模型层】意图判断（七类）
   ask_current / refuse_switch / state_range / state_new_need
   / list_others / analyze / unknown
   ↓
【确定性层】排序、筛选、聚合统计 —— 全部由代码算，模型无数字写权
   core.py（推荐排序） / analyze.py（统计分析）
   ↓
【审计层】22 条判据拦截
   编造数字 / 超证据断言 / 跨机型串台 / 品牌归属错误 / 模糊评价
   ↓
输出
```

**为什么这样设计**：

| 职责 | 谁做 | 为什么 |
|---|---|---|
| 理解用户说什么 | **模型** | 自然语言无穷变化，规则穷举不完 |
| 排序 / 筛选 / 统计 | **代码** | 必须确定性、可复现、可核对 |
| 表达成话 | **模型** | 需要自然语言生成能力 |
| 数字校验 | **代码** | 模型不可信，每个数字必须能追回数据 |

**模型没有「数字写权」和「排序权」** —— 这是系统可信的基础。

---

## 四、日志与训练数据（部署方视角）

每一轮都会落一行 JSON 到 `logs/chat-YYYY-MM-DD.log`，含：

```
uid（谁问的）· text · stage · slots · reply · warnings · issues · ms · ts
```

- `uid` 是浏览器本地生成的随机数（不是账号，不收集个人信息）；链接加 `?u=昵称` 可自定义
- `issues` 非空 = 出口审计当场检出问题（编造词 / 数据外数字 / 库外机型）
- 收样本：`python tools/agent/export_lora_data.py`（统计）/ `--write`（写出 `lora_data/train.jsonl`）

### 训练数据现状

| 项 | 数量 |
|---|---:|
| 路由层训练集 | **553 条**（其中 158 条带思维链，29%） |
| 路由层评估集 | 71 条 |
| 意图分布 | ask_current 190 / state_range 127 / state_new_need 96 / refuse_switch 50 / **analyze 49** / list_others 26 / unknown 15 |

> 训练集纪律在 [`tools/agent/样本积攒提示词.md`](tools/agent/样本积攒提示词.md)：
> **答案只能来自本系统**（自蒸馏）、**审计报过问题的轮次不进训练集**、
> **同句多轮取最后一次**（修复后的回答才算数）。

---

## 五、协作开发（组内提 PR）

流程与红线见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。三条最常踩的：

1. **`data/*.json` 零写权限** —— 数据由维护者核实，改代码不改数据
2. **排序只能在内核里算**（`core.py`），模型和前端都没有排序权与数字写权
3. **判据只写一遍** —— 同一个判据在第二条路径上抄一遍，早晚会漂（已复现 9 次）

### 一条命令跑全部测试

```bash
cd tools/agent
python run_all_tests.py           # 全部（需要 Ollama 在线）
python run_all_tests.py --fast    # 只跑不依赖模型的（约 30 秒）
python run_all_tests.py --only test_scope,test_analyze
```

**当前基线**：23 个测试，其中 18 个全绿，5 个有已知存量失败。

单跑某个测试：

```bash
python test_answer.py && python test_scope.py && python test_social.py
```

---

## 六、它明确不做什么

- 排行榜 / 综合评分 / 雷达图 —— 数据里没有"综合分"字段，给分就是编
- 帧数 / 续航时长 / 返修率 / 色准 —— 无实测数据，不预测
- 手机 / 平板 / 工作站 / Mac —— 明确拒答，不拿别的机型糊
- 登录注册、云同步、移动端 —— 现阶段不做

**这个系统不负责"知道得多"，只负责"说出口的每一句都站得住"。**

---

## 目录速查

```
tools/agent/
├─ core.py            排序内核（纯函数，确定性；模型无权改）
├─ agent.py           槽位解析 / 措辞 / 出口审计 / 字段卡 / 社交层
├─ analyze.py         数据分析引擎（8 种聚合操作）
├─ analyze_ctx.py     分析上下文（追问指代消解）
├─ intent_layer.py    意图分类（模型 + 规则兜底）
├─ paths.py           路径集中管理（可移植）
├─ server.py          HTTP 服务（7860）
├─ lora_server.py     可选：adapter 推理服务（兼容 Ollama API）
├─ index.html         单页前端（无框架、无构建）
├─ run_all_tests.py   一条命令跑全部测试（23 个测试）
├─ test_*.py          23 个测试文件，1300+ 项断言
├─ train_route_lora.py  LoRA 训练脚本（QLoRA 4bit）
├─ eval_lora.py       adapter 评估
├─ merge_lora.py      合并 adapter 到基座
└─ lora_data/         训练样本池（不入库）
data/                 数据源（只读）
docs/                 PRD、部署协作、数据口径、历史归档
docs/归档-*/          各阶段归档（含事故复盘、训练记录）
scripts/              launcher.bat（壳）+ 桌面快捷方式生成器
tools/launcher.py     启动器本体（3 种模式 + 停止 + 检查）
```

---

## 七、训练与部署说明（给想复现的同学）

### 硬件要求

| 项 | 最低 | 说明 |
|---|---|---|
| 显存 | **8 GB** | QLoRA 4bit 微调 4B 模型的上限（`max_length` 只能到 512） |
| 内存 | **15 GB** | 训练峰值 commit 约 11 GB |
| 磁盘 | 20 GB | 模型 8 GB + 合并后模型 8 GB |

### 训练（约 30 分钟）

```powershell
cd tools/agent
$py = 'E:\ai2026\Scripts\python.exe'   # 需要带 CUDA 的 Python

# ① 停 Ollama（占显存）
Get-Process -Name 'ollama','llama-server','ollama app' -ErrorAction SilentlyContinue | Stop-Process -Force

# ② 守门检查（commit < 13 GB 会拒绝，防蓝屏）
& $py safe_run.py

# ③ 验证 label 没被 mask（避免 28 分钟白跑）
& $py diagnose_zero_loss.py

# ④ 训练（这个 env 必须加，否则显存碎片化 OOM）
$env:PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True'
& $py train_route_lora.py --out lora_v6
```

### 部署（adapter → Ollama）

```powershell
# ① 合并 adapter 到基座（CPU，约 40 秒）
& $py merge_lora.py --adapter lora_v6

# ② 转 GGUF（需要 llama.cpp 的 convert_hf_to_gguf.py）
& $py convert_hf_to_gguf.py 'E:\models\Qwen3-4B-Instruct-2507-lora_v6' `
    --outfile 'E:\models\route-v6.gguf' --outtype f16

# ③ 导入 Ollama
cd E:\models
ollama create route-v6 -f Modelfile-v5
```

### 三条铁律（三次事故换来的）

| 铁律 | 事故 |
|---|---|
| **训练/推理的 system prompt 必须字节级一致** | 不一致会让模型输出 1219 字符死循环，无 JSON |
| **`max_length=512` 是 8GB 显存硬上限** | 640 就 OOM，所以 prompt 必须 ≤385 token |
| **同一进程不能连续加载模型** | `del`+`empty_cache()` 不归还 commit，会蓝屏 |

详见 [`docs/归档-20261122/训练事故-system prompt不一致.md`](docs/归档-20261122/训练事故-system%20prompt不一致.md)。

---

## 八、已知问题

| 问题 | 影响 | 状态 |
|---|---|---|
| 「啊啊啊」判成 `refuse_switch` | 无意义输入处理不对 | 已知，`unknown` 样本仅 15 条 |
| 「这个价算什么水平」判成 `state_range` | 应走 `ask_current` | 已知，影响有限 |
| 判据泄漏（特定场景） | 回答里泄漏内部指令 | 已知，v5 引入 |
| 带 CoT 时推理 1.6-2.2 秒 | 对话略有延迟 | 可接受 |
| `num_ctx 8192` 占显存 7.8 GB | 接近满载 | 建议降到 4096 |

**5 个存量测试失败**：`test_attack8` / `test_intent`（2 项）/ `test_answer` / `test_social` / `test_slot_hard`。

---

## 九、这个项目是怎么做出来的（给答辩/论文用）

完整技术演进记录在 `docs/归档-*` 里，几个关键节点：

| 时间 | 里程碑 |
|---|---|
| 11-16 | 项目地图整理，三个子系统分开 |
| 11-21 | 前端重构（1660 行 → 127 行 + 三个文件） |
| 11-21 | 新增 `analyze` 意图（数据分析能力） |
| 11-22 | 蓝屏事故（`0x3B`）→ 加内存守门 |
| 11-22 | 发现训练/推理 system prompt 不一致（差 39 倍） |
| 11-23 | loss 恒等于 0 → 定位为 label 全被 mask |
| 11-23 | 蓝屏事故（`0x1E`）→ 加同进程单加载限制 |
| 11-24 | v5 训练成功 88.0%，`analyze` 意图学到 |
| 11-24 | GGUF 转换：自写失败 → 官方脚本成功 |
| 11-24 | v5 部署到 Ollama，速度提升 8-14 倍 |

**三条可写的论文素材**：

1. **「规则兜底掩盖模型缺陷」的实证** —— `analyze` 意图代码写好、测试全绿、线上能用，但训练集样本数是 0，全靠规则兜底。
2. **「推理框架选择」的量化影响** —— 同样模型权重，transformers 4bit 18 秒 vs llama.cpp 1.6 秒，差 11 倍。
3. **「消费级硬件约束」的完整链** —— 8GB 显存 → max_length 512 → prompt 385 token → 砍掉详细定义。
