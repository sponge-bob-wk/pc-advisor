# 数据采集手册 —— 按数据类型去哪采、怎么写回

> 给负责采集数据的 Agent。本文回答三个问题：**采什么、去哪采、采回来怎么写**。
> 目标是**字段对齐**：不管谁采，写进 `data/machine_pool.json` 和 `data/bench.json` 的格式都一样。
> 项目根目录：`E:\电脑涨价分析\`（一切产出只落 E 盘，绝不写 C 盘）。
> 字段的完整 schema 和级别矩阵见 `交付Trae-数据采集任务书.md` §3.3；项目全貌见 `AGENTS.md`。

---

## 总索引：八类数据 → 八个采集通道

| # | 数据类型 | 字段（machine_pool.json） | 去哪采 | 优先级 |
|---|---|---|---|---|
| 1 | **屏幕数据** | `screen.*` | 京东商品页「规格参数」区 → 品牌官网规格页补漏 | 高 |
| 2 | **接口数据** | `ports[]` | 京东「规格参数」区 → 官网（京东常省略代际） | 高 |
| 3 | **CPU/GPU 跑分** | `bench.json` 的 `bench.r23` | Notebookcheck > CPU-Monkey > PChome 评测 | 高 |
| 4 | **价格数据** | `price.*` + shop/url/pid | **只认京东商品页**，一个字都不用第三方 | 最高 |
| 5 | **能效数据** | `efficiency/certModel/energyModel` | 中国能效标识网（搜字母码，不搜营销名） | 中 |
| 6 | **电池/重量** | `batteryWh/weightKg` | 京东规格区 → 官网 | 中 |
| 7 | **CPU/内存/存储规格** | `cpu.* / ram.* / storageGB` | 京东规格区 → 官网 → Intel ARK / AMD 产品页 | 高 |
| 8 | **专业合适度** | `majors.json` 的 `req/hardGates` | **软件厂商官方 sysreq 页**（不是采集网站） | 特殊，见 §八 |

---

## 一、屏幕数据（`screen.*`）

**去哪采（按顺序）**：
1. **京东商品页「规格参数」区**——分辨率、刷新率、面板类型通常都在；滚到那一屏截一张图
2. **品牌官网产品页规格表**——色域（DCI-P3 / sRGB 百分比）和峰值亮度（nit）京东经常不写，官网有
3. 官方新闻稿（首发评测通稿）——最后手段

**写回格式**：
```json
"screen": {
  "sizeInch": 16,
  "widthPx": 2560, "heightPx": 1600,
  "refreshHz": 240,
  "panel": "OLED",                     // 只能是 IPS / OLED / MiniLED / VA
  "colorGamut": {"dciP3": 100},        // 三选一：dciP3 / srgb / ntsc，值是百分比数字
  "brightnessNit": 500
}
```
**对齐要点**：
- 色域只存一种口径，官网同时写 sRGB 100% 和 DCI-P3 95% 时，**存 DCI-P3**（判定和展示都以它为准）
- 刷新率取官方标称值（240Hz），不取"自适应最高"之类的营销数
- 官网和京东矛盾时，**以官网为准**，把矛盾写进 `dataGaps` 备注

## 二、接口数据（`ports[]`）

**去哪采**：
1. **品牌官网规格页**——唯一写全「代际 + 数量 + 功能」的地方，是主来源
2. 京东规格区——通常只有"USB-A/USB-C/HDMI"这种不写代际的粗粒度，只能当佐证

**写回格式**（数组，每项一个接口）：
```json
"ports": ["USB-A ×3", "USB-C ×2（1个全功能140W PD / 1个仅数据）",
          "雷电 4 ×1", "HDMI 2.1", "RJ45 千兆", "3.5mm 音频", "SD 读卡器 UHS-II"]
