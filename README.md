# 电脑选机助手（对话式）

> 用户说清预算和用途，系统给出一台具体机型、到手价，以及这台机器在**你这个用途下的短板**。
> 核心不是"信息量大"，是**可核对**：回答里每个数字都能追回数据或用户原话，追不回去的会被出口审计拦下重写。

当前数据：**93 台**机型（22 台有人写过优缺点 / 71 台仅有参数）· 4 类 · 数据快照 2026-11-02

---

## 这个仓库里有什么

| 路径 | 是什么 |
|---|---|
| `tools/agent/` | **主产品**：对话式选机助手（`core.py` 排序内核 + `agent.py` 组装与审计 + `server.py` + `index.html`） |
| `data/` | 所有子系统的数据源。**只读**，见 [CONTRIBUTING](CONTRIBUTING.md) |
| `tools/pick.py` 等 | 另一个子系统：按专业静态匹配（69 台 / 311 专业），与主产品不通用 |
| 根目录 `.html` | 数据看板 / 行情页，只读展示 |
| `docs/` | PRD、部署协作、数据口径、历史归档 |

> ⚠️ 仓库里有三个"看起来都像选电脑"的东西，别搞混：先看 [`项目地图.md`](项目地图.md)。
> `tools/agent/` 之外的东西和主产品**数据口径与代码完全不通用**。

---

## 一、本地跑起来（需要本机有 Ollama）

```bash
# 1. 装模型（只做转述，4B 够用；越强的模型越想自由发挥）
ollama pull qwen3.5-4b        # 或任何你本地已有的模型，见下方 MODEL 环境变量

# 2. 起服务
cd tools/agent
python server.py
# 浏览器打开 http://127.0.0.1:7860
```

依赖：Python 3.10+，标准库即可（**不用 pip install**）。模型走 Ollama HTTP 接口。

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `MODEL` | `qwen3.5-4b` | Ollama 里的模型名 |
| `OLLAMA_URL` | `http://127.0.0.1:11434/api/chat` | 模型地址（Ollama 不在本机时改这里） |
| `PORT` | `7860` | 端口 |
| `HOST` | 空 | `0.0.0.0` = 允许外部访问 |
| `ACCESS_CODE` | 空 | 设了就要求访问码，公网暴露时**必须设** |

```bash
# 例：让别人连、且要访问码
ACCESS_CODE=mycode123 python server.py --lan
```

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

## 三、日志与训练数据（部署方视角）

每一轮都会落一行 JSON 到 `logs/chat-YYYY-MM-DD.log`，含：

```
uid（谁问的）· text · stage · slots · reply · warnings · issues · ms · ts
```

- `uid` 是浏览器本地生成的随机串（不是账号，不收集个人信息）；链接加 `?u=昵称` 可自定义
- `issues` 非空 = 出口审计当场检出问题（编造词 / 数据外数字 / 库外机型）
- 收样本：`python tools/agent/export_lora_data.py`（统计）/ `--write`（写出 `lora_data/train.jsonl`）
- 测试也算样本：`WB_SAMPLE_LOG=1 python test_xxx.py` → 每轮落到 `lora_data/auto-<日期>.jsonl`

> 训练集纪律在 [`tools/agent/样本积攒提示词.md`](tools/agent/样本积攒提示词.md)：
> **答案只能来自本系统**（自蒸馏）、**审计报过问题的轮次不进训练集**、
> **同句多轮取最后一次**（修复后的回答才算数）。

---

## 四、协作开发（组内提 PR）

流程与红线见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。三条最常踩的：

1. **`data/*.json` 零写权限** —— 数据由维护者核实，改代码不改数据
2. **排序只能在内核里算**（`core.py`），模型和前端都没有排序权与数字写权
3. **判据只写一遍** —— 同一个判据在第二条路径上抄一遍，早晚会漂（已复现 7 次）

改动提交前跑：

```bash
cd tools/agent
python test_answer.py && python test_scope.py && python test_social.py
python audit_data.py
```

完整回归（需要模型在线）见 `tools/agent/交接提示词-选机决策智能体.md`。

---

## 五、它明确不做什么

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
├─ server.py          HTTP 服务（7860）
├─ index.html         单页前端（无框架、无构建）
├─ test_*.py          886 项判定型测试（含社交层 59）
└─ lora_data/         训练样本池（不入库）
data/                 数据源（只读）
docs/                 PRD、部署协作、数据口径
scripts/              启动脚本
```
