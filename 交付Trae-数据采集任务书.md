# 交付 Trae 的数据采集任务书

> 生成时间：2026-11-11
> 适用对象：字节跳动 Trae（或任何具备「读写本地文件 + 执行命令 + 控制浏览器」能力的编码智能体）
> 项目根目录：`E:\电脑涨价分析\`（**绝对不要放 C 盘**）

---

## 第一部分 · 怎么用这份文档

1. 把 **第二部分「可直接粘贴的提示词」** 整段复制，粘进 Trae 的对话框，作为首条指令。
2. Trae 会自己读第三部分（附录）。你只需要在它汇报时按 §3.8 验收。
3. 如果 Trae 说"我没有浏览器控制能力"，把它引到 **§3.4.0 路径 B（CDP 接管）**，那是纯终端方案，任何 IDE 智能体都能做。
4. **一次不要让 Trae 干超过 25 台。** 按批次来，每批结束让它出回报，你抽验 1~2 台。

---

## 第二部分 · 可直接粘贴的提示词

**（以下整段复制给 Trae，一个字都不用改）**

```
你是一个数据采集执行方。任务：为「按专业选笔记本电脑」决策工具补全机型数据。
工作目录固定为 E:\电脑涨价分析\ ，所有产出必须落在 E 盘，不许写 C 盘。

━━ 第一步：先读交接文档，不要凭猜测开工 ━━
读这三个文件（按顺序）：
  1. E:\电脑涨价分析\AGENTS.md            ← 项目全貌、字段语义、19 条血泪坑、红线
  2. E:\电脑涨价分析\交付Trae-数据采集任务书.md  ← 本任务书，§3 是你的作业手册
  3. E:\电脑涨价分析\data\machine_pool.json   ← 目标数据（71 台机型）
读完先向我复述：现在缺哪三类字段、各缺多少台。复述不对就先别动手。

━━ 第二步：确认你的能力边界，然后选路径 ━━
路径 A：你有浏览器控制工具（能操作真实浏览器窗口）
        → 用它控你自己的 Edge/Chrome，但必须先确认「已登录京东 + 窗口在前台」
路径 B：你没有浏览器控制工具，只有终端
        → 用 Playwright 连本机 Edge 的远程调试端口，复用已登录会话
        → 具体命令见任务书 §3.4.0，照抄即可
任何情况下都不许用：无头模式(首次)、无痕窗口、内置预览浏览器、直连 item.jd.com 的裸 HTTP 请求。
理由：没有 cookie 和登录态的请求等于自报家门，京东会直接把你重定向到风控页。已经有人踩过。

━━ 第三步：按作业单干活，一次只干一类 ━━
作业单在任务书 §3.7，优先级：
  P0  12 台缺 ports（接口清单）—— 这个卡住了 7 个专业的判定，最值钱
  P1  7 台缺 cpuId（CPU 型号归一化）—— 影响跑分匹配
  P2  35 台缺 efficiency（能效等级）—— 只影响国补，不影响推荐结论
  P3  3 台价格待核 + 1 台 CPU 型号需改
不要跳着做，P0 没做完不要碰 P2。

━━ 第四步：每采一条，当场落盘 ━━
用 python 脚本读写 data/machine_pool.json，不要手工编辑 JSON 文本
（手工编辑容易在逗号、转义、编码上出错，而且改前改后无法对比）。

改完立刻跑校验（路径写死，不依赖当前目录）：
  python "E:\电脑涨价分析\tools\coverage.py"
对比改动前的数字。数字掉了就是改坏了，回滚。

━━ 五条铁律（违反任何一条，这批数据全部作废）━━
1. 价格只认京东商品页。ZOL / 什么值得买 / IT之家 / 学生机清单的报价一律不用。
   实测过：学生机清单普遍低 300~3400 元，ZOL 普遍高 1600 元，方向相反且量级很大。
2. 未取证不许填数字。查不到就写进 dataGaps，不要"估一个合理值"。
3. 不适用 ≠ 缺失。核显的 vramGB 是 0 不是 null；查不到就是 null，不是 0。
4. 单核和多核跑分必须来自同一次测试。跨来源拼接会造出根本不存在的芯片表现。
5. 遇到风控立刻停手，把截图和当前 URL 报给我，不要重试、不要绕验证、不要换 IP。

