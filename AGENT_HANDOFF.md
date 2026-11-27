# 选机助手项目 · Agent 交接提示词

> 把这份文档全文发给新 agent，它就能接手这个项目。
> 项目路径：`E:\电脑涨价分析`

---

## 你的身份和任务

你是「选机助手」项目的维护者。这是一个**对话式电脑选购推荐系统**：用户说预算和用途，系统推荐具体机型或给出装机配置单。

**项目的立身之本**（违反即返工）：所有数字由代码算，模型只负责措辞；排序由确定性内核做，模型无排序权；库里没有的明说没有，绝不编造。

---

## 一、项目架构（5 分钟搞懂）

```
用户输入
   ↓
【意图层】intent_layer.py（974 行）
   · LoRA 模型（route-v8-q8，Qwen3-4B 微调）判意图
   · 7 种意图：ask_current 问这台 / refuse_switch 换台 /
     state_range 说范围 / state_new_need 加条件 /
     analyze 全库统计 / list_others 要清单 / unknown 没说清
   · 规则否决层（classify()）只兜底，正常不干预
   ↓
【槽位抽取】agent.py 的 SLOT_SYS
   · 模型抽 cat/budget/purpose/carry 等
   · purpose 7 种：office/gaming/dev/video/design/ai/threeD
   ↓
【分支路由】agent.py 的 _run()（约 7200 行）
   · recommend 推机型 / build_pc 装机 / analyze 统计 /
     fit 用途匹配 / followup 追问 / clarify 反问
   ↓
【内核】core.py（2024 行）
   · 确定性排序/过滤/打分，模型零参与
   ↓
【出口审计】_audit 系列 + check_outside_products
   · 22 条判据拦编造数字、库外机型、越界内容
```

**装机线**（11-26 新增）：
- `pc_build_intent.py` 判断是否要装机 + 渲染装机单
- `build_pc.py`（1000+ 行）生成配置，依赖顺序严格：
  CPU初选 → 显卡 → CPU平衡 → **主板** → 内存 → 硬盘 → 电源 → **散热器** → **机箱（最后，要装得下前面所有）**
- `pc_rules.py` 用途配置规则表（每个约束带理由）
- `vrm_rules.py` 主板供电校验
- `fit_rules.py` 机箱/显卡/散热器物理尺寸校验

**前端**（11-27 重构）：
- `/` 对话模式（index.html + static/app.js + style.css）
- `/wizard.html` 向导模式（5 步点选项，拼自然话走同一 /api/chat）
- 两个页面统一风格：蓝紫 #3d5afe / 无衬线 / 14px 圆角 / 卡片式
- app.js 零改动原则：它生成的 class（.turn/.say/.md-table/.sec/.kv 等）样式在 style.css 里，改样式不改 JS

---

## 二、当前状态（2026-11-27 上午）

### 意图准确率

```
主对抗集（150 条）   97.3%（146/150）
真实场景集（42 条）  意图 54.8% / 端到端行为 90.5%
```

**关键认知**：意图标签低但行为高，说明规则兜底救了 35 个点。
**不要试图把路由逻辑全交给模型**——预算算术/路由决策必须留在代码里。

### 服务

```
7860   主服务（MODEL=route-v8-q8）    启动：python server.py --lan
11434  Ollama
公网   restart_public.py（cloudflared 临时隧道，地址会变，
       存在 _public_url.txt）
```

### 数据

```
data/machine_pool.json   243 台机型（223 可推荐）
data/parts.json          465 个配件（9 类）
data/parts_fit.json      158 个配件的尺寸参数
data/majors.json         311 个专业
tools/agent/lora_data/   训练集 1381 条（route-train.jsonl）
```

---

## 三、铁律（踩过的坑，别再踩）

### 代码纪律

1. **判据只写一遍** —— analyze 的统计动作判断曾写两份，改一处漏一处。现在抽成 `_STAT_PAT` 常量共用
2. **有依赖的步骤顺序不能反** —— 今天踩了 4 次：散热器/主板都要在 CPU 平衡后选，机箱最后选
3. **改代码写 .py 脚本执行，禁止 PowerShell heredoc** —— heredoc 会吃引号、破坏字符，还曾把 build_pc.py 截成 0 字节（git checkout 救回）
4. **改 UI 后必须强刷验证** —— 浏览器缓存 CSS/JS，用 CDP 时 `Network.setCacheDisabled`

### 模型/数据纪律

5. **训练/推理 system prompt 必须字节级一致**（差 39 倍那次事故）
6. **`fit` 字段只在 intent=ask_current 时为 true** —— 曾经乱标导致模型把 intent 填成 "fit"
7. **训练数据标注矛盾 = 模型摇摆** —— 改标注前先查代码实际行为（refuse_switch 会触发换台，所以「有没有XX牌」该标它）

### 显式要求 vs 自动推断

8. **用户显式要求（要独显）优先于用途推断** —— build_plan 的 explicit_dgpu 参数
9. **但比例只在该抬时抬** —— gaming 本来就重显卡（0.53），不要抬到 0.77（会把 CPU 预算挤没了）；只有 dev/office（<0.35）才抬到 0.5

---

## 四、环境事实

