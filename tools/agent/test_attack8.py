# -*- coding: utf-8 -*-
"""第八轮：演示前的「真实用户视角」测试。

前七轮都在打机器（编数字、标签错、听歪）。这一轮换成站在用户对面看：
**一个真实的、懂点电脑的人，会不会觉得这答案有问题。**

实测 2026-11-11 演示前跑出来的两个问题，都不是"判据失灵"，是"判据根本没管这件事"：

  ① 用户「学设计的，预算 7000，要带去教室」
     → 首位推荐惠普战 66，而它自己的 cons 写着「屏幕只有 62.5% sRGB，色域是明显短板」
     数据早就标了这是短板，排序只按 perf 和价格排，把设计专业最要命的项当没看见。
     ⇒ 新增 core.purpose_blockers()

  ② 回答里冒出「小米笔记本的售后体系不如手机」「别指望替代专业设计工作站」
     check_scope 只在**入口**校验用户输入，回答这一侧完全没管；
     check_outside_products 只筛"库外机型名"，筛不到"手机/工作站"这种品类。
     ⇒ 新增 agent._out_scope_product() + 出口 check_scope()

这两个都是**演示时会被一眼看穿**的：① 是专业错，② 是越界。
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent
import core

OK = 0
BAD = 0
FAILS = []


def ck(name, cond, note=""):
    global OK, BAD
    if cond:
        OK += 1
        print("  [OK ] %s" % name)
    else:
        BAD += 1
        FAILS.append((name, note))
        print("  [FAIL] %s   %s" % (name, note))


D = core.load("jd")

print("=" * 70)
print("第八轮：演示前真实用户视角")
print("=" * 70)

# ---------------------------------------------------------------- 1
print("\n[1] 设计专业不能推低色域屏（按数据明写的短板判）")

r = core.build_recommendation({"cat": "thin", "budget": 7000,
                               "purpose": ["design"], "carry": True})
top = r["ranked"][0]
ck("设计中预算 7000 首位不是低色域机型",
   not (top.get("blockers") or []),
   "首位是 %s，硬伤 %s" % (top["name"], top.get("blockers")))

# 那两台已知低色域的，必须被判出来
zhan66 = core.get_offer("thin-zhan66")
ck("惠普战66 在设计用途下被判硬伤",
   bool(core.purpose_blockers(zhan66, ["design"])),
   "cons 里明写「屏幕只有 62.5% sRGB，色域是明显短板」")

air15 = [o for o in D["offers"] if "Air15" in o["name"]]
ck("小新Air15 在设计用途下被判硬伤",
   bool(air15) and bool(core.purpose_blockers(air15[0], ["design"])),
   "cons 里明写「低色域屏，修图看片颜色发灰」")

# ---------------------------------------------------------------- 2
print("\n[2] 最关键的一条：「没查」不能判成「有问题」")

# thin 池里 10 台，只有 2 台写了屏幕短板。其余 8 台是**没查**。
blocked = [o["name"] for o in D["offers"]
           if o["cat"] == "thin" and core.purpose_blockers(o, ["design"])]
ck("设计用途下只判 2 台硬伤（不是把 8 台没查的也判了）",
   len(blocked) == 2, "实际 %d 台：%s" % (len(blocked), blocked))

# 「没有色域数据」的机型，blockers 必须是空列表，不能是"未知"或被扣分
no_data = [o for o in D["offers"] if o["cat"] == "thin"
           and not any(w in " ".join((o.get("pros") or []) + (o.get("cons") or []))
                       for w in ("色域", "sRGB", "色准"))]
ck("无色域数据的机型不被判硬伤",
   all(not core.purpose_blockers(o, ["design"]) for o in no_data),
   "无色域数据 %d 台" % len(no_data))

# ---------------------------------------------------------------- 3
print("\n[3] 硬伤判据不能外溢到别的用途")

for pu, why in [("office", "办公不看色域"), ("dev", "写代码不看色域"),
                ("daily", "日常不看色域")]:
    hits = [o["name"] for o in D["offers"]
            if o["cat"] == "thin" and core.purpose_blockers(o, [pu])]
    ck("%s 用途下零硬伤（%s）" % (pu, why), len(hits) == 0, str(hits))

# 游戏用途：**不能**因为 spec 里没写 RTX 就判"没独显"。
# 这是一个刻意的克制，不是漏判 —— 实测 2026-11-11：
#   thin 池里 10 台，spec 全都没写独显型号，但这只是"没写"，不是"写明没有"。
#   京东这类页面漏标显卡是常事（核显机型往往干脆不写）。
#   如果按"没写=没有"判，10 台全被判硬伤 → 等于说"轻薄本都不能打游戏"，
#   这是用缺失数据下结论，和「没色域数据就说色域差」是同一个错误。
# 所以 BLOCKER_RULES["gaming"] 只认**文案里明说的**「没有独立显卡/纯核显」。
gaming_thin = [o for o in D["offers"] if o["cat"] == "thin"
               and core.purpose_blockers(o, ["gaming"])]
ck("游戏用途下不按「没写独显」判硬伤（缺数据≠没有）",
   len(gaming_thin) == 0,
   "判了 %d 台。数据里没有一台明写「无独显」，所以正确的行为就是 0" % len(gaming_thin))

# 但如果文案真的明写了，必须判出来 —— 用构造数据验判据本身有效
fake_no_dgpu = {"name": "测试机", "cat": "thin", "spec": "U5 · 16G + 512G",
                "pros": ["轻"], "cons": ["没有独立显卡，纯核显"], "caution": ""}
ck("文案明写「没有独立显卡」时能判出来（判据本身有效）",
   bool(core.purpose_blockers(fake_no_dgpu, ["gaming"])),
   "构造数据，验证判据不是死的")

# ---------------------------------------------------------------- 3b
print("\n[3b] 用途和品类打架时，必须把冲突说出来（2026-11-12 用户实测）")

# 用户原话：「我说了设计和打游戏，他依旧给我推荐 matebook，还说这台不适合打游戏」。
# 根因：cat 停在 thin（第一轮"学设计"定的），后来加的 gaming 只进了 purpose，
# 内核照旧在 thin 里排，零通警告 —— 一边推它，一边在追问里承认它打不了游戏。
_r_gaming_thin = core.build_recommendation(
    {"cat": "thin", "budget": 7000, "purpose": ["design", "gaming"]})
ck("7000 的轻薄本 + 要打游戏 → 必须标记「这个品类里没有能打的」",
   bool(_r_gaming_thin.get("noGamingInCat")), "没标记，用户会以为推的这台能打游戏")

_r_design_only = core.build_recommendation(
    {"cat": "thin", "budget": 7000, "purpose": ["design"]})
ck("对照·只要设计 → 不许误报冲突",
   not _r_design_only.get("noGamingInCat"), "没打游戏却报了冲突")

# 有钱能买到带独显的轻薄本时，不该说"这个品类没有"，而该让独显那台排前面
_r_rich = core.build_recommendation(
    {"cat": "thin", "budget": 15000, "purpose": ["design", "gaming"]})
ck("对照·预算够得着独显机时不报冲突",
   not _r_rich.get("noGamingInCat"), "有解却报了无解")
ck("预算够时带独显的排第一（不是只靠说话，排序也要跟着变）",
   bool(_r_rich["ranked"]) and core.has_dgpu(
       core.get_offer(_r_rich["ranked"][0]["id"]) or {}),
   "首位：%s" % (_r_rich["ranked"][0]["name"] if _r_rich["ranked"] else "无"))

# 采集池明标核显（Intel Arc 140T）→ 判据要能判出来，但**不许**因此改变排序：
# 人工层 22 台没有 gpu 字段，按"已知核显"降权等于惩罚数据更全的机型。
_mb14 = core.get_offer("add-matebook14-2026")
_b = core.purpose_blockers(_mb14, ["gaming"], gpu_sort=False)
ck("采集标注 integrated 时能判成打游戏硬伤",
   bool(_b) and _b[0]["level"] == "hard" and "Arc 140T" in _b[0]["why"],
   str(_b))
ck("这类硬伤只陈述、不排序（sort=False）",
   bool(_b) and _b[0].get("sort") is False, "没有 sort 标记，会拿缺失数据占便宜")

# 对照：人工层没写显卡字段的机器，不许被推断成"没有独显"
_x16 = core.get_offer("thin-xiaoxin16")
ck("对照·人工层没写显卡 → 不许判成核显硬伤",
   not any(x.get("purpose") == "gaming"
           for x in core.purpose_blockers(_x16, ["gaming"])),
   "把缺数据当成了没独显")

# ---------------------------------------------------------------- 3c
print("\n[3c] 专业要求表要真的接进推荐（2026-11-12 用户实测）")

# 用户原话：「我是人工智能专业的大一新生，预算7000，给我推荐一台笔记本」
# → 系统只抽 purpose=dev，按跑分和价格推了 MateBook 14（16G / 核显 / 不可加装）。
# majors.json 里这一组写着：16G 底线、32G 推荐，hardGate 是 upgrade>=2。
_g, _m = core.match_major("我是人工智能专业的大一新生，预算7000")
ck("认出「人工智能」属于编程数据组", bool(_g) and _g.get("id") == "code", str(_g and _g.get("id")))
ck("认出规范专业名", _m == "人工智能", str(_m))
ck("对照·不认专业名时不硬猜",
   core.match_major("预算 7000 想买个笔记本")[0] is None, "把普通句子认成了专业")

_slots = {"cat": "thin", "budget": 7000, "purpose": ["dev"],
          "_majorId": "code", "_majorName": "人工智能",
          "_majorGroup": "编程数据", "_minRam": 16, "_minUpgrade": 2}
_r_ai = core.build_recommendation(_slots)
_first = _r_ai["ranked"][0]
ck("推荐结果满足本组 hardGate（内存可加装）",
   int(_first.get("upgrade") or 0) >= 2,
   "推了 upgrade=%s 的 %s" % (_first.get("upgrade"), _first["name"]))
ck("推荐结果满足内存底线 16G", int(_first.get("ram") or 0) >= 16,
   "推了 %sG 的 %s" % (_first.get("ram"), _first["name"]))
ck("不会再把 16G/焊死内存的 MateBook 推给人工智能新生",
   _first["name"] != "MateBook 14 2026", "首位：%s" % _first["name"])

# 对照：同一句话，没有专业约束时结果可能不同 —— 说明上面那条不是因为别的规则碰巧成立
_r_plain = core.build_recommendation({"cat": "thin", "budget": 7000, "purpose": ["dev"]})
ck("对照·不加专业约束时排序确实不同（证明是这条在起作用）",
   _r_plain["ranked"][0]["name"] != _first["name"],
   "两种情况下首位都是 %s" % _first["name"])

# ---------------------------------------------------------------- 3d
print("\n[3d] 小数价格不许被误判成「数字不可追溯」（乱测夹具抓到）")

# 2026-11-12：¥4240.65 被 verify_answer 判成不可追溯 ——
# numbers_in() 抽整数（"4240.65"→4240），allowed 却按 round 记成 4241。
# 后果：一条如实报数的回答被自己人毙掉，白重写一次，还可能落进兜底模板。
_fake = {"ranked": [{"id": "x", "name": "测试机", "price": 4240.65,
                     "spec": "", "subsidy": {}, "pros": [], "cons": [],
                     "tag": "", "caution": "", "brandNote": ""}],
         "warnings": [], "dataAsOf": "2026-11-02"}
ck("允许集合里同时有 4240 和 4241",
   4240 in agent.allowed_numbers(_fake) and 4241 in agent.allowed_numbers(_fake),
   str(sorted(agent.allowed_numbers(_fake))[:6]))
ck("如实报「到手 ¥4240.65」必须放行",
   agent.verify_answer("到手 ¥4240.65", _fake) == [],
   "被判成：%s" % agent.verify_answer("到手 ¥4240.65", _fake))
ck("对照·真编的数字仍然要抓",
   agent.verify_answer("到手 ¥3999", _fake) == [3999],
   "3999 不在数据里，必须被抓")

# ---------------------------------------------------------------- 4
print("\n[4] 硬伤不删机型，只靠后 —— 预算只够它时仍要出现")

# 预算 5100：低色域的机器买得起，高色域的 6799 超预算。
# 这时不能因为硬伤就把唯一的选项删掉（那是替用户决定"宁可不买"）。
r2 = core.build_recommendation({"cat": "thin", "budget": 5100,
                                "purpose": ["design"]})
names = [o["name"] for o in r2["ranked"]]
ck("预算紧张时低色域机型仍在候选里（没被删掉）",
   len(names) > 0, "候选 %s" % names[:3])

# 但 blockers 必须透传出去，让上层能如实说
has_blocker_field = all("blockers" in o for o in r2["ranked"])
ck("每台都带 blockers 字段（上层据此如实告知）", has_blocker_field)

# 排序上，有硬伤的必须排在没硬伤之后（同预算内）
#
# ⚠️ 2026-11-11 判据升级：blockers 现在分 hard / soft 两级。
#   hard = 数据明说这块不行（"色域是明显短板"）→ 参与降权
#   soft = 这块不算突出但能用（"屏幕素质在同价位不算突出"）→ **不降权**
# 所以这里只能数 hard。原来的写法 `1 if blockers else 0` 把 soft 也算进去了，
# 于是「屏幕素质不算突出」的小新 16 被当成"有硬伤"，序列变成 [0,1,0] 误报失败。
# 这不是判据错了，是测试预期要跟着判据一起升级 —— 两边必须用同一个口径。
r3 = core.build_recommendation({"cat": "thin", "budget": 7000,
                                "purpose": ["design"]})
seq = [1 if any(b.get("level") == "hard" for b in (o.get("blockers") or [])) else 0
       for o in r3["ranked"]]
ck("有硬伤的都排在无硬伤之后",
   seq == sorted(seq), "序列 %s" % seq)

# ---------------------------------------------------------------- 5
print("\n[5] 回答里不许出现库外品类（出口校验）")

MUST_BLOCK = [
    ("小米笔记本的售后体系不如手机", "手机"),
    ("别指望它能完全替代专业设计工作站", "工作站"),
    ("续航比 iPad 强不少", "iPad"),
    ("跑 PS5 那些游戏别想了", "PS5"),
    ("不如手机划算", "手机"),
]
for t, want in MUST_BLOCK:
    got = agent._out_scope_product(t)
    ck("拦住库外品类：%s" % t[:22], bool(got), "命中 %s" % got)

# 正当修辞要放行 —— 「比手机还便宜」是在说价格，不是在介绍手机
MUST_PASS = [
    "这价格比手机还便宜",
    "不是手机那种定位",
    "比平板还轻",
    "这机器比工作站便宜",
    "比手机薄",
]
for t in MUST_PASS:
    ck("放行正当修辞：%s" % t, not agent._out_scope_product(t),
       "被拦成 %s" % agent._out_scope_product(t))

# ---------------------------------------------------------------- 6
print("\n[6] 出口校验必须真的接在回答链路上（不是只写了个函数）")

# 直接给一个含库外品类的回答，走 _answer_current 的校验，必须被打回
o = core.get_offer("thin-redmibookpro14")
ck("_answer_current 的校验包含出口范围检查",
   "check_scope(rep)" in io.open(agent.__file__, encoding="utf-8").read(),
   "函数写了但没接上，等于没写")

# ---------------------------------------------------------------- 7
print("\n[7] 设计专业端到端：首位推荐必须能说出屏幕好在哪")

r4 = core.build_recommendation({"cat": "thin", "budget": 7000,
                                "purpose": ["design"], "carry": True})
top4 = r4["ranked"][0]
blob = " ".join((top4.get("pros") or []) + (top4.get("cons") or [])
                + [top4.get("caution") or "", top4.get("spec") or ""])


def _has_screen_good(b):
    """blob 里有没有**屏幕层面的硬依据**（分辨率 / 面板 / 色域 / 色准这些）。

    ⚠️ 2026-11-12 扩词表：原来只认 ("3.2K", "色准", "2.5K", "屏幕") ——
      那是 22 台时代的写法，当时第一名靠人工 pros 里的「色准」过。
      v0.4.1 池子扩到 97 台后第一名换成采集池的 MateBook 14 2026，
      它的屏幕依据是 spec 里的 `2880×1920 OLED 100% DCI-P3` ——
      是真参数，一个评价词都没有，于是被判失败。
      这不是"没依据"，是**词表没覆盖参数写法**。
    """
    if any(w in b for w in ("3.2K", "色准", "2.5K", "2.8K", "屏幕")):
        return True
    if "OLED" in b or "MiniLED" in b or "IPS" in b:
        return True
    if "DCI-P3" in b or "sRGB" in b:
        return True
    import re as _re
    return bool(_re.search(r"\d{3,4}\s*[×xX]\s*\d{3,4}", b))


has_screen_good = _has_screen_good(blob)
ck("首位推荐有屏幕相关的正面依据（不是靠猜）",
   has_screen_good, "%s 的依据：%s" % (top4["name"], blob[:80]))

# 🔴 分辨力对照：判据不许对"没有屏幕依据"的 blob 也放行
ck("对照·词表不许把只有 CPU+内存的 blob 判成有屏幕依据",
   not _has_screen_good("Ultra 9 275HX · 32G + 1T · RTX 5080"),
   "这是纯 CPU+内存+显卡，没有屏幕依据，词表却放行了")
ck("对照·词表不许把空 blob 判成有屏幕依据",
   not _has_screen_good(""), "空字符串被放行了")

# ---------------------------------------------------------------- 8
print("\n[8] 不能把「另一台更值得买」说成这台的坑（等于偷换推荐对象）")

# 数据里的 caution 可能混入"XX 同价位更值得看"，那是采集时顺手记的对比，
# 不是这台的缺点。模型照搬 + 加个"要提醒的坑是："就变成了偷偷主推别家。
# 实测原文：小新 16 的 caution = 「小新 16SE 有双 M.2 硬盘位，同价位更值得看」
fake_res = {"ranked": [{"name": "联想小新 16"}]}

PITCH_BLOCK = [
    "要提醒的坑是：小新 16SE 有双 M.2 硬盘位，同价位更值得看",
    "同价位的 ThinkBook 14 更值得考虑",
    "另一款 16SE 更划算",
    # 2026-11-12 补：当前机型名是「联想小新 16」，而「联想小新 16SE」以它开头。
    # 旧判据用 `cur in seg` 判"在说它自己"，子串命中就放行 —— 等于让 16SE 冒名 16。
    "联想小新 16SE 更值得看",
]
for t in PITCH_BLOCK:
    ck("拦住偷换推荐对象：%s" % t[:24], agent._other_model_pitch(t, fake_res),
       "整句：%s" % t)

# 说它自己的时候必须放行
PITCH_PASS = [
    "联想小新 16 同价位更值得看",
    "这台联想小新 16 的屏幕素质不算突出",
    "这款更划算，性价比高",
]
for t in PITCH_PASS:
    ck("放行说它自己：%s" % t, not agent._other_model_pitch(t, fake_res),
       "被拦成主推")

# 没有榜首数据时不能瞎判
ck("没有 ranked 时不判（避免空数据误伤）",
   not agent._other_model_pitch("小新 16SE 更值得看", {"ranked": []}))

# ---------------------------------------------------------------- 9
print("\n[9] 同名品牌不能串台（PC 的备注不能取成手机版）")

# 实测 2026-11-11 演示前的真 bug：
#   `brands` 里华为/红米/荣耀/苹果各出现两次，一次 scope="pc"、一次 scope="phone"。
#   原来写成 {x["name"]: x for x in brands} 的字典推导 —— 手机版覆盖了 PC 版。
#   后果：问「华为 MateBook D16 怎么样」，回答里冒出「Mate 90 Pro 同配置比上代高 1500」。
#   说得头头是道，但说的不是这台东西 —— 演示时最尴尬的一类。
dup = {}
for b in D.get("brands") or []:
    dup.setdefault(b.get("name"), []).append(b.get("scope"))
dups = {k: v for k, v in dup.items() if len(v) > 1}
ck("数据里确实存在同名品牌（说明这条判据不是空测）", bool(dups), str(dups))

for o in D["offers"]:
    if o["cat"] not in core.ALLOWED_CATS:
        continue
    b = core.brand_info(o)
    if not b:
        continue
    ck("品牌信息取的是 pc 版：%s（%s）" % (o["name"], b.get("name")),
       b.get("scope") == "pc",
       "取到了 scope=%s 的版本" % b.get("scope"))

# 手机型号不能出现在品牌备注里
PHONE_MODEL = ("Mate 90", "K90", "iPhone", "Reno", "Neo8", "Turbo6", "X500", "17 Ultra")
leak = []
for o in D["offers"]:
    if o["cat"] not in core.ALLOWED_CATS:
        continue
    note = core.brand_info(o).get("note") or ""
    if any(p in note for p in PHONE_MODEL):
        leak.append((o["name"], note[:40]))
ck("电脑机型的品牌备注里不含手机型号", not leak, str(leak[:3]))

# ---------------------------------------------------------------- 10
print("\n[10] 品牌备注里的别的机型，不能当本机信息说出去")

# 联想品牌 note 原文：「同一个品牌里，E14 能加内存、X1C 不能 —— 别按品牌判断，
#   按型号判断。小新 16SE 的双 M.2 硬盘位是今年的卖点。」
# 后一句在说另一台，但混在品牌备注里，兜底模板会把整段念出来。
xl = core.get_offer("thin-xiaoxin16")
xl_brief = core.brief(xl)
cleaned = agent._clean_brand_note(xl_brief.get("brandNote"), xl_brief)
ck("清掉「16SE 硬盘位」这种别的机型的配置",
   "16SE" not in cleaned and "M.2" not in cleaned, "过滤后：%s" % cleaned)
# 但方法论要留下 —— 这是真正对用户有用的部分
ck("保留「按型号判断」这种通用方法论",
   "型号" in cleaned, "过滤后：%s" % cleaned)

print("\n" + "=" * 70)
print("通过 %d / %d" % (OK, OK + BAD))
if FAILS:
    print("\n失败明细：")
    for n, note in FAILS:
        print("  - %s   %s" % (n, note))
else:
    print("全绿。")
print("=" * 70)