━━ 回报格式（每批结束必须给）━━
批次 | 处理台数 | 成功/失败/存疑 各几台
每条记录：机型名 + 改了哪些字段 + 每个字段的来源（哪个页面/哪张截图）
存疑的单独列：怀疑什么、为什么怀疑、下一步怎么证
最后附 coverage.py 改动前后的两个数字
```

---

## 第三部分 · 需求说明书（附录，Trae 按需查）

### 3.1 这个项目在干什么

给准大学生做「**按专业选笔记本**」的推荐。核心方法是一条推导链：

```
专业 → 该专业要上什么课 → 课要用什么软件 → 软件的厂商官方系统要求 → 硬件门槛
```

每一跳都必须能指回原文。所以数据不是为了"看起来全"，而是为了**每一个推荐结论都能被追溯**。

**关键结论（已验证，别推翻）**：同一个专业名，不同学校的课程不同，门槛就不同。
长沙理工 / 海南大学 / 浙工大的计算机专业，门槛分别是「32G 核显够」「32G + 8G 显存必须独显」「32G GPU 可选」。
所以不能按学科大类一刀切。

**当前规模**：71 台机型 / 311 个专业 / 10 个软件栈组 / 28 颗 CPU 跑分 / 710 个「机型×专业组」判定对，
其中能出明确结论 96.1%，能推荐 50.3%。**50.3% 低是市场现实**（32G + 独显的机器本来就少），不是数据不够——不要为了数字好看放宽门槛。

---

### 3.2 数据存哪里（硬性）

| 内容 | 绝对路径 | 说明 |
|---|---|---|
| **机型池（主表）** | `E:\电脑涨价分析\data\machine_pool.json` | 71 台机型，所有规格+价格 |
| **CPU/GPU 跑分** | `E:\电脑涨价分析\data\bench.json` | 以芯片 id 为主键，28 CPU + 6 GPU |
| **专业与软件要求** | `E:\电脑涨价分析\data\majors.json` | 311 专业 / 10 组 / 20 条软件要求 |
| **字段级别矩阵** | `E:\电脑涨价分析\data\field_matrix.json` | 每个字段在每组是 R/H/-/P |
| **原始采集留档** | `E:\电脑涨价分析\data\crawl\<批次名>\` | 每台一个目录，含截图 + 原始文本 |
| **截图** | `E:\电脑涨价分析\data\crawl\<批次名>\*.png` | 1080p 以上，能看清价格和型号 |
| **覆盖率报告** | `E:\电脑涨价分析\data\coverage.json` | 由 `tools/coverage.py` 生成，不要手改 |
| **交接文档** | `E:\电脑涨价分析\AGENTS.md` | 每批干完要更新它 |

**绝对禁止**：
- 不许新建 `C:\Users\...\` 下的数据副本作为唯一副本
- 不许覆盖式写入而不备份（改前 `cp machine_pool.json machine_pool.json.bak-YYYYMMDD`）
- 不许把截图放桌面或临时目录然后不归档

**为什么这么严**：2026-11-11 C 盘工作区被整体重建，83KB 价格数据 + 438 项回归测试 + 139KB 报告全丢，回收站里都没有。

---

### 3.3 需要什么数据

#### 3.3.1 机型（`machine_pool.json` → `machines[]`）

顶层结构：`{meta, stats, machines: [...]}`。**不要改成裸数组**，工具都按这个结构读。

```jsonc
{
  "id": "spec-legion-y9000p-2026",      // 唯一 id，稳定不改
  "name": "拯救者 Y9000P 2026",          // 展示名
  "brand": "联想",
  "cat": "gaming",                       // th 轻薄 / gaming 游戏 / desktop 台式

  "cpu": {
    "model": "Core Ultra 9 290HX Plus",  // 原始型号，保留厂商写法
    "vendor": "Intel",                   // Intel / AMD / Apple
    "cores": 24, "threads": 24,
    "baseW": 55, "maxW": 160,
    "coreSrc": "同代 275HX 官方 24核；290HX Plus 待核"  // 核心数存疑时必填
  },

  "ram": {
    "gb": 16,                            // 出厂容量，判定只认这个，不看能否扩展
    "type": "DDR5",                      // DDR5 / DDR4 / LPDDR5X / 统一内存
    "config": "16GB×1",                  // 单条还是双条，影响双通道
    "form": "sodimm",                    // soldered 板载 / sodimm 可换 / lpcamm2
    "slots": 2, "maxGB": 64
  },

  "storageGB": 1024,

  "gpu": {
    "model": "RTX 5060", "vendor": "NVIDIA",
    "vramGB": 8,                         // 核显填 0，不是 null
    "type": "discrete",                  // discrete / integrated
    "tgpBaseW": 100, "tgpMaxW": 115,
    "boost": "Dynamic Boost 2.0"
  },

  "screen": {
    "sizeInch": 16,
    "widthPx": 2560, "heightPx": 1600,
    "refreshHz": 240,
    "panel": "OLED",                     // IPS / OLED / MiniLED / VA
    "colorGamut": {"dciP3": 100},        // 或 {"srgb": 100} 或 {"ntsc": 72}
    "brightnessNit": 500
  },

  "batteryWh": 80,
  "weightKg": 2.5,
  "ports": ["USB-A ×3", "USB-C ×2（1个全功能140W PD）", "HDMI 2.1", "RJ45 千兆", "3.5mm 音频"],

  "efficiency": 1,                       // 1/2/3 级能效，家电线须 1 级才有国补
  "certModel": "Legion Y9000P IAX10",    // 能效网备案型号
  "energyModel": "Legion Y9000P IAX10",  // 能效网「规格型号」，通常是字母码
  "efficiencyStatus": "verified",

  "throttle": null,                      // 持续性能衰减，极少有数据，缺了只判降级

  "confidence": "medium",                // high / medium / unverified
  "dataFrom": {"spec": "...", "price": "crawl/jd-xxx", "sku": "crawl/jd-xxx"},

  "price": {
    "list": 16999,                       // 页面划线价
    "discounts": [],                     // 可叠加的券，逐条记
    "net": 16999,                        // 实付（算涨幅只能用这个）
    "subsidy": 1500,                     // 页面写的国补金额
    "final": 15499,                      // 到手价
    "basisNote": "list=划线价¥16999；discounts=无；subsidy=「国家补贴｜领后减￥1500」；final=「国补领后价」¥15499；校验：16999−1500=15499 成立。"
  },
  "priceCapturedAt": "2026-11-07T22:34:00+08:00",
  "shopType": "self",                    // self 自营 / flagship 旗舰店 / pop 三方
  "shopName": "联想京东自营旗舰店",
  "url": "https://item.jd.com/100377405028.html",
  "platformSkuId": "100377405028",       // 京东 pid，必填，否则对不上具体配置

  "cpuId": "ultra-9-290hx-plus",         // 归一化后的芯片 id，必须能在 bench.json 找到
  "dataGaps": ["batteryWh", "throttle"], // 这台还缺什么，写在这，不要留空
  "priceBlockedBy": null                 // 规格和在售 SKU 不是同一台时填原因
}
```

**字段级别（`field_matrix.json`，改之前必须理解）**：

| 级别 | 含义 | 例 |
|---|---|---|
| `R` | 必填，缺了判 UNKNOWN | 编程组的 `ramType` |
| `H` | 硬门槛，不满足判 VIOLATE | 视频组的 `vramGB` |
| `-` | 不相关 | 办公组的 `vramGB` |
| `P` | 只影响价格/国补，**不进判定** | `efficiency` |

> ⚠️ `efficiency` 是 P 级。有人把它当门槛，结果所有组被卡死成「0 台可用」。
> ⚠️ `ram.maxGB` **不影响判定**。有人按"杠杆最高"排序去补它，补了 10 台覆盖率纹丝不动。
> `judge()` 里 `ram.gb < 需求` 直接判 VIOLATE，压根不看能不能扩展。

#### 3.3.2 CPU / GPU 跑分（`bench.json`）

```jsonc
// 顶层：{meta, methods, cpus[], todo, gpus[], gpuTodo, todoNote}
// cpus[] 以芯片 id 为主键
{
  "id": "ultra-9-290hx-plus",
  "name": "Core Ultra 9 290HX Plus",
  "vendor": "Intel",
  "cores": 24,
  "bench": {"r23": {"single": 2200, "multi": 38000}},   // 首选 Cinebench R23
  "observedR23": {"variants": [...]},                    // 同一芯片在不同机身的实测分布
  "perfCurrent": 100,                                    // 归一化性能分
  "usedInMachines": ["spec-legion-y9000p-2026"],
  "source": "pchome|cpu-monkey|notebookcheck",
  "sourceNote": "同机同一次测试，单核多核未跨源拼接",
  "verified": true
}
```

**`cpuId` 归一化是硬要求**：同一颗 CPU 有 `Ryzen 7 H255` / `R7-H255` / `锐龙7 H 260` / `AMD Ryzen 7 255H` 四种写法。
新增机型后必须重跑归一化脚本，否则判定失配（历史上曾只有 5/34 种能匹配上）。

#### 3.3.3 专业（`majors.json`）—— **这一块不用你采**

已有 311 个专业 / 10 个软件栈组，结构完整。你只需要读，不要改。
`software[]` 20 条里 10 条有官方 sysreq 来源、10 条空着——**空着是有意的**，不要帮忙"补全"。

门槛只有两种状态：
- `official`：能追到软件厂商官方系统要求页，且必须挂上对应 `software` id
- `judgment`：人写的判断，`rationale` ≥ 40 字

**没有第三种。**

---

### 3.4 怎么获取数据

#### 3.4.0 路径选择（开工前第一件事）

**路径 A —— Trae 自带浏览器控制工具**

前提三件事，缺一不可：
1. 用的是**用户日常在用的浏览器**（有京东登录态），不是 Trae 内置的预览浏览器
2. 窗口**在前台**（后台标签截图必超时，这是最常见的失败原因）
3. 页面已经加载完（不是白屏骨架）

**路径 B —— 纯终端：Playwright 接管本机 Edge（推荐，最稳）**

```bash
# 1. 装 playwright（一次性）
python -m pip install playwright
python -m playwright install msedge

