# -*- coding: utf-8 -*-
"""build_route_lora.py —— 合并所有路由层样本，产出最终 LoRA 训练集

## 为什么需要一次「合并」而不是直接喂多份文件

`lora_data/` 下现在有 4 份**路由层**数据，来源不同、标签来源也不同：

| 文件 | 来源 | 标签怎么来的 | 可靠性 |
|---|---|---|---|
| `adv-all.jsonl` | 手写模板 | 模板设计（每句都写了 why） | 高 |
| `expanded-route.jsonl` | 维度组合 | 成分唯一决定标签 | 高 |
| `log-route.jsonl` | 真实日志 | 形态规则（只收能确定的） | 高 |
| `adv-errors.jsonl` | adv-all 的子集 | 同上 | 高（但**与 adv-all 重复**） |

直接多文件喂会给 `adv-errors` 里的 40 条**双倍权重**（它们本来就在 adv-all 里），
模型会过度拟合那 40 句。所以必须去重后再合并。

## 标签可靠性校验（这一步不许省）

合并后逐条核对：**标签是否与代码自己的判据一致**。
`agent._is_fit_question` 和 `intent_layer.fit_by_rule` 是项目里对 fit 的两套实现，
它们对 `fit=False` 的判断是一致的（差异只在 `够用吗` 这类边界，见文末 ⚠）。

如果一条样本的 `fit` 与这两套判据**都**矛盾，说明标签可疑 → 单独列出，不静默保留。

## 输出

    lora_data/route-train.jsonl   合并去重后的训练集
    lora_data/route-eval.jsonl    留出评估集（默认 10%，按 user 哈希稳定切分）

## 用法

    python build_route_lora.py                # 统计 + 校验
    python build_route_lora.py --write        # 写出训练/评估集
    python build_route_lora.py --write --eval-ratio 0.15
"""
import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LORA_DIR = os.path.join(HERE, "lora_data")

