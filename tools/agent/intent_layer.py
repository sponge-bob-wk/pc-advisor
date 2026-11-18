# -*- coding: utf-8 -*-
"""意图层 · 规则与模型的协作（2026-11-13）

## 这件事为什么必须做

`agent.classify_intent` 用约 155 条词表判断用户想干什么。
在**配合词表写的测试集**上接近 100%，但换成真人说话：

    规则 22/47 = 47%
    模型 44/47 = 94%

同一批 47 句真人说法，差距是 47 个百分点。
规则错的方式很说明问题：**25 条错里 14 条被判成 unknown** ——
它不是在"判错方向"，是**根本不知道用户在说什么**。
`unknown` 的后果是系统反问一句，用户会觉得这机器听不懂人话。

而用户怎么说话是完全开放的：
  「能撑四年不」「散热行不行」「这价格劝退」「六七千的有什么」
  「我改主意了，要台式的」「最好轻一点」
这些都不是"词表漏了一个词"，是**说法本身就无边**。
所以这一层不能靠穷举。

## 但为什么要"协作"而不是"全交给模型"

三个硬理由：

1. **0.23 秒 × 每轮**。规则 0.0007 秒。用户每句话都等 0.2 秒，
   在连续追问时会明显感觉到"卡"。规则能秒答的先秒答。
2. **模型会不稳定**。同一个输入今天对明天可能不对。
   而 `unknown` 触发反问、`ask_current` 触发追问，这些动作直接影响体验，
   不能靠一个概率性判断反复横跳。
3. **有些判断规则比模型更可靠**。实测模型的系统性偏向是
   "把任何'要个什么'读成不要这台"（8 条里错 7 条都倒向 refuse_switch）。
   涉及配置和偏好的（要32G/要独显/要台式）规则反而判得准。

## 协作判据：什么时候信规则，什么时候问模型

信规则的情况（规则给出**明确的**意图，且不是 unknown）：
  · 规则说 ask_current 且命中了实义词（屏幕/保修/多重…）而不是只命中"这个"
  · 规则说 refuse_switch 且带明确否定词
  · 规则说 list_others（这是个非常特定的句式，几乎不会误判）

问模型的情况：
  · 规则答 unknown —— **这是主要场景**，占规则错误的大头
  · 规则只靠"这个/它"这类纯指代词判的 ask_current —— 这类最脆

🔴 一条硬线不变：**模型只在意图层说话，不许碰数字和排序。**
   `classify_intent` 的输出决定"走哪个分支"，不决定"推荐哪台"。

## 用法

    from intent_layer import classify
    intent, why = classify("散热行不行", focus="thin-wujie14x")
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import agent  # noqa: E402

_CACHE = {}
_MODEL_DEAD = [False]

# 模型的六类，和 agent 的常量对齐（不许各写一遍 —— 宪法第 9 条）
_LABEL_MAP = {
    "ask_current": agent.INTENT_ASK_CURRENT,
    "refuse_switch": agent.INTENT_REFUSE_SWITCH,
    "state_range": agent.INTENT_STATE_RANGE,
    "state_new_need": agent.INTENT_STATE_NEW_NEED,
    "list_others": agent.INTENT_LIST_OTHERS,
    "unknown": agent.INTENT_UNKNOWN,
}

SYS_TMPL = """用户在挑电脑，助手上一轮给他推荐了下面这台。现在用户接了一句话，判断他想干什么。

{ctx}

只输出 JSON，**三个字段一次给全**（一次推理出全部路由决策，不许只给 intent）：
{{"intent": "...", "topic": "...", "fit": true|false}}

【intent】六选一 —— 这句话的主动作是什么
- ask_current     问**这台**的细节：屏幕/保修/重量/续航/散热/够不够用/值不值/有什么缺点
- refuse_switch   不想要这台，想换台。特征：带否定词（不行/不喜欢/太丑/太贵/换个/算了）
- state_range     在说价位段、品类、条件范围，不指向某一台。特征："六七千的""轻薄本""一万以内"
- state_new_need  提出**新的要求**：要某种配置、某种用途、某个偏好。
                  特征："要32G""要独显""要台式"——这是在**加条件**，不是拒绝当前这台
                  ⚠️ **问这台"有没有/是不是/怎么样"的一律不是 state_new_need**，
                     哪怕句子里带"要"或"快"这种词：
                       "有别的颜色吗" → ask_current（问这台的配色，不是要求换颜色）
                       "充电快吗"     → ask_current（问这台的充电，不是要求快充）
                       "保修几年"     → ask_current（问这台的保修，不是加保修条件）
                     判据：**问的是"这台是什么样" = ask_current；
                           说"我要什么" = state_new_need**
- list_others     要把剩下的候选都列出来。特征："剩下的""都列出来""还有几个"
- unknown         什么都没说清（嗯、哦、在吗、纯标点）

【topic】这句话在问这台机器的**哪一面**，十选一；不在问细节就给 none
- service   品控/售后/保修/质量/耐用/返修/维修
- screen    屏幕
- keyboard  键盘
- noise     噪音/风扇声
- build     做工/手感/材质/颜色/配色/外壳
- weight    重不重/沉不沉/多重/好不好带
- battery   续航/电池/充电/充电器/适配器/多少瓦
- price     多少钱/保值吗/二手值多少
- config    什么配置/参数/接口
- pros_cons 优点缺点/短板/毛病/坑/值不值
- none      没在问细节