# 2. 完全退出 Edge（任务管理器确认没有 msedge.exe），然后：
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" \
  --remote-debugging-port=9222 \
  --user-data-dir="E:\电脑涨价分析\tools\edge-profile"

# 3. 在弹出的窗口里手动登录一次京东（扫码），之后这个 profile 长期有效
# 4. 之后所有脚本都连这个端口，复用的是同一个已登录浏览器
```

```python
# fetch_jd.py —— 单台商品页取数，人工节奏，不碰 XHR
from playwright.sync_api import sync_playwright
import json, random, time, sys

def human_pause(a=3.0, b=8.0):
    time.sleep(random.uniform(a, b))

def is_blocked(page):
    head = page.evaluate("() => (document.title + ' ' + document.body.innerText).slice(0, 800)")
    marks = ["risk_handler", "频控", "访问过于频繁", "安全验证", "验证码", "欢迎登录"]
    return any(m in head for m in marks) or "risk_handler" in page.url

def grab(pid, outdir):
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")  # ← 复用已登录会话
        ctx = browser.contexts[0]
        page = ctx.new_page()
        page.goto(f"https://item.jd.com/{pid}.html", wait_until="domcontentloaded", timeout=60000)
        human_pause(4, 7)

        if is_blocked(page):
            page.screenshot(path=f"{outdir}/BLOCKED-{pid}.png")
            print(json.dumps({"pid": pid, "status": "BLOCKED", "url": page.url}, ensure_ascii=False))
            sys.exit(2)   # ← 退出码 2 = 撞风控，不重试

        # 按元素定位，不要盲滚像素
        for _ in range(random.randint(2, 3)):
            page.mouse.wheel(0, random.randint(700, 1200))
            human_pause(1.5, 3.0)

        # 滚到「规格参数」区
        try:
            page.locator("text=规格参数").first.scroll_into_view_if_needed(timeout=8000)
            human_pause(1.5, 2.5)
        except Exception:
            pass

        page.screenshot(path=f"{outdir}/{pid}-spec.png", full_page=False)
        print(json.dumps({"pid": pid, "status": "OK", "title": page.title()}, ensure_ascii=False))