```
**对齐要点**：
- 必须**写代际**（HDMI 2.1 不是 HDMI）、**写数量**（×3）、**写功能差异**（全功能 vs 仅数据）
- 雷电口写「雷电 4 ×1」，不写「USB-C」
- 🔴 **全缺时不要留空数组**——写 `dataGaps: ["ports"]`。空数组会被判定引擎当成"这台机器没有任何接口"
- 采不到代际的接口，写成 `"USB-C（代际待核）"` 并挂 `dataGaps`，**不要猜**

## 三、CPU/GPU 跑分（`bench.json`）

**去哪采（可靠性从高到低）**：
1. **Notebookcheck**——同机实测，最可靠，首选
2. **CPU-Monkey**——汇总站，次选
3. **PChome 评测**——国行机型常有首测

**采什么**：首选 **Cinebench R23 单核 + 多核**；没有就 R20/R15，再次 Geekbench 6（记进 `methods` 说明口径变了）。

**写回格式**（bench.json 以芯片 id 为主键）：
```json
{
  "id": "ultra-9-290hx-plus",
  "name": "Core Ultra 9 290HX Plus", "vendor": "Intel", "cores": 24,
  "bench": {"r23": {"single": 2200, "multi": 38000}},
  "source": "notebookcheck",
  "sourceNote": "X 机型实测，同一次测试，单核多核未跨源拼接",
  "verified": true
}
```
**对齐要点**：
- 🔴 **单核和多核必须来自同一次测试、同一台机器**。跨来源拼接是造假（踩过：R7 H255 拼出 2055/15475，实际 1709/15475）
- `cpuId` 归一化：同一颗 CPU 有四种写法（`Ryzen 7 H255` / `R7-H255` / `锐龙7 H 260` / `AMD Ryzen 7 255H`），机型里的 `cpuId` 必须能对应上 bench.json 的芯片 id。新增机型后重跑归一化脚本
- GPU 跑分同库的 `gpus[]`，按显存/TGP 分组比，不与 CPU 混在同一 key

## 四、价格数据（`price.*`）—— 红线中的红线

**去哪采**：**只认京东商品页**。
实测过三方偏差方向相反且巨大：学生机清单/微信文章普遍低 300~3400 元、ZOL 普遍高 1600 元、IT之家偏低。
→ ZOL / 什么值得买 / IT之家 / 学生机清单 / 百科 / AI 摘要的报价**一律不用**。

**怎么采**：
1. 搜机型名 → **搜索页截图**（一次能读多个 SKU 的到手价）
2. 同一机型常有 5~7 个 SKU（不同内存/显卡/店铺），**选和库里配置一致的那个**，记 pid
3. 进商品页滚到价格区 + 规格区，截一张图

**写回格式**：
```json
"price": {
  "list": 16999,          // 划线价（灰色删除线）
  "discounts": ["满3000减300"],   // 可叠加的券，逐条记，中间券最容易漏
  "net": 16999,           // 实付。算涨幅只能用这个
  "subsidy": 1500,        // 页面明写的国补金额
  "final": 15499,         // 到手价
  "basisNote": "list=划线价¥16999；subsidy=「国家补贴｜领后减￥1500」；final=15499；校验：16999−1500=15499 成立。pid=100xxxxx，联想京东自营旗舰店，2026-11-11 15:20 抓取"
},
"priceCapturedAt": "2026-11-11T15:20:00+08:00",
"shopType": "self",        // self 自营 / flagship 旗舰店 / pop 三方
"shopName": "联想京东自营旗舰店",
"url": "https://item.jd.com/100xxx.html",
"platformSkuId": "100xxx"  // 京东 pid，必填
```
**对齐要点**：
- 🔴 **价格必须对上配置**：1080p 版和 2.5K 144Hz 版是两个 SKU，能差 1200 元。basisNote 里写明是哪个配置
- **必做加减校验**：`list − discounts − subsidy = final`，对不上就说明漏了券
- 怀疑漏券的信号：`list == net` 且 `final = list − subsidy`

## 五、能效数据（`efficiency`）

**去哪采**：**中国能效标识网 `www.energylabel.gov.cn`**，且**搜「规格型号」字母码，不搜营销名**（搜"天选"一条都没有，搜 `FA608` 才有）。

**怎么采**：
1. 字母码从**京东商品页「规格参数」区最后几行**的「能效网规格型号」拿——和价格同屏，顺手截到
2. 拿字母码去能效网搜 → 得到能效等级（1/2/3 级）+ 备案型号

**写回格式**：
```json
"efficiency": 1,
"certModel": "Legion Y9000P IAX10",
"energyModel": "FA608",           // 能效网规格型号（字母码）
"efficiencyStatus": "verified"
```
**对齐要点**：能效是 **P 级**字段——只影响国补金额，**不进判定引擎**。查不到就空着，不要用"大概率是 1 级"去猜。

## 六、电池 / 重量（`batteryWh` / `weightKg`）

**去哪采**：京东规格区（"电池容量 Wh""净重 kg"两行）→ 官网规格表（更全，游戏本官网常标"电池 80Wh"）。
**对齐要点**：Wh 必须是瓦时数不是 mAh；官网和京东矛盾时（发生过：苍龙18 Pro 一边 80Wh 一边 99Wh）以官网为准 + 挂 `dataGaps` 备注。缺了是降级项不是硬门槛。

## 七、CPU / 内存 / 存储规格（`cpu.*` / `ram.*` / `storageGB`）

**去哪采**：京东规格区（核心/线程/内存容量频率/硬盘容量）→ **Intel ARK（ark.intel.com）/ AMD 官网产品页**（基础功耗 baseW、最大功耗 maxW、核数线程的权威来源）→ 品牌官网。

**写回格式**：
```json
"cpu":   {"model": "Core Ultra 9 290HX Plus", "vendor": "Intel",
          "cores": 24, "threads": 24, "baseW": 55, "maxW": 160,
          "coreSrc": "同代 275HX 官方 24核；290HX Plus 待核"},