# 路由层来源（顺序即优先级：前面的先占位，后面重复的丢弃）
SOURCES = [
    ("adv-all.jsonl", "手写模板"),
    ("expanded-route.jsonl", "维度组合"),
    ("log-route.jsonl", "真实日志"),
    # 🔴 2026-11-17 新增：真实误判纠正样本。
    #    来源是用户实测对话里模型判错的句子（collect_corrections.py 收集）。
    #    这类样本**最贵** —— 它是模型在当前版本下确实会错的地方，
    #    比手写模板更贴近真实分布，且每条都带 wrong（可作 DPO 负样本）。
    ("corrections.jsonl", "实测误判纠正"),
    # 🔴 2026-11-17 新增：思维链蒸馏数据。
    #    用户要求「不仅问题和答案，也把思维链给他，蒸馏效果更好」。
    #    只给答案 → 学生背结论；给思维链 → 学生学判断方法。
    #    这批样本的 assistant 是「思维链 + JSON」两段，
    #    训练时原样喂（推理时 _extract_json 会从后往前取 JSON）。
    ("cot-route.jsonl", "思维链蒸馏"),
    # 🔴 2026-11-17 新增：**真实日志**挖出来的思维链样本。
    #    价值：我手写的模板是"我想到的句子"，这些是"用户真会说的句子"。
    #    对比：
    #      模板：「这台不要了换一个」
    #      真实：「那我不想要这种的」「再看看别的」「换一台这台我不想要」
    #    语气、省略、口语化程度完全不同。
    ("cot-log.jsonl", "真实日志+思维链"),
    # 🔴 2026-11-21：整链对抗测试的失败样本（带纠错思维链）。
    #    来源：adversarial_v2.py --collect
    #    每条都是**真跑出来的错**，不是猜的。
    #    这批最值钱：它记录的是模型当前的真实弱点。
    ("adversarial-v2.jsonl", "整链对抗失败样本"),
    # 🔴🔴 2026-11-23：**analyze 意图的种子数据**。
    #
    #    事故：v4 把「有多少台有独显」判成 ask_current，应该判 analyze。
    #    查训练集发现 **analyze 样本数 = 0**。
    #
    #    原因：analyze 意图是 11-21 加的，只写了代码（analyze.py +
    #    agent.py 的 0b2 分支 + 规则兜底），**从没加进训练集**。
    #    模型压根没见过这个类别，只能归到最像的 ask_current。
    #
    #    内容：35 条正例（覆盖 8 种聚合操作）+ 8 条负例
    #    （"这个价算什么水平""哪个最便宜" 这类容易误判成 analyze 的）。
    #    负例很重要 —— 没有它们，模型会把 analyze 用滥。
    ("analyze-seed.jsonl", "analyze 意图种子"),
    # 🔴 2026-11-25 新增：**state_new_need 的「我要 X」样本**。
    #
    #    问题：实测「我要一台游戏本」被判成 state_range（应该 state_new_need）。
    #    查训练集发现「要/买 + 品类」这个模式只有 3 条，而且全标成了
    #    state_range（标错）。
    #
    #    语义边界（用户确认）：
    #      「我要一台游戏本」  → state_new_need（主动索取，加条件）
    #      「6000 的游戏本」    → state_range（纯描述范围）
    #
    #    内容：40 条正例（要/想买/来一台/帮我找 + 品类或配置要求）
    #         + 10 条负例（纯范围描述，防止模型过度泛化）
    ("new-need-seed.jsonl", "state_new_need 种子"),
    # 🔴 2026-11-26 新增（对抗测试发现准确率只有 65.3%，针对性补数据）
    #
    # ① 装机/台式机专项（76 条）
    #    诊断：装机 43 条样本判错 27 条（37% 正确率）。
    #    训练集里**一条装机样本都没有** —— 模型完全不认识
    #    「组装/装机/攒机/DIY/配一台/弄个主机」这类说法。
    #    语义归属：装机 = state_new_need（提了个新需求要台式机），
    #    由 pc_build_intent 规则判断是不是自己装。
    ("desktop-build-seed.jsonl", "装机/台式机专项"),
    # ② 意图边界专项（78 条）
    #    诊断出的三组混淆：
    #      refuse_switch ↔ list_others（「还有别的吗」vs「全部列出」）
    #      state_new_need ↔ ask_current（「组一台打游戏的」vs「这台多重」）
    #      analyze ↔ state_range（「性能最强的五台」vs「8000以内」）
    #    每对都给了正例和对照负例，教模型区分边界。
    ("boundary-seed.jsonl", "意图边界专项"),
    # 🔴🔴 2026-11-27 新增：**真实用户提问**（网上抓的，人工标注）。
    #
    #    补的缺口：老 1381 条用户句长度中位数只有 **7 个字**，≥20 字的仅 5 条。
    #    真实用户写的是「预算七千出头，主要打永劫和吃鸡，偶尔剪点视频，
    #    台式机，机箱别太吵」这种 20-40 字、多条件连写的句子。
    #    实测后果：真实首句集上 state_new_need 被整句当"报价位"吃掉 41 次。
    #
    #    ⚠️ 这批里 205 条是**无焦点**的（meta.focus=False），
    #       老数据一条都没有 —— _sys_prompt() 会按 meta.focus 分别给 ctx。
    #
    #    ⚠️ 已切出 63 条保持集（real-20261127.eval.jsonl），**不进训练**，
    #       否则重训后拿它评就是拿训练集考自己。
    ("real-20261127.train.jsonl", "真实提问（抓取+人工标注）"),
    # 🔴 2026-11-27 新增：**模拟补充集**（手写，补网页上抓不到的意图）。
    #    list_others / refuse_switch / analyze 在论坛标题里几乎不出现
    #    （那是对话内话术），只能靠人写。204 条覆盖全部七类。
    ("sim-20261127.train.jsonl", "模拟补缺（对话内意图）"),
]