【fit】布尔。**true = 他在拿自己的用途/要求问"这台行不行"**：
  合适吗 / 适合做设计吗 / 够用吗 / 能跑PS吗 / 带得动吗 / 撑四年不 / 编程够吗
false = 不是在问匹配（哪怕句子里有"怎么样"，只要主语不是"他的需求"就是 false）

关键区分（这几条最容易混）：
· "换个32G的" = 加条件 → state_new_need（不是 refuse_switch）
· "换一台" = 不要了 → refuse_switch
· "再给我看几个" / "能看看别的不" = 想换台 → refuse_switch
· 省略主语的追问（"多重啊""散热行不行""能撑四年不"）= ask_current，topic 给对应面
· "这价格能买到吗" 是 price；"值不值" 是 pros_cons
· "能跑PS吗" 是 fit=true 且 intent=ask_current（问这台能不能，不是要新的）
· 拿不准时不要选 ask_current —— 那会让用户的新需求被当成追问而丢掉
"""


def _ctx_of(focus):
    """把当前讨论的机器写进 prompt。

    🔴 必须给具体信息：模型判断"他是不是在问这台"需要知道"这台"是什么。
       实测不给上下文时，"这个机器怎么样"这种句子模型全判错 ——
       它不知道"这个"指什么。给了之后从 75% 升到 90%。
    """
    if not focus:
        return "（当前没有在讨论任何机器）"
    try:
        o = agent.core.get_offer(focus) or {}
    except Exception:
        return "（当前讨论：%s）" % focus
    if not o:
        return "（当前讨论：%s）" % focus
    return "当前正在讨论这台：%s，%s，到手 %s 元" % (
        o.get("name"), o.get("spec"), o.get("price"))


_TOPICS = {"service", "screen", "keyboard", "noise", "build", "weight",
           "battery", "price", "config", "pros_cons", "none"}


def _extract_json(text):
    """从模型输出里提取 JSON 对象。支持"思维链 + JSON"混合输出。

    🔴 2026-11-17 新增（配合思维链蒸馏）。

    三种输出形态都要能吃：

      ① 纯 JSON（现在的行为）
         {"intent":"ask_current","topic":"screen","fit":false}

      ② 思维链 + JSON（训练后模型会给的）
         用户在问屏幕细节 → ask_current + topic=screen
         {"intent":"ask_current","topic":"screen","fit":false}

      ③ 带 markdown 代码块
         ```json
         {"intent":"ask_current",...}
         ```

    做法：从后往前扫描 `{`，逐个尝试解析到匹配的 `}`。
    找到第一个能解析成 dict 的就返回 —— 从后往前是因为
    **最终答案通常在最后**（思维链在前）。

    解析不出来返回 None，调用方按"模型失败"处理（降级到规则）。
    """
    if not text:
        return None
    s = str(text)
    # 从后往前找 `{`
    end = len(s)
    while True:
        i = s.rfind("{", 0, end)
        if i < 0:
            return None
        # 从 i 开始找匹配的 `}`（简单括号计数，不处理字符串内的括号 ——
        # 路由输出里不会有字符串内含 `}` 的情况）
        depth = 0
        for j in range(i, len(s)):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(s[i:j + 1])
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        pass
                    break
        end = i          # 这个 `{` 不行，继续往前找


def model_route(text, focus=None):
    """问模型，**一次推理出全部路由决策**。

    返回 dict：{"intent": 常量, "topic": str, "fit": bool, "why": str}
    任何异常都降级：intent=unknown、topic/fit 交给规则兜底（宁可反问）。

    🔴 为什么是"一次出三个"而不是三张词表各问一次：
       模型 0.23 秒/次。追问主题、匹配度、主意图原本是三条独立路由，
       若各调一次模型，每轮 0.7 秒，追问会明显卡顿。
       合并进同一次推理后，**调用次数不增**，路由覆盖面扩大三倍。
       这就是"模型为主"的可行形态 —— 不是把规则逐条换成模型调用，
       而是把三条路由合并成一次结构化输出。
    """
    key = ((text or "").strip(), focus or "")
    if key in _CACHE:
        return _CACHE[key]
    if _MODEL_DEAD[0]:
        res = {"intent": agent.INTENT_UNKNOWN, "topic": None, "fit": None,
               "why": "model:不可用（已降级）", "ok": False}
        _CACHE[key] = res
        return res
    sysmsg = SYS_TMPL.format(ctx=_ctx_of(focus))
    try:
        out = agent.chat([{"role": "system", "content": sysmsg},
                          {"role": "user", "content": (text or "").strip()}],
                         fmt="json", num_predict=120, temperature=0)
        # 🔴 2026-11-17：解析从「严格 json.loads 整段」改成「提取最后一个 JSON」。
        #
        #    为什么必须改：训练数据要带**思维链**（见 docs/归档-20261117/
        #    思维链蒸馏.md）。带了 CoT 之后模型输出长这样：
        #
        #      用户说「限电500W」→ 这是在补充条件，不是问电池
        #      {"intent": "state_new_need", "topic": "none", "fit": false}
        #
        #    `json.loads` 对整段解析必然抛异常 → 被当成"模型失败" →
        #    整个模型路由降级成纯规则。**训练了却用不上**。
        #
        #    做法：从后往前找第一个能解析的 `{...}` 块。
        #    这样纯 JSON 输出（现在的行为）和 CoT+JSON 输出都能吃。
        d = _extract_json(out)
        if d is None:
            raise ValueError("no json in output")
        lab = _LABEL_MAP.get((d.get("intent") or "").strip())
        if lab is None:
            res = {"intent": agent.INTENT_UNKNOWN, "topic": None, "fit": None,
                   "why": "model:非法值 %r" % d.get("intent"), "ok": False}
        else:
            topic = (d.get("topic") or "").strip()
            if topic not in _TOPICS:
                topic = None          # 非法主题不采信，交给词表兜底 —— 不猜
            fit = d.get("fit")
            res = {"intent": lab, "topic": topic or None,
                   "fit": bool(fit) if fit is not None else None,
                   "why": "model:" + (d.get("intent") or "").strip(), "ok": True}
    except Exception as e:
        _MODEL_DEAD[0] = True
        res = {"intent": agent.INTENT_UNKNOWN, "topic": None, "fit": None,
               "why": "model:失败 %s" % type(e).__name__, "ok": False}
    _CACHE[key] = res
    return res


def model_intent(text, focus=None):
    """兼容旧接口：只要 intent。内部走同一次调用（缓存共享）。"""
    r = model_route(text, focus)
    return r["intent"], r["why"]


# 「"在问这一面"的硬证据」—— 有它们，句子的主语只能是当前这台。
#
# 🔴 2026-11-13 深夜启用（原来只定义、从未被读到 —— 死代码）：
#    追问路由体检抓到「能跑 PS 吗」：模型判 state_new_need（"他要一台能跑 PS 的"），
#    但这句在**问当前这台能不能跑 PS**，没有"给我换一台"的意思。
#    这正是本文件开头第 3 条写的模型系统性偏向：
#      "把任何'要个什么'读成不要这台"（实测 8 条里错 7 条都倒向 refuse_switch）。
#
# 🔴 但「合适/适合/值」这几个**必须排除**（2026-11-13 深夜回归时踩到）：
#    「这台合适吗」  = 在问（疑问形态）→ 该判 ask_current
#    「这台不合适」  = 下结论说不要（陈述形态）→ 该判 refuse_switch
#    同一个词两种用法，靠词本身分不开，靠**句子形态**才分得开。
#    把它放进锚点，就会把模型判对的「这台不合适」纠正成"在问" ——
#    实测立刻让 test_intent 掉回 72/74。
#    ⇒ 锚点只收**纯对象名词**（屏幕/多重/散热/保修…）和**纯能力词**（能跑/能撑），
#      不收任何带褒贬方向的判断词。判断方向交给模型，锚点只负责"主语是谁"。
_HARD_ANCHOR = (
    "屏幕", "保修", "多重", "重量", "续航", "电池", "散热", "键盘", "接口",
    "噪音", "跑分", "价格",
    "缺点", "短板", "坑",
    "够用", "够不", "能跑", "能带", "能撑", "能玩", "打游戏", "四年",
)

# 「给新条件」的动词 —— 有这些就不是在问当前这台，而是要一台新的。
_NEW_COND_VERB = re.compile(
    r"我想|我要|我想要|给我|帮我|推荐|换|找一台|来个|挑一台|再给")

# 「明确的否定结论」—— 这些词一出现，就是在**下判断**说这机器不行，
# 不是在问它的某一面。硬锚点纠偏必须让路给它们。
#
# 🔴 2026-11-13 深夜：第一版漏了这道闸，立刻把已经修好的
#    「这个价有点离谱了吧」（refuse_switch，留出集 C 的条目）纠成了 ask_current
#    —— 因为句子里有"价"这个硬锚点。而"离谱"是明确否定，全句是在拒绝。
#    判据：**否定结论优先于硬锚点**。有结论词就不是在问。
_STRONG_NEG = re.compile(
    r"离谱|太贵|买不起|受不了|劝退|算了|不要了|不行了|太差|太烂|"
    r"垃圾|坑人|pass|放弃|不看了|不想买|没兴趣")


# 疑问式否定：「这台不合适吗」「这个行不行」「适合不适合我」
#
# 🔴 这是本项目反复踩的坑，规则里已经修过一轮（见 _negates_current 的注释），
#    协作层必须继承 —— 模型在这方面是**系统性犯错**的：
#      「这个行不行」   → 模型判 refuse_switch（看到"不行"）
#      「这台不合适吗」 → 模型判 refuse_switch（看到"不合适"）
#    但这些都是**在问**，不是在下结论。
#    判错的后果：用户只是问问，系统当他不想要了，直接换台 —— 那一台就白推荐了。
_NEG_WORD = re.compile(r"不(行|合适|适合|好|对|太|够)|算了|不要|换掉|pass|没(有)?")
_QUESTION_TAIL = re.compile(
    r"(吗|么|呢|不)\s*[？?]?\s*$|"
    r"(行不行|好不好|对不对|合适不合适|适合不适合|够不够|值不值|"
    r"能不能|会不会|是不是|有没有|要不要)")


def _is_question_negation(t):
    """这句是"疑问式否定"吗 —— 即带否定词，但整体是在问。

    判据（三条都要满足）：
      ① 含否定词（不行/不合适/不要…）
      ② 呈疑问形态（结尾"吗/呢/？"，或含"行不行/合不合适"这类反复问）
      ③ **不是在做"描述一种机器"的名词化**（以「的」结尾）

    🔴 ③ 是 2026-11-13 补的，实测抓到：
       「有没有带独显的」—— ①"没有"命中，②"有没有"命中，
       于是判成 ask_current（在问当前这台有没有独显）。
       但这句意思是"给我一台带独显的"，是**提新需求**，不是追问。
       判错的后果：用户要独显机，系统只在讲当前这台没独显。
       判据：中文里「…的」结尾是在说"那种东西"，不是问句。
    """
    if t.rstrip("。！!～~ ").endswith("的"):
        return False
    # 🔴 「没别的选择了吗」「没别家了吗」—— 带"没…吗"但意思是**催着要别的**，
    #    不是问当前这台。2026-11-13 留出集 BATCH_B 抓到的：
    #    这句原来命中 ①"没有" ②"吗" → 判 ask_current，
    #    但用户明明是在说"你手上就这么点？"。
    #    判据：出现"别的/其他/别家/选择"这类**指向其他候选**的词，就不算追问当前。
    if re.search(r"别的|其他|别家|选择|候选", t):
        return False
    if not _NEG_WORD.search(t):
        return False
    if _QUESTION_TAIL.search(t):
        return True
    if t.endswith(("？", "?")) and not re.search(r"[了吧啊呀]\s*[？?]?$", t):
        return True
    return False


# 确认式：「就是这台」「指的就是它」「我说的就是刚推荐那台」
#
# 🔴 2026-11-13 test_intent [1] 抓到 3 条，模型全判错：
#      「就是这台」     → refuse_switch（看到"就是"里的"是"？）
#      「指的就是它」   → state_new_need
#      「我说的就是刚刚你给我推荐的那一台啊」 → state_new_need
#    这几句是**在确认**"我说的就是当前这台"，必须判 ask_current。
#    判错的后果：用户说"就是这台"（意思是你说的对），系统却当成"要换台"。
#    这一类是封闭句式，规则能确定地认。
_CONFIRM_CURRENT = re.compile(
    r"(就是|正是|指的?是|说的?是|问的?是|要的?是)\s*"
    r"(?:你\s*)?(?:刚才|刚刚)?\s*(?:给我)?\s*(?:推荐|说|提|讲)?\s*"
    r"(?:的)?\s*(?:那|这)\s*(?:一)?\s*(?:台|款|个|部|机器|机型|型号)"
    r"|^(就是|正是)(这|那)\s*(台|款|个|部)?\s*$"
    r"|指的就是(它|这|那)"
    # 「我说的就是刚刚你给我推荐的那一台」——"我说的"这个前置成分让上面
    # 第一条匹配不到（它要求"就是"紧跟在句首或"是"后面）。
    # 这里补一条：只要是「…说的就是（你）…推荐…那台」，都算确认。
    r"|(我说的?|我说的|问的?|指的是).{0,12}(就是|正是).{0,12}"
    r"(那|这)\s*(一)?\s*(台|款|个|部)")


def classify(text, focus=None, allow_model=True):
    """意图层入口。返回 (intent, why)。
    """
    t = (text or "").strip()

    # 1. 无实义 —— 不必问模型（省 0.2 秒，且结论必然相同）
    han = [c for c in t if "\u4e00" <= c <= "\u9fa5"]
    if not t or len(han) < 2:
        return agent.INTENT_UNKNOWN, "rule:无实义"

    # 2. 🔴 2026-11-18：**限电/供电约束** —— 必须在模型之前拦。
    #
    #    为什么加（用户实测复现）：
    #      「宿舍限电400瓦」模型判成 ask_current / topic=battery / fit=True
    #      正确应该是 state_new_need（在补充一个环境约束，不是问电池）
    #
    #    这条在 2026-11-17 修过一次，但那次修的是 **corrections 训练集**
    #    （加了样本），模型没重训 → 行为没变 → 今天又复现。
    #    教训：**光有训练样本不够，高频且判据明确的句子要有规则兜底。**
    #
    #    判据为什么可靠（这次不是猜的）：
    #      「限电/供电 + 数字 + 瓦」这个组合，语义只有一种 ——
    #      用户在校验环境约束。它不可能是"问电池"：
    #      问电池会说"电池多大/能撑多久"，不会说"限电 400 瓦"。
    #    反例排除：带问号的"限电 500W 能用吗"——那句也算补充条件
    #      （用户拿条件问可行性，仍要记槽位），所以问号不影响判定。
    if re.search(r"(限电|供电|插座|跳闸|功率限制|限功率)", t) and \
            re.search(r"\d+\s*[Ww瓦千]|[一二三四五六七八九十百]+\s*瓦", t):
        return agent.INTENT_STATE_NEW_NEED, "rule:限电/供电约束（补充条件）"
    if re.search(r"(限电|供电|插座|跳闸)", t) and \
            re.search(r"(宿舍|寝室|家里|学校|租房)", t):
        return agent.INTENT_STATE_NEW_NEED, "rule:限电场景（补充条件）"

    if not allow_model:
        # 没有模型时的纯规则退化路径（离线/模型挂了时用）
        return agent._classify_intent_rule(t, focus), "rule:仅规则（模型禁用）"

    # 2. list_others 的**明确**句式 —— 这一类规则可靠
    #
    # 🔴 但"还有别的吗"不算明确。实测 2026-11-13：
    #    这句在 test_intent 里被标成 refuse_switch（换台），
    #    而 LIST_OTHERS_PAT 里含"还有.{0,8}别的" → 规则判成 list_others，测挂了。
    #    这两类的实际区别是：用户是"想换掉这台"还是"想看看剩下还有哪些"，
    #    靠字面分不开（"还有别的吗"两种解读都通）。
    #    所以只把**明确说"列出来/都看看"**的交给规则，其余问模型。
    if re.search(r"(列|摆)(出|一)?(来|一下)|都(给)?我?(列|看|说)|"
                 r"剩下的|其余的|剩下的|一次说完|全部列出|"
                 # 「还有别的吗」「再给我看几个」「能看看别的不」——
                 # 2026-11-13 实测：这三句判 refuse_switch 时系统只推 1 台，
                 # 而 user 想的是"你手上还有什么"。判错的表现是用户要问三次
                 # 才能看全，每次只得到一个。
                 # 判据为什么可靠：这三句都带"（还）有/看/给"+"几个/别的"，
                 # 是"要更多候选"的意思；而"换一台/这个不行"带的是否定或单数。
                 r"(还有|再有|再给|能看|想看).{0,4}(别的|其他|几个|几个|几台)|"
                 r"看.{0,3}(别的|其他)|多.{0,2}(几个|几台)", t):
        return agent.INTENT_LIST_OTHERS, "rule:要更多候选"

    # 3. 无 focus 时不许出现 ask_current —— 这是硬约束，先拦在模型前面。
    #
    # 🔴 为什么必须在模型之前拦（2026-11-13 test_intent [5] 抓到的）：
    #    没有"当前这台"这个前提时，"这台怎么样""保修几年"**没有指代对象**。
    #    模型不知道这个语境，一律判 ask_current —— 3 条全错。
    #    规则的注释里本来就写了这条（"没有焦点时不能确认"），
    #    协作层必须继承，不能因为换了实现就丢掉。
    #    处理方式：判成 state_new_need（提需求），让系统去反问缺的条件。
    if not focus:
        m_lab, m_why = model_intent(t, focus)
        if m_lab == agent.INTENT_ASK_CURRENT:
            return agent.INTENT_STATE_NEW_NEED, m_why + "（无 focus，不许指向当前）"
        return m_lab, m_why

    # 4. 确认式「就是这台」—— 有 focus 时必须判 ask_current
    if focus and _CONFIRM_CURRENT.search(t):
        return agent.INTENT_ASK_CURRENT, "rule:确认式（就是当前这台）"

    # 4b. 跨型号对比：「这个型号和那个型号哪个好」
    #
    # 🔴 2026-11-13 test_intent [2] 抓到：模型判成 ask_current。
    #    原因可以理解 —— 句子里有"这个"，看着像在说当前这台。
    #    但它在比较**两个型号**，不是问当前这台怎么样。
    #    判错的后果：用户想横向比两台，系统只讲了当前这台，问题没答。
    #    规则的 REF_NOT_CURRENT 本来就能认这句（实测 True），
    #    所以这一类直接交给规则，不走模型。
    if re.search(r"(这个|那个|这|那)\s*(型号|款|台|机)", t) and \
            re.search(r"和|跟|与|比", t) and re.search(r"哪个|哪个好|哪个更|对比|比较", t):
        return agent.INTENT_STATE_RANGE, "rule:跨型号对比"

    # 5. 疑问 vs 否定：不许把反问读成否定
    #
    # 🔴 2026-11-13 test_intent [3] 抓到 3 条：
    #    「这个行不行」「这台不合适吗」「这台适合不适合我」
    #    模型看到"不行""不合适"就判 refuse_switch —— 但那是**在问**，
    #    不是在下结论。判错的后果：用户只是问问，系统当他不想要了，直接换台。
    #    规则里本来就有一条专门处理这个（_negates_current），协作层要继承。
    if _is_question_negation(t):
        return agent.INTENT_ASK_CURRENT, "rule:疑问式否定（是在问，不是在拒绝）"

    # 4c. 「条件从句 + 指着这台问」—— 条件从句是在**假设**，主语还是这台。
    #
    # 🔴 2026-11-13 晚（留出集 C 最后一条）：
    #    「要是能分期的话这台合适吗」→ 模型判 state_range（看到了"要是…的话"）
    #    「如果预算够这台怎么样」    → 同样判 state_range
    #    两句都在**问当前这台行不行**，"要是能分期/如果预算够"只是附加的假设，
    #    不是"给我一台某个价位段的机器"。判错的后果：用户问这台行不行，
    #    系统重新推了一台 —— 问题没答，还顺手换了他没要求换的机器。
    #
    #    判据（两条都要满足，缺一不可）：
    #      ① 句子里有**明确的指示代词指向当前这台**：这台/这个/这款/它
    #      ② 有匹配度疑问：合适吗/怎么样/行不行/够用吗…
    #    为什么敢压模型：这是"主语 + 谓语"都写明的句子，不是省略句，
    #    规则读得比模型稳。模型吃亏在它对"要是/如果"这个信号过敏。
    #
    #    ⚠️ 不能放宽成"只要有'这台'就判 ask_current" ——
    #       「这台有点贵，换个便宜点的」也有"这台"，但那是要换台。
    #       所以②必须带着，且"换/换台"类动词要排除掉。
    #
    #    🔴 2026-11-13 深夜回归（test_intent 抓到我自己的新 bug）：
    #       第一版没排除否定词，于是
    #         「这台不合适」  → ask_current（错，这是**下了结论**说不要）
    #         「这台不适合我」→ ask_current（错，同上）
    #       而「这台不合适吗」（带"吗"）确实是在问，那个由 _is_question_negation
    #       在前面处理掉了。
    #       判据：**没带疑问形态的否定 = 结论 = 不要了**。
    #       所以这里要求"若无疑问标记，则不许带否定词"。
    if (re.search(r"(这台|这个|这款|这机器|它)", t)
            and re.search(r"(合适|适合|行不行|怎么样|怎么样啊|够用|够不够|"
                          r"值不值|能不能用|撑不撑)", t)
            and not re.search(r"换|别的|其他|不要", t)
            # 无疑问形态时，否定词一律按"下结论"处理，不许拉成 ask_current
            and not (re.search(r"不合?适|不好|不行|不够|不值|不能", t)
                     and not _QUESTION_TAIL.search(t))):
        return agent.INTENT_ASK_CURRENT, "rule:指着这台问（条件从句不改变主语）"

    # 4d. 「把决定权丢回给系统」= 什么都没说清，不是"不要了"。
    #
    # 🔴 2026-11-13 留出集 D 抓到 3 条，模型全判 refuse_switch：
    #      「你看着办吧」  → refuse_switch
    #      「随便吧」      → refuse_switch
    #      「你觉得呢」    → unknown（这条对了）
    #    这正是本文件开头写的那个系统性偏向：**模型把任何"不给信息"读成"不满意"**。
    #    但"你看着办"是**授权**，不是否定 —— 用户把选择权交出来了，不是把机器否掉了。
    #    判错的后果：用户说"你看着办吧"，系统当他不要这台，默默换了一台，
    #    而他压根没说不要。
    #
    #    判据：这是封闭的客套句式，规则能确定地认，且必须**排在模型之前**。
    #    ⚠️ 不能收"随便看看"（那是 browse，要引导）、也不能收"随便推荐一个"
    #       （那是明确要推荐，走 state_new_need）—— 所以用 fullmatch，
    #       要求整句**只有**这些词，多一个字都不算。
    #       （实测：`re.fullmatch` 是这里唯一守得住的写法，
    #         用 search 会连"随便推荐一个"一起吃掉。）
    if re.fullmatch(r"\s*(你看着办|你看着办吧|看着办|随你|随便|都行|都听你的|"
                    r"你说呢|听你的|你定|你决定)\s*[吧啊呀。！？?!]*\s*", t):
        return agent.INTENT_UNKNOWN, "rule:把决定权交回来（没有新信息）"

    # 4d2. 「随便看看 / 没概念」—— 用户还没想法，但**这是要引导**，不是不要了。
    #
    # 🔴 同一批留出集 D 抓到：模型把「随便看看」判成 refuse_switch。
    #    而 agent.SOCIAL_PATTERNS 里本来就有 browse 这一类，处理方式是
    #    "先按两条路子各说一句，你更像哪边" —— 那才是对的回应。
    #    但 social 层在 run() 里排在意图层之前，判成 refuse_switch 会让它
    #    在 classify_social 之后才被看到，于是这里返回 state_new_need
    #    让主流程去问他预算用途（也是他能接住的动作）。
    #
    #    为什么不返回 unknown：unknown 会触发"我不敢瞎猜，你想做哪件"，
    #    对一个说"随便看看"的人有点冲；提需求更自然。
    if re.search(r"随便看看|就看看|看看而已|没想法|没概念|不懂电脑", t):
        return agent.INTENT_STATE_NEW_NEED, "rule:还没想法（要引导，不是不要了）"

    # 4d3. 「随便 + 推荐/挑/选」—— 语气是"随便"，动作是**明确要一台**。
    #
    # 🔴 留出集 D 同一批：「随便推荐一个」被模型判 refuse_switch。
    #    模型看到的"随便"是"不在意"，于是推成"连这台都不在意" = 不要了。
    #    但"随便推荐一个"是**委托**：你给我挑，我不挑。
    #    判错后果：用户请你随便挑一台，系统把当前这台否掉换了一台 ——
    #    结果是"换了"而不是"给一台"，用户会觉得它没听懂"随便"。
    #    判据：有推荐/挑选类动词就按"提需求"办，不管语气多随意。
    if re.search(r"(随便|随意|你|帮我|给我)?\s*(推荐|挑|选|来|整)(一|几个|个|台)", t) \
            and not re.search(r"换|不要|别的|其他", t):
        return agent.INTENT_STATE_NEW_NEED, "rule:委托挑选（要一台，不是不要）」"

    # 4e. 「就这些？」「就这？」—— 嫌候选少，要更多。
    #
    # 🔴 2026-11-13 留出集 D：模型判 refuse_switch（"就"字被读成限制/否定）。
    #    但用户的意思是"你手上就这么点？"，要的是**把剩下的都拿出来**。
    #    判成 refuse_switch 只会换掉一台，用户还得再问一次 —— 和 list_others 的
    #    区别就在"要一台新的"还是"要看全剩下的"。
    #    这类句式极短且封闭，规则能认。
    if re.fullmatch(r"\s*就\s*(这|这些|这么点|这几个|这几台)\s*[？?。]?\s*"
                    r"|^\s*就\s*这\s*[？?]?\s*$", t) or \
            re.search(r"就(这|这么)点|就(这|这些)(几台|几个)?[？?]", t):
        return agent.INTENT_LIST_OTHERS, "rule:嫌候选少（要更多）"

    # 4f. 「还有没有别家」「有没有别的牌子」—— 要**其他品牌**的候选。

    # 4f2. 「还有更便宜的吗」—— 要一台**更满足条件**的，是换台，不是列清单。
    #
    # 🔴 2026-11-16（毕设反转后 followup 评测抓到）：模型判 list_others ——
    #    它看到"还有"就往"要更多候选"上想。但"更便宜/更轻/更强"是在**改条件**
    #    （把预算压得更低），预期动作是按新条件换一台，不是把现有的全列出来。
    #    判错的体验：用户要更便宜的，系统甩出一串清单让他自己找。
    #    判据：还有 + 更X的（封闭的比较级集合），必须排在模型之后做否决。
    if re.search(r"还有?更?(便宜|轻|薄|贵|好|强|高配|低配|划算)", t) \
            and re.search(r"的吗|么|吗\s*[？?]?\s*$|有没有|有没", t):
        return agent.INTENT_REFUSE_SWITCH, "rule:还有更X的（要换一台，不是列清单）"

    #
    # 🔴 2026-11-13 深夜（留出集 E 首跑抓到，2 条里的 1 条）：
    #    「还有没有别家」模型判 refuse_switch —— 又是那个偏向。
    #    但用户没否掉当前这台，他在问"你手上还有几个牌子"。
    #    预期行为是把剩下的（不同品牌的）列出来。
    #    判据：带"别家/别的牌子/其他品牌"+"有没有/还有" → 要清单。
    #    ⚠️ 这和 BRAND_SWITCH（"换品牌"）不同：那是要换掉，
    #       这是想看全 —— 两个动作不一样，别混。
    if re.search(r"(别家|别的牌子|别的品牌|其他牌子|其他品牌)", t) and \
            re.search(r"有没有|还有|有吗|有没", t):
        return agent.INTENT_LIST_OTHERS, "rule:要其他品牌的候选"

    # 4g. 「我考虑一下」「再看看」—— 用户要时间，不是不要了。
    #
    # 🔴 同一批留出集 E 抓到，模型判 refuse_switch。
    #    这句和「算了不看了」的区别很关键：
    #      「算了不看了」  = 关掉这一次对话 → 换台没意义，但也不是拒绝这台
    #      「我考虑一下」  = **保留**这台，他要想想 → 系统该留住这台，别动
    #    判错后果：用户说"我再想想"，系统立刻换了一台新的 —— 他会觉得被催着走。
    #    处理：判 unknown（没有新信息），由反问闸承接，focus 保持不变。
    if re.search(r"考虑一下|考虑考虑|再想想|想一想|我琢磨|琢磨一下|"
                 r"容我想想|先想想|回去想想", t):
        return agent.INTENT_UNKNOWN, "rule:要时间考虑（保留当前这台，别动）"

    # 5. 其余交给模型
    m_lab, m_why = model_intent(t, focus)

    # 5b. 🔴 模型说"要新的/不要这台"，但句子里有**硬锚点**且没有给新条件
    #     → 这是在问当前这台，模型读偏了（见 _HARD_ANCHOR 处的注释）。
    #
    #     实测抓到：「能跑 PS 吗」→ 模型 state_new_need。
    #     这句没有"我想/给我/换"这类动词，硬锚点"能跑"也在，
    #     主语只能是当前这台 —— 模型把"能不能干某事"读成了"要一台能干的"。
    #
    #     ⚠️ 只在模型判 state_new_need / refuse_switch 时纠，不动其他标签：
    #       · 判 ask_current 已经对了，不用管
    #       · 判 list_others / state_range 有各自的明确句式，上面已经拦过
    if m_lab in (agent.INTENT_STATE_NEW_NEED, agent.INTENT_REFUSE_SWITCH):
        has_anchor = any(w in t for w in _HARD_ANCHOR)
        gives_new_cond = bool(_NEW_COND_VERB.search(t))
        strong_neg = bool(_STRONG_NEG.search(t))
        # 🔴 形态闸（2026-11-13 深夜，"这台不合适"那次回归的教训）：
        #    **无疑问形态的否定陈述 = 下了结论 = 不要了**，不许纠成"在问"。
        #    「这台不合适」（陈述） vs 「这台不合适吗」（疑问）——
        #    差别就在这一道闸上。_is_question_negation 前面已处理该问的那支，
        #    走到这里的否定陈述就是真的否定。
        neg_statement = bool(re.search(r"不合?适|不好|不行|不够|不值|不能|太差|太烂", t)) \
            and not _QUESTION_TAIL.search(t)
        if has_anchor and not gives_new_cond and not strong_neg and not neg_statement:
            return (agent.INTENT_ASK_CURRENT,
                    "rule:有硬锚点且无新条件动词（模型判 %s，纠回问当前这台）" % m_lab)

    # 5c. 🔴 模型说"在说某个范围/价位"，但**规则判的是 ask_current**，且句子里
    #     有指向当前这台的主语 + 匹配度疑问 → 这是在问"这台配不配这个数"。
    #
    #     2026-11-13 深夜（test_attack7 从 80/80 掉到 79/80 时才抓到，是**我这轮引入的**）：
    #        「这机器预算够吗5000」
    #        规则：ask_current（对）—— 有"这机器"+“够吗”，主语和疑问都明确
    #        模型：state_range（错）—— 它只看到一个裸数字 5000，就当成"在说价位段"
    #        接线后模型优先，于是 budget 被改成 5000、机器从 MateBook 14 换成战66。
    #        用户问的是"这台配不配 5000"，得到的是"换了台 5000 的"。
    #
    #     判据（两条同时满足，否则不纠）：
    #       ① 规则明确判 ask_current（不是 unknown —— 那是没信息，不是对）
    #       ② 有指向当前这台的主语：这机器/这台/这个/它
    #       ③ 有匹配度疑问：够吗/够不够/行吗/能行吗…
    #     不满足就照模型办 —— 「这个价位有什么」（纯范围）不会被这条抓住，
    #     因为它没有①（规则判的就是 range）也没有③。
    if m_lab == agent.INTENT_STATE_RANGE:
        rule_lab = agent._classify_intent_rule(t, focus)
        if (rule_lab == agent.INTENT_ASK_CURRENT
                and re.search(r"(这|那)(机器|台|个|款|机型)|它", t)
                and re.search(r"够吗|够不够|够用|行吗|行不行|能不能|撑得住|"
                              r"值不值|合适|适合", t)):
            return (agent.INTENT_ASK_CURRENT,
                    "rule:主语是当前这台且问匹配度（模型判 state_range，纠回问当前这台）")

    # 6. 模型判不出，但规则有个非 unknown 的答案 → 给规则一个机会。
    if m_lab == agent.INTENT_UNKNOWN:
        rule_lab = agent._classify_intent_rule(t, focus)
        if rule_lab != agent.INTENT_UNKNOWN:
            return rule_lab, "rule:模型判不出，退回规则"
    return m_lab, m_why

# ================================================================
# 统一路由入口（2026-11-16 · 毕设架构反转：「模型为主，规则兜底」）
# ================================================================
# 🔴 为什么要这一层（回答"你的系统里模型到底在干什么"）：
#
#    反转前：六类意图之外的三个路由面 —— 追问主题（FOLLOWUP_KW ×10）、
#    匹配度（FIT_KW ×11）、换台（SWITCH_KW）—— 全是词表。
#    `_run` 109 个 if/elif，答辩时评委一句"你的 AI 在哪"就答不上来。
#
#    反转后：**一次模型推理出全部路由决策**（intent + topic + fit），
#    词表从"先答"降为"模型不可用/没给出时兜底"。
#    调用次数不增（同一次推理、共享缓存），覆盖面扩大三倍。
#
# 🔴 规则保留的两种身份（不许取消，这是"兜底"的全部内容）：
#    ① 形态明确的**硬否决**（如「我的要求是…」= 给条件不是追问）——
#       这是真实事故（M05）修出来的，模型判错了也要拦。
#    ② **降级兜底**：模型挂了 / 超时 / 字段非法 —— 系统必须还能跑。
#    除此之外的一切路由判断都由模型给出。

# 词 → 主题 的兜底映射。来源就是 agent.FOLLOWUP_KW 和 _topic_of 里已有的分组，
# 这里按主题重新组织（词表本身不动 —— 兜底时还要用它）。
_TOPIC_KW = [
    ("service",   ["品控", "售后", "保修", "质量", "耐用", "返修", "维修", "坏", "修"]),
    ("screen",    ["屏幕", "屏"]),
    ("keyboard",  ["键盘"]),
    ("noise",     ["噪音", "风扇"]),
    ("build",     ["做工"]),
    ("weight",    ["沉不沉", "重不重", "多重", "重量"]),
    ("battery",   ["续航", "电池"]),
    ("price",     ["多少钱", "保值", "二手", "价格"]),
    ("config",    ["什么配置", "配置怎么样", "参数", "接口"]),
    ("pros_cons", ["缺点", "短板", "毛病", "坑", "不足", "优点", "好处", "值得", "怎么样"]),
]


def topic_by_rule(text):
    """词表兜底：这句话在问哪一面。认不出返回 None（不猜）。"""
    t = text or ""
    for tp, kws in _TOPIC_KW:
        if any(k in t for k in kws):
            return tp
    return None


def fit_by_rule(text):
    """词表兜底：是不是在问"这台满足我的要求吗"。"""
    t = text or ""
    if not any(k in t for k in agent.FIT_KW):
        return False
    # 🔴 「它有什么缺点」里有"怎么样"变体，会误命中 FIT_KW ——
    #    但那是问细节（topic=pros_cons），不是问匹配。exclude 见 agent 1543 行注释。
    if any(k in t for k in ("缺点", "短板", "毛病", "坑", "不足")):
        return False
    if any(w in t for w in agent.DEMAND_WORDS):
        return False
    return True


def route(text, focus=None):
    """统一路由入口。返回 dict：

        {"intent": 常量, "topic": str|None, "fit": bool|None,
         "why": str, "model_ok": bool}

    顺序（这就是"模型为主，规则兜底"的字面实现）：
      1. 模型一次推理出 intent + topic + fit
      2. intent 过 classify() 的规则否决层（模型已知偏向的矫正，缓存共享）
      3. topic / fit 模型没给出（非法/缺失）→ 词表兜底
      4. 模型整体不可用 → intent 走纯规则，topic/fit 全靠词表
    """
    t = (text or "").strip()
    m = model_route(t, focus)

    # intent：走完整 classify（否决层在其中，model_route 已把这次推理缓存）
    intent, why = classify(t, focus)

    if not m["ok"]:
        # 模型不可用：纯规则降级。intent 的兜底 classify 已经做了，
        # topic/fit 这里兜。
        return {"intent": intent, "why": why + "（模型不可用，全规则降级）",
                "topic": topic_by_rule(t), "fit": fit_by_rule(t),
                "model_ok": False}

    topic = m["topic"] if m["topic"] else topic_by_rule(t)
    fit = m["fit"] if m["fit"] is not None else fit_by_rule(t)
    return {"intent": intent, "why": why, "topic": topic, "fit": fit,
            "model_ok": True}