```
Python      E:\ai2026\Scripts\python.exe（3.12，torch 2.13+cu126）
模型        E:\models\route-v8-q8.gguf（Ollama）
            基座 E:\models\Qwen3-4B-Instruct-2507
训练        train_route_lora.py --epochs 3 --out lora_out_vN（约 100 分钟）
            watch_training.py --log train-vN.log --follow 看进度
合并导出    merge_lora.py → convert_hf_to_gguf.py --outtype q8_0 → ollama create
Git 推送    网络不稳，失败就重试或换 IP：
            git -c http.curloptResolve=github.com:443:140.82.121.3 push origin main
            （用户有 GitHub 加速器 Watt Toolkit，开着时直接 push）
Node        C:\Users\23575\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe
            （验证 JS 语法：new Function(s)）
CDP 测试    Edge --remote-debugging-port=9333 --user-data-dir=E:\edge-cdp-profile
            shoot_ui.py 封装了 WebSocket 客户端
测试基线    test_scope 18/18、test_weight 28/28、test_source 64/64、
            test_analyze 27/27、test_multiturn 12/12、test_named 124/124、
            test_neg 144/144、test_intent 72/73（1 条历史遗留）
```

**PowerShell 陷阱**：`@"..."@` heredoc 会破坏嵌套引号 → 一律写临时 .py 文件再执行；`Out-File -Encoding utf8` 加 BOM；`$()` 里的 `&&` 要拆开。

---

## 五、测试怎么跑

```powershell
cd E:\电脑涨价分析\tools\agent

# 意图对抗测试（150 条主集）
python diagnose_intent_gaps.py
# 真实场景集（42 条）
python ..\..\deepseek harness tests\run_real_test.py
# 论坛风格集（56 条）
python ..\..\deepseek harness tests\run_forum_test.py
# 端到端（看最终 stage 合理性）
python ..\..\deepseek harness tests\e2e_real_test.py

# 回归
python run_all_tests.py --fast
```

**新对抗集**（`lora_data/adversarial-real.jsonl`）暴露了模型短板：
纯用途句（跑大模型/微调）判成 ask_current/unknown，
预算+用途句判成 state_range —— 规则兜住了行为（端到端 90.5%），
但意图层还差。**下一步可重训补这些样本。**

---

## 六、UI 结构（改样式前必读）

**app.js 生成的 class 必须有样式**，全清单：

```
消息流   turn / say / numlist / n / majorq / fadein
markdown md-h md-h1-4 / md-hr / md-quote / md-table / md-table-wrap
推荐卡   kv / chk ok no / banner / chipbox / conf / sec / bd /
         ubb / head / cnt / cmp / det / cand
数据概览 nowbox / rg / rg-body / rail-global / ov / cap / bk /
         r1 / tr / rv / rng-row / rng / rng-note
其他     wizard-strip / wizard-entry / offline
```

**布局关键**：
- `.dock` 里所有元素包在 `.dock-inner`（640px 居中）——不包的话 chips 会贴到 x=0 被侧栏挡住
- `.turn/.hero` 也是 640px 居中 —— 三者必须同宽对齐
- rail 收起挂 `body.rail-mini`，样式在 style.css 末尾
- textarea 高度=行高（padding:0）才是光标垂直居中

---

## 七、今天（11-26~27）做完的事

1. 配件库 348→465（固态 92、散热器 46 新抓）
2. 装机校验：VRM 供电 / 机箱尺寸 / CPU 平衡顺序
3. v8 训练：意图准确率 65.3% → 97.3%
4. 修 5 类意图 bug（_HARD_ANCHOR 用途词污染 / 老补丁失效 / 判据写两遍 / fit 字段污染 / 纯用途兜底）
5. 修「要独显被忽略」（三层：SLOT_SYS 枚举 / 词表归 ai / explicit_dgpu 通路）
6. 修预算自洽（gpu 上限 + 其他件按剩余分）
7. 修 analyze 极值答 8 万机 + 审计误报库外（查错数据源）
8. 对话页前端重构（向导风格）+ 向导页新建 + 互链
9. UI 打磨（表格卡片化 / 光标居中 / chips 对齐 / rail 收起 / 会话卡片 / 标题乱码）

**归档**：`docs/归档-20261127/收工-20261127.md`（完整时间线和教训）

---

## 八、已知未修（按优先级）

| 优先 | 问题 | 说明 |
|---|---|---|
| P1 | 真实场景集意图 54.8% | 行为 90.5% 兜住了，但意图层要补样本重训 |
| P1 | 日志无网页入口 | 只能 `python view_log.py` |
| P2 | needDgpu 模型字段 | 让模型判「是否显式要独显」替代正则（正则留着兜底），需重训 |
| P2 | M.2 插槽数校验 | 没做 |
| P3 | agent.py 7200 行拆分 | 15 步有状态依赖，拆错代价大，先补回归测试再动 |
| P3 | 20+ 文件硬编码路径 | 单机部署暂无碍 |

---

## 九、接到任务后的第一个动作

```powershell
# 1. 确认服务
cd E:\电脑涨价分析\tools\agent
python restart_public.py --check

# 2. 跑回归确认没坏
python run_all_tests.py --fast

# 3. 读归档了解最新状态
#    docs/归档-20261127/收工-20261127.md
```

**改任何东西之前**：先跑一次对应的对抗测试留基线，改完再跑对比。没有基线的改动等于盲改。

---

## 十、和用户沟通的风格

用户是中文，回复用中文。用户偏好：
- 短句、直接、不要客套
- 每次改动要说明**根因**和**为什么这么修**，不是"改好了"
- 诚实：做不到就说做不到，测试数字不粉饰
- 用户会实际使用并反馈问题（比如这次独显 bug 就是用户实测发现的），**用户报的问题优先级最高，立刻查日志复现**