```

> **为什么强调「按元素定位」**：盲滚固定像素经常滚过头或不够，白等好几轮。
> 用 `scroll_into_view_if_needed()` 直接定位到「规格参数」标题再截图，一次就能拍全价格 + CPU + 显卡 + 分辨率 + 刷新率 + 插槽 + 净重 + 能效网型号。

#### 3.4.1 京东价格（**唯一认可的来源**）

**流程**（一台机约 40~90 秒）：

1. **搜索页先看一批**（省时间）：搜机型名 → 截图 → 能一次读到多个 SKU 的「到手价」。
2. **锁定精确 pid**：搜索结果里同一机型常有 5~7 个 SKU（不同内存/显卡/店铺），
   必须选**和库里 `spec` 配置一致**的那个，记下 `data-sku` 或 URL 里的 pid。
3. **进商品页**，滚到规格区，截一张能同时拍到「价格 + 规格参数」的图。
4. **拆价格四字段**：
   - `list` = 划线价（灰色带删除线那个）
   - `discounts[]` = 可叠加的券，逐条记（"满 3000 减 300"这种）
   - `net` = 实付（**算涨幅只能用这个**）
   - `subsidy` = 页面明写的国补金额（"国家补贴｜领后减 ¥1500"）
   - `final` = 到手价
   - `basisNote` 里写清：哪个 pid、哪家店、什么时候、页面原文怎么写的、**做一次加减校验**
5. 写 `priceCapturedAt`（ISO 带时区）、`shopType`、`shopName`、`url`、`platformSkuId`。

**价格必须对上配置。** 同一个机型名，1080p 版和 2.5K 144Hz 版是两个 SKU，能差 1200 元。
苍龙 18 Pro 的 5060 版 ¥10899、5070 版 ¥11099 起、顶配 ¥14099——写错版本等于数据作废。

#### 3.4.2 规格（品牌官网 / 京东规格参数）

优先级：
1. **京东商品页「规格参数」区**（最快，且和价格同一个页面，一次截图两件事都拿到）
2. **品牌官网产品页**（最权威，用于补京东没写的，如 TGP、色域、满血功耗）
3. **官方新闻稿 / 官方商城**

**不许用的**：ZOL 参数页、什么值得买、百度百科（可作交叉参考，不可作唯一来源）、AI 生成摘要。

**接口 `ports` 的写法**（这是 P0 缺口，最需要补）：
```
"ports": ["USB-A ×3", "USB-C ×2（1个全功能140W PD / 1个仅数据）",
          "HDMI 2.1", "miniDP 2.1", "RJ45 千兆", "3.5mm 音频", "SD 读卡器 UHS-II"]