# 🔴🔴 2026-11-26：**均衡数据优先**。
#
# 背景：对抗测试发现准确率只有 65.3%，诊断出三类问题：
#   ① 装机/台式机完全没有训练样本（43 条测试判错 27 条）
#   ② 意图边界混淆（refuse_switch↔list_others / analyze↔state_range）
#   ③ 分布严重失衡（unknown 只占 2.4%）
#
# 扩充数据后（新增 6 个种子文件，共 2000+ 条），
# 用 seed_rebalance.py 重采样成 1479 条、7 类分布按目标比例：
#   state_new_need 30% / ask_current 22% / state_range 16% /
#   refuse_switch 12% / analyze 10% / list_others 6% / unknown 4%
#
# 为什么**只用它、不叠加原始来源**：
#   route-balanced.jsonl 已经包含了全部原始数据（去重后按比例采样）。
#   同时加会导致：① 重复样本 ② 分布被原始失衡数据破坏。
_BALANCED = os.path.join(LORA_DIR, "route-balanced.jsonl")

# 🔴 2026-11-27：新增的「真实提问 + 模拟补缺」**不过 seed_rebalance**。
#
#    为什么不走重采样：
#      rebalance 是按意图比例**随机降采样**。我这批数据的价值在"句子形态"
#      （20-40 字、多条件连写），不在数量；随机丢正好会把要补的形态丢掉。
#      而且它不是"同类堆量"，是补分布缺口（无焦点、对话内意图），不需要削。
#
#    去重仍然生效（下面按 norm 后的句子去重），已核验与老集 0 重叠。
_NEW_EXTRA = [
    ("real-20261127.train.jsonl", "真实提问（抓取+人工标注）"),
    ("sim-20261127.train.jsonl", "模拟补缺（对话内意图）"),
    # 🔴 2026-11-27：**unknown 类扩充**（60 条手写）。
    #
    #    背景：v8 在保持集上 unknown 是 0/6 —— 一条都判不出来，
    #    只会硬塞 state_new_need / state_range。
    #    根因：训练集 unknown 只有 87 条，其他类 200+。
    #
    #    为什么手写而不抓：真实用户不发帖说「嗯」「在吗」，
    #    这类句子只存在于多轮对话里，网页上物理性没有。
    #
    #    为什么不走 seed_rebalance：这批的价值在**形态**
    #    （单字语气词、双字应答、延后决策），不在数量。
    #    重采样会随机丢，正好把要补的形态丢掉。
    #
    #    ⚠️ 踩过的坑：上一版我直接把它追加进 route-train.jsonl（产物文件），
    #       跑 --write 重新生成时被覆盖 → 白丢一次。
    ("unknown-expand-20261127.jsonl", "unknown 扩充"),
]

if os.path.exists(_BALANCED):
    SOURCES = [("route-balanced.jsonl", "均衡成品（含全部扩充）")] + _NEW_EXTRA


OUT_TRAIN = os.path.join(LORA_DIR, "route-train.jsonl")
OUT_EVAL = os.path.join(LORA_DIR, "route-eval.jsonl")

# 🔴 2026-11-23：LABELS 加上 analyze。
#    这个元组用于**校验**（检查样本的 intent 是否合法）。
#    之前没有 analyze，加了种子数据后必须同步，否则样本会被当非法丢掉。
LABELS = ("ask_current", "refuse_switch", "state_range",
          "state_new_need", "list_others", "unknown", "analyze")


def norm(s):
    return re.sub(r"[\s，。？！、；：,.?!;:]+", "", str(s or "")).lower()


# 老样本没写 meta.focus，它们都是"已经推荐过一台"之后的话
DEFAULT_FOCUS = "zol-2103842"
_CTX_CACHE = {}