"ram":   {"gb": 16, "type": "DDR5", "config": "16GB×1",
          "form": "sodimm", "slots": 2, "maxGB": 64},
"storageGB": 1024
```
**对齐要点**：
- `ram.gb` 是**出厂容量**。判定只认它，`maxGB` 再大也救不了 16G 机型
- `form` 枚举：`soldered` 板载 / `sodimm` 可换 / `lpcamm2`；苹果写 `soldered`
- `cpu.coreSrc`：核心数是推断的必填此字段写明依据
- 枚举别写错：`vendor` ∈ Intel/AMD/Apple；`cat` ∈ th/gaming/desktop
- 🔴 **核显 `vramGB` 填 `0` 不是 `null`**（不适用≠缺失）；查不到的才填 `null`

## 八、专业合适度（`majors.json`）—— 这不是"采"，是"取证"

**⚠️ `majors.json` 已有 311 专业 / 10 组 / 20 条软件要求，只读不改**。只有当某个软件要求（`software[]`）还是空的、用户明确让你取证时，才做这一步。

**去哪采**：**软件厂商官方系统要求页（sysreq）**，一条软件一条来源：

| 软件 | 官方来源 |
|---|---|
| Adobe 全家桶 | helpx.adobe.com 的系统要求页 |
| Autodesk（AutoCAD/Revit/3ds Max） | autodesk.com 官方"系统要求"页 |
| SolidWorks | solidworks.com 的 SW Hardware 页 |
| MATLAB | mathworks.com 官方要求 |
| Unity / Unreal | 官网 sysreq |
| Office | microsoft.com 官方页 |

**写回格式**（majors.json 的 groups[].req）：
```json
"req": {"ramMin": 32, "vramMin": 8, "storageMinGB": 1024,
        "basis": "official",            // 或 "judgment"，没有第三种
        "rationale": "……"}
"hardGates": [{"dim": "ram", "min": 32}]
```
**对齐要点**：
- `basis` 只有两种：`official`（能追到官方 sysreq 页，且在 `software[]` 挂上对应软件 id）/ `judgment`（人写判断，`rationale` ≥ 40 字）
- 🔴 **未取证不许填数字**。10 条软件要求空着就让它空着，不要"估一个合理值"
- 每个字段在每组是 R/H/-/P 四级（`field_matrix.json`）：R=缺了判未知、H=硬门槛、-=不相关、P=只影响价格。`efficiency` 是 P 级，**绝不能当门槛**
- 同专业不同学校门槛不同（长沙理工/海南大学/浙工大的计算机分别是"32G 核显够/32G+8G独显/32G GPU 可选"），不能按学科大类一刀切

---

## 九、所有通道通用的规矩

**采集方式**（涉及京东页面时）：
- 只用**用户已登录的 Edge**（窗口在前台、页面加载完），或 Playwright 接管 9222 调试端口。命令见 `交付Trae-数据采集任务书.md` §3.4.0
- 禁止：无 cookie 直连、内置预览浏览器、无痕窗口、headless、并发多标签、固定间隔、裸 HTTP。撞验证页立即停手截图汇报，不重试不绕过
- 滚动用元素定位：`page.locator("text=规格参数").first.scroll_into_view_if_needed()`，别盲滚像素

**落盘纪律**：
1. 改前备份：`cp data/machine_pool.json data/machine_pool.json.bak-YYYYMMDD`
2. 用 python 脚本读写 JSON，不手工编辑
3. 证据归档：每台机器的截图存 `data/crawl/<批次名>/`，每张截图对应一个字段来源
4. 改完跑 `python tools/coverage.py` 对比基线，数字掉了就回滚

**四条对齐铁律**：
1. **每个数字都能追到底**：要么有来源（官方页/截图），要么写进 `dataGaps`——没有第三种状态
2. **不适用 ≠ 缺失**：核显 vramGB=0；查不到=null
3. **不猜**：查不到代际/数量/等级就挂 dataGaps，不按常识补
4. **主来源分级**：规格类主来源必须是京东商品页或品牌官网；第三方报道只能交叉验证