```
要点：
- 写**代际**（HDMI 2.1 不是 HDMI）、写**数量**（×3）、写**功能差异**（全功能 vs 仅数据）
- 雷电口写「雷电 4 ×1」而不是「USB-C」
- **全缺时不要留空数组**，写 `dataGaps: ["ports"]`——留空数组会让判定认为"这机器没有任何接口"

#### 3.4.3 能效等级（`efficiency`）

**关键技巧：能效网搜的是「规格型号」，不是营销名。**

搜"天选"在能效网一条都查不到，得搜 `FA608` 这种字母码。
字母码从哪来？**京东商品页「规格参数」区最后几行就有「能效网规格型号」**，一次截图顺手拿到。

流程：
1. 从京东规格区截到 `energyModel`（字母码，如 `FA608`）
2. 去中国能效标识网（`www.energylabel.gov.cn`）搜这个码
3. 拿到 `efficiency`（1/2/3 级）+ 备案型号 `certModel`
4. 只有 `efficiency == 1` 且页面确实有补贴，`subsidyEligible` 才为 true

> `efficiency` 是 **P 级**——只影响国补能不能拿，不影响"这机器够不够用"。
> 所以它是 P2 优先级，别在它上面耗太久。

#### 3.4.4 CPU 跑分（`bench.json`）

- 首选 **Cinebench R23**（单核 + 多核），其次 R20/R15，再次 Geekbench 6
- 来源：Notebookcheck（同机实测最可靠）、CPU-Monkey、PChome 评测
- **单核和多核必须来自同一次测试、同一台机器。** 跨来源拼接会造出不存在的芯片表现
  （踩过：R7 H255 的单核抄了百科异常值、多核抄了另一家实测，拼出 2055/15475，实际 1709/15475，性能分虚高 7 分）
- 记 `sourceNote` 说明是哪台机器、哪个来源、是否同一次测试

---

### 3.5 防风控（**最高优先级，读三遍**）

#### 3.5.1 红线（违反即停）

| # | 禁止 | 为什么 |
|---|---|---|
| 1 | 用无 cookie / 未登录的浏览器访问京东 | 京东直接重定向到 `cfe.m.jd.com/privatedomain/risk_handler/`。**已实际发生过一次** |
| 2 | 用 Trae/IDE 内置的预览浏览器 | 同上：无历史、无登录、指纹异常 |
| 3 | 首次就用 headless 模式 | 无头指纹是公开的识别特征 |
| 4 | 用无痕/隐私窗口 | 每次都是"全新的陌生人" |
| 5 | 并发请求 / 多标签同时开商品页 | 人类不会同时看 8 个商品页 |
| 6 | 固定间隔（如每 2.000 秒一次） | 机器特征。间隔要随机、要抖动 |
| 7 | 直连 `item.jd.com` 的裸 HTTP / requests | 没有 Referer、没有 cookie、没有 JS 指纹 |
| 8 | 撞到验证页后重试 / 换 IP / 绕验证 | 越绕越黑，可能封掉你的登录账号 |
| 9 | 深夜批量跑（凌晨 2~6 点连续几百次） | 最典型的风控命中画像 |
| 10 | 清 cookie / 清缓存"重来一次" | 登录态没了，前面所有信任归零 |

#### 3.5.2 人的节奏（照这个来）

| 项目 | 建议值 |
|---|---|
| 单页停留 | 4~7 秒（进页面后先别动，等渲染） |
| 两次滚动之间 | 1.5~3 秒，每次滚 700~1200px（不是精确值） |
| 两台机器之间 | 30~90 秒随机 |
| 每批总量 | **不超过 25 台** |
| 每小时上限 | 25 台 |
| 单次会话 | 不超过 2 小时，然后至少停 30 分钟 |
| 遇到验证页 | **立即停手**，本批结束 |
| 一天内撞 2 次验证 | 当天收工 |

**滚动要按元素定位，不要盲滚像素。** 目标元素 `scroll_into_view_if_needed()`，比"滚 3 屏"准得多，也少一次无谓的翻页。

#### 3.5.3 撞风控的信号（看到任一条就停）

- URL 里出现 `risk_handler` / `safe.jd.com` / `verify`
- 页面标题含 `验证` / `安全` / `欢迎登录` / `京东-请登录`
- 页面正文含 `访问过于频繁` / `请完成安全验证` / `拖动滑块` / `环境异常`
- 原本有的价格区变成空白 / 变成 `--`
- 请求返回 302 跳到登录页

**撞了怎么办**：
1. 立刻截图存 `crawl/<批次>/BLOCKED-<pid>.png`
2. 记下当前 URL、时间、是第几台
3. **停止本批全部动作**，不要重试
4. 向用户汇报：撞在第几台、什么信号、是否需要人工过一次验证

#### 3.5.4 低风险路径总结

```
✅ 用户日常在用的 Edge（有登录态、有历史、有书签）
✅ 窗口在前台、有真实尺寸、用户能看见
✅ 一台一台来，节奏有抖动
✅ 走真实 DNS + 真实 UA + 真实 JS 环境
✅ 撞验证就停，等人处理