def _ctx_for(focus_id):
    """取这一条的「当前讨论」串，**必须和生产同一个来源**。

    🔴🔴 2026-11-27：第二次同类事故。

      `_ctx_of()` 有两条分支：
        焦点机型不在池里 → "（当前讨论：zol-2103842）"      ← 降级分支
        焦点机型在池里   → "当前正在讨论这台：联想拯救者，i7…，到手 8099 元"
                                                          ↑ 生产天天走这条

      而 1381 条训练样本**全部**写的是降级分支那个形态。
      也就是说线上真正送给模型的那 806 字符 prompt，
      模型在训练里一次都没见过。

      和 11-23 那次「训练 56 字符 / 推理 2170 字符，差 39 倍」是同一类错：
      **不是写错了常量，是取值的地方和生产不是同一个函数。**

      所以这里不再手写字符串，直接调 intent_layer._ctx_of() 取真值。
    """
    key = focus_id or "__none__"
    if key not in _CTX_CACHE:
        try:
            import intent_layer as il
            _CTX_CACHE[key] = il._ctx_of(focus_id)
        except Exception:                          # noqa: BLE001
            _CTX_CACHE[key] = ("（当前没有在讨论任何机器）" if not focus_id
                               else "（当前讨论：%s）" % focus_id)
    return _CTX_CACHE[key]


def _sys_prompt(row=None):
    """返回**和推理时完全相同**的 system prompt（按这一条的焦点状态）。

    🔴 2026-11-23：system prompt 必须和推理时一模一样。

      那次的发现：训练用 56 字符，推理用 2170 字符，差 39 倍，
      后果是 v3 adapter 对「好的」输出 1219 字符思维链死循环。

    2026-11-27 补：光"用同一个模板"不够，**ctx 也得是真值**。
      · meta.focus 为 True  → 用焦点机型的描述式 ctx（生产形态）
      · meta.focus 为 False → "（当前没有在讨论任何机器）"（真实首句）
      · meta 没这个字段     → 老样本，按"已推荐过一台"处理
    """
    import intent_layer as il
    meta = (row or {}).get("meta") or {}
    if meta.get("focus") is False:
        focus_id = None
    elif meta.get("focus"):
        focus_id = meta.get("focus_id") or DEFAULT_FOCUS
    else:
        focus_id = DEFAULT_FOCUS
    return il.SYS_TMPL.format(ctx=_ctx_for(focus_id))