❌ 任何"干净的"新环境
❌ 任何"快一点"的并发/固定间隔
❌ 任何"绕过验证"的尝试
```

---

### 3.6 数据纪律（八条，写进 DNA）

1. **每个数字都要能追到底，追不到就说追不到。** 只有两种状态：`official` 有来源 / `judgment` 写明 ≥40 字理由。
2. **未取证不许填数字。** 空着就空着，不要"估一个合理值"。
3. **不适用 ≠ 缺失。** 核显 `vramGB = 0`（不是 null）；内存条没有 CPU 是 null（不是 0 分）。
4. **价格只认京东商品页。** 三方报价偏差方向相反且可达数千元（实测：学生机清单低 3366，ZOL 高 1650）。
5. **单核多核必须同一次测试。** 跨源拼接是造假，不是补全。
6. **数据放 E 盘，改前先备份。** C 盘工作区丢过一次，回收站里都没有。
7. **不要为了数字好看放宽门槛。** 50.3% 低是市场现实，不是脚本不行。
8. **改 `lib_judge.py` 前先跑 `coverage.py` 记基线**，改完对比。数字异常先查别处。

---

### 3.7 作业单（按优先级，别跳）

#### P0 · 缺 `ports` 接口清单 · 12 台 ⭐ 最值钱

卡住了 7 个专业的判定（音乐音频组 `ports` 是 R 级）。

| # | id | 机型 | 品牌 |
|---|---|---|---|
| 1 | `add-xingbookplus14` | 星Book Plus 14 | 惠普 |
| 2 | `add-honorx14-amd` | 荣耀 X14 锐龙版 | 荣耀 |
| 3 | `add-matebook14-2026` | MateBook 14 2026 | 华为 |
| 4 | `add-laikuair16` | 联想来酷 Air16 2026 | 联想来酷 |
| 5 | `add-xingbookproair14` | 星Book Pro Air 14 | 惠普 |
| 6 | `add-adou14air` | a豆 14 Air 2026 | 华硕 |
| 7 | `add-laiku-zhan7000` | 联想来酷 斗战者 战7000 | 联想来酷 |
| 8 | `add-acer-predator-helios9` | 宏碁 掠夺者 战斧9 Neo S | 宏碁 |
| 9 | `add-acer-predator-helios10` | 宏碁 掠夺者 战斧10 Neo S | 宏碁 |
| 10 | `add-thunde-renjian-s` | 雷神 猎刃S | 雷神 |
| 11 | `add-canglong18pro-jd` | 苍龙 18 Pro | 机械革命 |
| 12 | `add-omen-pro15-jd` | 暗影精灵 Pro 15 | 惠普 |

顺带可一并补的：这 12 台里有 10 台同时缺 `efficiency`、`batteryWh` 或 `weightKg`。

#### P1 · 缺 `cpuId` · 7 台

影响跑分匹配，进而影响性能分。

| # | id | 机型 | 库里 CPU 写法 |
|---|---|---|---|
| 1 | `spec-parsed-jd-lenovo-thinkpade16-u5-32g-1t-authoriz` | ThinkPad E16 Gen 2（Ultra5 版） | Ultra5（第1代）← 要确认是 125H 还是 225H |
| 2 | `spec-parsed-jd-dell-inspiron14plus-7440-core5-210h-1` | 灵越 14 Plus 7440 | Core5-210H |
| 3 | `jd-mechrevo-jiaolong15k-r57640h-3050-16g-512g-20261109` | 机械革命 蛟龙 15K | R5-7640H |
| 4 | `jd-msi-shenying16-r8840hx-16g-512g-5060-20261110` | 微星 神影16 锐龙版 | R7-8840HX |
| 5 | `add-adou14air` | a豆 14 Air 2026 | R7-8840H ← **存疑，应为锐龙 AI 9 H 465** |
| 6 | `add-tianxuan-air-2026` | 天选 Air 2026 | 锐龙AI 7 H 350 |
| 7 | `add-thunde-renjian-s` | 雷神 猎刃S | R7-7745HX |

**要做的**：① 核实真实 CPU 型号（京东规格区 / 官网）；② 映射到 `bench.json` 里已有的芯片 id；
③ 若 `bench.json` 里没有，补一条（含 R23 单核多核，同一次测试）；④ 重跑归一化。

#### P2 · 缺 `efficiency` · 35 台

只影响国补，不影响推荐结论。**P0/P1 没做完不要碰这块。**

做法：京东规格区拿「能效网规格型号」字母码 → 能效网查等级。
35 台 id 清单见 `python -c "import json;d=json.load(open('data/machine_pool.json',encoding='utf-8'));print([m['id'] for m in d['machines'] if m.get('efficiency') is None])"`

#### P3 · 待核项 · 4 台

| 机型 | 问题 | 怎么核 |
|---|---|---|
| 星Book Plus 14 | 价格待核 | 库里 SKU 与京东在售配置可能不是同一台 |
| MateBook 14 2026 | 价格 + CPU 配置待核 | 同上，且是鸿蒙系统版，要标注 |
| a豆 14 Air 2026 | 价格待核 + CPU 型号错 | CPU 应为锐龙 AI 9 H 465，改后再核价 |
| 苍龙 16 Pro | 电池 80Wh 存疑（9955HX 版有报道为 99Wh）+ 色域存疑（100% sRGB vs 100% DCI-P3） | 查官网规格页原文 |

#### P4 · 扩池（做完上面再说）

缺口：**32G 内存 + 8G 显存 + 1T 存储** 的机型只有 4~5 台，
而视频影视/三维建筑/游戏数媒/测绘四组共 72 个专业都卡在这个门槛上。
补字段救不了，只能补机型。方向：游戏本里 32G 版本、移动工作站、创作者本。

---

### 3.8 验收标准（怎么算这批干完了）

Trae 交回来时，按这 7 条逐项打勾，缺一条打回：

- [ ] **1. 覆盖率不掉**：`python "E:\电脑涨价分析\tools\coverage.py"` 的 `recommendable` 数字 ≥ 改动前（当前 357/710 = 50.3%）
- [ ] **2. 每条有来源**：改动的每个字段，都能指向一张截图或一个 URL。指着说"我查到的"不算
- [ ] **3. 价格有 basisNote**：含 pid、店铺、时间、页面原文、加减校验
- [ ] **4. 存疑有标注**：不确定的写进 `dataGaps`，不许含混过去
- [ ] **5. 没有 null/0 混淆**：核显 `vramGB` 是 0，查不到的是 null
- [ ] **6. 文件没坏**：`python -c "import json;json.load(open('data/machine_pool.json',encoding='utf-8'))"` 通过，`machines` 数组长度只增不减
- [ ] **7. 备份在**：改动前的 `.bak-YYYYMMDD` 文件存在

**抽验方法**（用户做）：随机挑 2 台，让它把截图和 JSON 条目一起给你，自己看截图里的数字对不对得上。

---

### 3.9 回报格式

每批结束，Trae 必须给出这个格式：

```
━━ 批次回报 ━━
批次：P0-ports-01
处理：12 台目标中的 6 台
成功 5 / 存疑 1 / 失败 0 / 撞风控 0