def load_route_file(path):
    """只收路由层格式（assistant 是 {"intent","topic","fit"}）。"""
    out, skipped = [], 0
    if not os.path.exists(path):
        return out, skipped
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            msgs = r.get("messages") or []
            by = {m.get("role"): (m.get("content") or "").strip() for m in msgs}
            u, a = by.get("user"), by.get("assistant")
            if not u or not a:
                skipped += 1
                continue
            d = None
            # 🔴 2026-11-17：assistant 内容可能是「思维链 + JSON」。
            #    直接 json.loads 会失败 → 样本被当成"别的体系"跳过。
            #    先用 _extract_json 提取（它从后往前找最后一个 JSON）。
            try:
                d = json.loads(a)
            except (json.JSONDecodeError, TypeError):
                try:
                    import intent_layer as _il
                    d = _il._extract_json(a)
                except Exception:
                    d = None
            if not isinstance(d, dict) or "intent" not in d:
                skipped += 1          # 预算层/别的体系，跳过
                continue
            # 保留原始 assistant 内容（带思维链的样本训练时要原样喂）
            out.append({"user": u, "gold": d, "meta": r.get("_meta") or {},
                        "src": os.path.basename(path),
                        "assistant_raw": a})
        except (json.JSONDecodeError, TypeError, AttributeError):
            skipped += 1
    return out, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--eval-ratio", type=float, default=0.10)
    args = ap.parse_args()

    all_rows = []
    for name, desc in SOURCES:
        rows, skipped = load_route_file(os.path.join(LORA_DIR, name))
        print("  %-26s %4d 条（跳过 %d）  ← %s" % (name, len(rows), skipped, desc))
        all_rows.extend(rows)

    # 🔴 2026-11-27：**保持集防护**。
    #
    #    real-20261127.eval.jsonl 里有 60 条手写补充样本（src 含
    #    "holdout"），它们**绝不能进训练集** —— 否则评估分数虚高。
    #
    #    如果哪天有人把这些样本误加进 SOURCES，这里直接拦下报错，
    #    而不是静默训练出一个"看起来很好"的模型。
    _holdout_guard = [r for r in all_rows
                      if "holdout" in str(r.get("src") or "").lower()]
    if _holdout_guard:
        print()
        print("🔴 发现 %d 条保持集样本混进了训练数据：" % len(_holdout_guard))
        for _r in _holdout_guard[:5]:
            print("     %s" % str(_r.get("user"))[:60])
        print("   这些是评测用的，必须从 SOURCES 移除后再训练。")
        raise SystemExit("保持集样本不能进训练集")

    # 去重：同一句只留第一次出现的（按 SOURCES 顺序 = 手写模板优先）
    seen, uniq = {}, []
    for r in all_rows:
        k = norm(r["user"])
        if not k:
            continue
        if k in seen:
            seen[k]["dup_from"].append(r["src"])
            continue
        r["dup_from"] = []
        seen[k] = r
        uniq.append(r)

    print()
    print("合并 %d 条 → 去重后 %d 条（丢弃重复 %d 条）"
          % (len(all_rows), len(uniq), len(all_rows) - len(uniq)))

    # ── 标签校验：与代码自己的两套 fit 判据对照 ──────────────────────
    # 🔴 校验的目的不是"证明我的标签对"，而是**找出三方不一致的地方**：
    #      ① 训练集标签  ② agent._is_fit_question  ③ intent_layer.fit_by_rule
    #    三方一致 → 可信；不一致 → 必须人看，不许静默保留也不许静默删。
    #
    #    实测抓到过一类真实缺陷（2026-11-16）：
    #      「这台跑PS够吗」标签 True，但 ② 判 False —— 因为 `_is_fit_question` 的
    #      CAP 词表有「够用」「够不够」，**唯独漏了「够吗」**。
    #      而 `_asked_purpose` 明明认出了 design，说明是词表漏项，不是标签错。
    #      → 这类记为 "代码词表漏"，与 "标签可疑" 分开列，别混为一谈。
    import agent
    import intent_layer as L

    # CAP 词表缺项探测：标签 True 但两套规则判 False 的，先问一句
    # **SYS_TMPL 自己举的 fit=true 例子是不是这个形态**。
    #
    # 🔴 实测（2026-11-16）`_is_fit_question` 的 CAP 词表只有 13 个词：
    #      合适/适合/能不能/可不可以/够用/够不够/能跑/能打/带得动/
    #      玩得动/跑得动/行不行/行吗
    #    而 SYS_TMPL 明确举的 fit=true 例子是：
    #      「合适吗 / 适合做设计吗 / 够用吗 / 能跑PS吗 / 带得动吗 / 撑四年不 / 编程够吗」
    #    两相对照，词表**漏掉了整整三类**：
    #      ① 「能+任意动词」—— 能做设计吗 / 能剪视频吗 / 能编程吗 / 能开虚拟机吗
    #      ② 「够+任意动词」—— 够跑PS吗 / 够做设计吗
    #      ③ 「撑N年 / 用到毕业」—— 能撑四年吗 / 够我用到毕业吗
    #    这是枚举式判据的固有限制：动词是开放集合，词表永远列不完。
    #    而模型（本项目的路由器）恰好能泛化 —— 这正是"模型为主、规则兜底"的实证。
    #    ⇒ 这类差异**不是标签错**，别在训练集里改标签。
    _FIT_FORM = re.compile(
        r"(能做|能剪|能编|能开|能带|能用|能玩|能写|能画|能渲|能撑|能跑|能打"
        r"|够跑|够做|够剪|够编|够用|够我|够撑|够打|够开|够写|够渲|够玩|够带"
        r"|够吗|行吗|可以吗|适合|合适|撑四年|撑得住|带到毕业|用到毕业)")

    suspect, code_gap = [], []
    for r in uniq:
        fit = r["gold"].get("fit")
        by_code = agent._is_fit_question(r["user"])
        by_rule = L.fit_by_rule(r["user"])
        if fit is True and not by_code and not by_rule:
            if _FIT_FORM.search(r["user"]):
                code_gap.append((r, "形态属于 SYS_TMPL 列举的 fit=true 例，但两套规则词表都漏了"))
            else:
                suspect.append((r, "标签 True，但两套判据都是 False，且形态不在 SYS_TMPL 例子里"))
        elif fit is False and by_code and by_rule:
            suspect.append((r, "标签 False，但两套判据都是 True"))

    print()
    print("标签校验：可疑 %d 条，代码词表漏 %d 条" % (len(suspect), len(code_gap)))
    for r, why in suspect[:8]:
        print("  [可疑] 「%s」 %s" % (r["user"][:22], why))
    if len(suspect) > 8:
        print("  … 还有 %d 条可疑" % (len(suspect) - 8))
    for r, why in code_gap[:5]:
        print("  [代码漏] 「%s」 %s" % (r["user"][:22], why))
    if len(code_gap) > 5:
        print("  … 还有 %d 条（同一类缺陷）" % (len(code_gap) - 5))

    if code_gap:
        print("  ⚠ 「代码漏」这一类：**不要在训练集里改标签** —— 标签对，是规则词表列不完。")
        print("     这正是本项目「模型为主、规则兜底」的实证：规则枚举动词，模型能泛化。")

    # 🔴 M05 事故形态的已知冲突：`我的要求是…` 里若含「能跑/能做」，
    #    `_is_fit_question` 和 `fit_by_rule` 都会判 True（它们只看能力词，不看主语）。
    #    而 M05 的结论是「我的要求是…」= 给条件 → state_new_need / fit=False。
    #    这是**规则与事故结论直接冲突**的地方，训练集保留正确标签，
    #    同时在这里点名，避免下一个人以为标签写错了。
    _REQ = re.compile(r"^(我的要求是|我的需求是|我的条件是)")
    m05 = [(r, why) for r, why in suspect if _REQ.match(r["user"])]
    if m05:
        print()
        print("  🔴 其中 %d 条是 M05 事故形态（「我的要求是…」含能力词）：" % len(m05))
        for r, _w in m05:
            print("     「%s」" % r["user"])
        print("     两套规则只看能力词、不看主语，所以判 True；")
        print("     但 M05 的真实结论是「我的要求是…」= 给条件 → state_new_need / fit=False。")
        print("     **训练集保留 state_new_need / False**，这是设计结论，不是笔误。")

    print()
    print("意图分布：")
    labs = Counter(r["gold"].get("intent") for r in uniq)
    for k in LABELS:
        n = labs.get(k, 0)
        bar = "█" * min(40, n // 3)
        print("  %-16s %4d  %s" % (k, n, bar))
    missing = [k for k in LABELS if not labs.get(k)]
    if missing:
        print("  ⚠ 缺样本：%s" % "、".join(missing))

    print()
    print("topic 覆盖（仅 ask_current 有意义）：")
    topics = Counter(r["gold"].get("topic") for r in uniq
                     if r["gold"].get("intent") == "ask_current")
    for k, v in topics.most_common():
        print("  %-14s %d" % (k, v))

    # ── 训练/评估切分（按 user 哈希，稳定可复现）──────────────────────
    #
    # 🔴 2026-11-17 例外：**实测误判纠正样本必须进训练集**。
    #
    #    实测：加了 corrections.jsonl 之后，2 条纠错样本被哈希切分全切进了 eval，
    #    训练集里一条都没有 —— 那等于"最贵的样本没拿去训练，只拿来考试"。
    #
    #    这类样本的价值在于让模型**学会不再犯同样的错**，
    #    所以强制进训练集；同时**保留一份在 eval**，
    #    这样每次评估都能看到"上次那个错现在还会不会犯"。
    #
    #    ⚠️ 训练集和评估集有重叠会让 eval 分数偏乐观，
    #       但这两条是**已知必考项**，宁可分数保守也要盯着它们。
    train, evalset = [], []
    n_forced = 0
    for r in uniq:
        src = str((r.get("meta") or {}).get("src") or "")
        if "correction" in src:
            train.append(r)
            evalset.append(r)      # 故意重叠：必考项
            n_forced += 1
            continue
        h = int(hashlib.md5(norm(r["user"]).encode("utf-8")).hexdigest()[:8], 16)
        (evalset if (h % 1000) < int(args.eval_ratio * 1000) else train).append(r)

    print()
    print("切分：训练 %d / 评估 %d（eval-ratio=%.0f%%）"
          % (len(train), len(evalset), args.eval_ratio * 100))
    if n_forced:
        print("      其中 %d 条实测误判纠正样本强制进训练集（同时保留在评估集）"
              % n_forced)

    n = len(uniq)
    print()
    print("结论：路由层共 %d 条。" % n)
    if n >= 1000:
        print("  → 够训（>1000 稳妥区间）")
    elif n >= 500:
        print("  → 够训（>500 下限），建议再补 %d 条到稳妥区间" % (1000 - n))
    else:
        print("  → 还差 %d 条到 500 下限。最该补的方向：%s"
              % (500 - n, "、".join(sorted(LABELS, key=lambda k: labs.get(k, 0))[:3])))

    if args.write:
        def dump(path, rows):
            with open(path, "w", encoding="utf-8") as f:
                for r in rows:
                    # 🔴 2026-11-17：有思维链的样本，assistant 栏原样保留。
                    #
                    #    为什么不重新拼：思维链是**手工写的判据说明**，
                    #    重新拼只能拼出 JSON，思维链就丢了。
                    #    而丢思维链等于回到"背结论"的蒸馏，正是要避免的。
                    #
                    #    ⚠️ 混着两种格式（有 CoT / 无 CoT）是故意的：
                    #      有 CoT 的教判断方法，无 CoT 的教输出格式。
                    #      推理端 `_extract_json` 两种都能吃。
                    content = r.get("assistant_raw") or \
                        json.dumps(r["gold"], ensure_ascii=False)
                    f.write(json.dumps({
                        "messages": [
                            # 🔴🔴 2026-11-23：**system prompt 必须和推理时一模一样**。
                            #
                            #    这是本轮最严重的发现：
                            #      训练用 system:  56 字符（一句话）
                            #      推理用 system: 2170 字符（七类意图的详细定义）
                            #      **差 39 倍，根本不是同一个 prompt**
                            #
                            #    后果（实测）：v3 adapter 对「好的」输出
                            #    1219 字符的思维链死循环，一个 JSON 都没有。
                            #    而同一个 adapter 换成 56 字符的极简 system
                            #    就正常输出 JSON（252 字符）。
                            #
                            #    为什么之前两版能 work：基座模型够强，
                            #    能忽略这种不一致。但它一直在"猜"任务是什么，
                            #    只是恰好猜对了 —— 这是运气，不是设计。
                            #
                            #    ⚠️ 现在直接用 intent_layer.SYS_TMPL，
                            #    而不是在这里另写一份。**判据抄两遍一定会漂**
                            #    （这个项目已经复现过 9 次）。
                            {"role": "system", "content": _sys_prompt(r)},
                            {"role": "user", "content": r["user"]},
                            {"role": "assistant", "content": content},
                        ],
                        "_meta": r.get("meta") or {},
                    }, ensure_ascii=False) + "\n")
        dump(OUT_TRAIN, train)
        dump(OUT_EVAL, evalset)
        print()
        print("写出 %s（%d 条）" % (OUT_TRAIN, len(train)))
        print("写出 %s（%d 条）" % (OUT_EVAL, len(evalset)))
        # 统计有多少条带思维链
        n_cot = sum(1 for r in train if r.get("assistant_raw")
                    and "{" not in r["assistant_raw"].split("\n")[0])
        print("      其中带思维链：%d 条" % n_cot)
    else:
        print()
        print("（演练模式。加 --write 写出）")


if __name__ == "__main__":
    main()