逐条：
1. 星Book Plus 14 (add-xingbookplus14)
   改：ports, batteryWh, weightKg
   源：京东 item.jd.com/100xxxxxxxx.html（自营，2026-11-11 15:20）
       + 惠普官网规格页 hp.com/cn/...
   图：crawl/P0-ports-01/100xxxxxxxx-spec.png
   注：USB-C 是否支持 PD 充电，京东页未写，官网写 65W PD

2. ...

存疑清单：
- 苍龙 18 Pro 电池：京东页写 99Wh，官网写 80Wh，两处矛盾。
  下一步：查能效网备案型号看电池容量，或找第三方拆机。

覆盖变化：recommendable 357 → 361（+4）

更新了：data/machine_pool.json、AGENTS.md 第 6 节、data/crawl/P0-ports-01/
```

---

## 附：给用户的三条提醒

1. **Trae 第一轮大概率会想"帮你把数据补全"**——它会用常识去猜 TGP、猜色域。第一次汇报就盯着这点，看到没来源的数字立刻打回。
2. **它可能想用 `requests` 直接抓京东。** 明确禁止。京东商品页的价格是 JS 异步渲染的，裸请求拿到的 HTML 里没有价格，硬抓只会拿到一堆 `--`。
3. **撞风控不是 Trae 的错，是节奏的问题。** 让它停下来汇报，别让它"再试一次"。一次风控处理不当，可能连你日常用的账号一起黑掉。
