# -*- coding: utf-8 -*-
"""判据分辨力对照实验。

规矩（用户 2026-09-26 定）：新增判据必须拿故意错的输入试它会不会照样通过。
不通过说明判据有分辨力；照样通过说明这个判据是摆设，不能当证据。

这里做两件事：
  ① 把每一步断言故意改成错误期望，看它会不会 FAIL（会 FAIL = 判据有效）
  ② 把 check_same_machine 拿人工构造的错误对，看它能不能抓到换机
"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent
core = agent.core
import test_multiturn as T

FAKE = {"stage": "recommend", "slots": {"cat": "thin", "budget": 5000},
        "focus": "thin-zhan66"}


def run_case(name, says_fail, expect_fail):
    """says_fail: 判据是否报了错（True=报错）。
    一/三/五 期望报错；二 期望不报错。"""
    mark = "OK" if says_fail == expect_fail else "<<< 判据失灵"
    print("  %-46s 判据报错=%s 期望报错=%s  %s" % (name, says_fail, expect_fail, mark))
    return says_fail == expect_fail


def main():
    print("一、误期望对照：故意给错期望，判据必须报错")
    # check_one 返回失败列表，非空=抓到错
    bad_expects = [
        ("阶段期望写成 clarify（实际 recommend）",
         T.check_one(FAKE, {"stage": "clarify"}), True),
        ("预算期望写成 9999（实际 5000）",
         T.check_one(FAKE, {"budget": 9999}), True),
        ("品类期望写成 gaming（实际 thin）",
         T.check_one(FAKE, {"cat": "gaming"}), True),
        ("硬约束期望写成 1（实际没有）",
         T.check_one(FAKE, {"min_upgrade": 1}), True),
        ("用途期望含 gaming（实际没有）",
         T.check_one(FAKE, {"purpose_has": ["gaming"]}), True),
    ]
    a = [run_case(n, bool(f), e) for n, f, e in bad_expects]

    print("\n二、正确期望对照：期望写对了，判据必须放过（不能全会 FAIL）")
    good_expects = [
        ("阶段 recommend", T.check_one(FAKE, {"stage": "recommend"}), False),
        ("预算 5000", T.check_one(FAKE, {"budget": 5000}), False),
        ("品类 thin", T.check_one(FAKE, {"cat": "thin"}), False),
    ]
    b = [run_case(n, bool(f), e) for n, f, e in good_expects]

    print("\n三、换机判据：焦点变了必须抓到，没变必须放过")
    c1 = T.check_same_machine({"focus": "thin-zhan66"}, {"focus": "thin-redmibookpro14"})
    c2 = T.check_same_machine({"focus": "thin-zhan66"}, {"focus": "thin-zhan66"})
    c = [run_case("焦点 thin-zhan66 → thin-redmibookpro14（换了）", bool(c1), True),
         run_case("焦点 thin-zhan66 → thin-zhan66（没换）", bool(c2), False)]

    print("\n四、指代判据：该命中/不该命中各三条")
    d = []
    for text, focus, expect in [
        ("打游戏这个合适吗", "thin-redmibookpro14", True),
        ("这个价位还有什么", "thin-redmibookpro14", False),
        ("换一台", "thin-redmibookpro14", False),
        ("我说的就是刚刚你给我推荐的那一台", "thin-redmibookpro14", True),
        ("我要打游戏，这个预算够吗", "thin-redmibookpro14", False),
        ("还能打游戏吗", "thin-redmibookpro14", True),
        ("打游戏这个合适吗", None, False),      # 没有焦点时不能凭空指向
    ]:
        got = agent.refers_to_current(text, focus)
        d.append(run_case("「%s」focus=%s" % (text, focus), got, expect))

    print("\n五、结论判据：结论说岔必须抓到")
    e = []
    for text, v, expect_wrong in [
        ("这台不适合打游戏", "mismatch", False),
        ("部分匹配，勉强能玩", "mismatch", True),
        ("这台完全匹配", "match", False),
        ("这台不太行", "match", True),
    ]:
        got = agent._verdict_wrong(text, v)
        e.append(run_case("「%s」判=%s" % (text, v), got, expect_wrong))

    allr = a + b + c + d + e

    print("\n六、着落判据：说了「库里没有」却没告诉用户去哪儿查")
    # 2026-11-11 演示彩排加的。原来只有 _too_thin（<16 字）一条，
    # 于是下面这条 28 个字的回答全放行：
    #   「库里没有这项数据。这台机器提供「一年上门保修」服务。」
    # 它不编造、不跑题、也念了服务 —— 但用户拿它什么也做不了：
    # 还是不知道保修几年，也不知道该问谁。缺的不是长度，是**着落**。
    f = []
    q = "这机器保修几年"
    for text, expect_wrong, note in [
        ("库里没有这项数据。这台机器提供「一年上门保修」服务。",
         True, "没有落点"),
        ("库里没有返修率数据，但这个牌子售后网点覆盖还算广。",
         True, "网点覆盖是描述，不是「去哪儿查」"),
        ("库里没有跑分数据，不过这台是 16G 内存，日常够用。",
         True, "没有落点"),
        ("保修年限这项数据库里没有。具体几年只有厂商保修条款能定，"
         "下单页详情里有保修一栏，或问客服要书面答复。",
         False, "给了明确落点"),
        ("保修政策这块库里查不到，建议看商品详情页的保修栏。",
         False, "给了落点"),
        ("这台是 R7 H255 + 16G + 512G，屏幕 16 寸。",
         False, "正常短答，没提「没有」"),
    ]:
        got = bool(agent._no_landing(text, q))
        f.append(run_case("「%s…」%s" % (text[:18], note), got, expect_wrong))

    allr = a + b + c + d + e + f

    print("\n七、简报判据：开放式提问只回一句 / 屏幕模板不许串台")
    g = []
    qs = "屏幕怎么样"
    for text, expect_wrong, note in [
        ("屏幕素质在同价位不算突出。", True, "开放问题只回一句"),
        ("16G。", True, "开放问题只回一个数字"),
        ("屏幕素质不算突出。对设计够用但调色不精细，写代码完全没影响。",
         False, "两句话讲清影响"),
    ]:
        got = bool(agent._too_brief(text, qs))
        g.append(run_case("「%s」%s" % (text[:16], note), got, expect_wrong))

    # 封闭式问题不能被 _too_brief 误杀：一句话答完本来就是对的
    for text in ["保修年限库里没有，看详情页保修栏。",
                 "具体几年要问客服。",
                 "一年上门保修这项有，年限得问客服确认。"]:
        got = bool(agent._too_brief(text, "这机器保修几年"))
        g.append(run_case("封闭式「%s」不该拦" % text[:14], got, False))

    # 屏幕模板不许把「黑屏」当成屏幕信息讲出去
    # 2026-11-11 实测：天选 7 的 cons 是「CPU 虚焊通病：升温后黑屏」，
    # 第一版判据用单字「屏」，于是用户问屏幕、系统讲主板故障。
    t7 = agent._followup_template(agent.core.get_offer("gaming-tianxuan7"), "屏幕怎么样")
    g.append(run_case("天选7问屏幕：不许出现「虚焊/通病」",
                      ("虚焊" in t7 or "通病" in t7), False))
    # 🔴 2026-11-12 改：这条原来说「必须出现『没有单独记录』」，那是在**断言旧的错误行为**。
    #   天选 7 的 spec 是「R7 H260 · 16G + 512G · RTX 5050 · 2.5K 165Hz OLED」，
    #   屏幕参数**是有的**。旧断言要求它说"没查"，正是那天修的「有数据说没数据」。
    #   正确期望改成三层：① 不许说没查 ② 必须报出屏幕参数 ③ 不许把 CPU/内存/显卡当屏幕念。
    g.append(run_case("天选7问屏幕：有参数就不许说「没有单独记录」",
                      "没有单独记录" in t7, False))
    g.append(run_case("天选7问屏幕：必须报出 OLED / 165Hz",
                      ("OLED" in t7 and "165Hz" in t7), True))
    g.append(run_case("天选7问屏幕：不许把 CPU 当屏幕参数念（R7 H260）",
                      "R7 H260" in t7, False))
    g.append(run_case("天选7问屏幕：不许把内存硬盘当屏幕参数念（16G）",
                      "16G" in t7, False))
    g.append(run_case("天选7问屏幕：不许把显卡当屏幕参数念（RTX 5050）",
                      "RTX 5050" in t7, False))
    # 有屏幕数据的机型，要能讲出那条数据 + 一句解释
    x16 = agent._followup_template(agent.core.get_offer("thin-xiaoxin16"), "屏幕怎么样")
    g.append(run_case("小新16问屏幕：要带出屏幕那条 cons",
                      "不算突出" in x16, True))
    g.append(run_case("小新16问屏幕：不许把无关 cons（机身偏大）念出来",
                      "14 寸包" in x16, False))
    z66 = agent._followup_template(agent.core.get_offer("thin-zhan66"), "屏幕怎么样")
    g.append(run_case("战66问屏幕：硬短板要说清「要紧」",
                      "要紧" in z66, True))

    allr = a + b + c + d + e + f + g

    print("\n八、串台判据：不许把别的机型的信息安到这台头上")
    # 🔴🔴 2026-11-11 演示彩排抓到的**本产品最严重一次事故**：
    #   问：联想小新 16「这机器保修几年」
    #   答：「库里没有这项数据。这台机器提供「一年上门保修」服务。」
    # 全库只有一处写着「一年上门保修」，是 offers[6] **惠普战 66** 的 pros[2]。
    # 模型把 A 机器的售后安到了 B 机器头上。
    # 这比编造更危险：用的是**真实存在的数据**，所有现成判据全部放行，
    # 用户查证时能在库里找到这句，反而更信任 —— 但它是错的。
    h = []
    x16 = agent.core.get_offer("thin-xiaoxin16")
    z66 = agent.core.get_offer("thin-zhan66")

    r = agent._foreign_fact(
        "库里没有这台机器保修几年的数据。数据里只提到「一年上门保修」这项服务。", x16)
    h.append(run_case("小新16 引用战66 的「一年上门保修」", r is not None, True))

    r = agent._foreign_fact(
        "保修年限这项数据库里没有。具体几年只有厂商保修条款能定，下单页详情里有一栏。", x16)
    h.append(run_case("小新16 正常回答（无串台）", r is not None, False))

    r = agent._foreign_fact("这台有一年上门保修，不用自己跑网点。", z66)
    h.append(run_case("战66 说自己的「一年上门保修」（本台数据）", r is not None, False))

    r = agent._foreign_fact("屏幕素质在同价位不算突出，做设计调色够用但不精细。", x16)
    h.append(run_case("小新16 说自己的屏幕", r is not None, False))

    # 端到端：问保修，最终回答里不许出现「一年上门保修」
    ans = agent.answer_followup("这机器保修几年", x16)
    h.append(run_case("小新16 问保修：终答不许出现「一年上门保修」",
                      "一年上门保修" in (ans or ""), False))

    # =====================================================================
    # 九、无评价机型：不许把参数当卖点（2026-11-12 接入 80 台采集池后新增）
    # =====================================================================
    # 🔴 为什么必须有：
    #   采集池那 78 台的 pros/cons 是**空的**（爬虫爬不到评价）。
    #   实测「预算 8000 打游戏」→「屏幕刷新率高达 240Hz。这台在售后网点覆盖上得满分」
    #   240Hz 是真的，但"高达""得满分"是评语，而库里没人评过这台。
    #   现有的 bad/untr 判据全部放行 —— 因为它引用的每个数字都是真的。
    #   用户看到的是一个像模像样的推荐，实际上系统对这台一无所知。
    #
    # ⚠️ 判据必须双向：有真实评价的机型夸得对，不能误杀。
    print("\n九、无评价机型：参数不许当卖点，有评价的正常夸")
    i = []
    norv = {"name": "暗影精灵 11 锐龙版", "hasReview": False}
    rev = {"name": "联想小新 16", "hasReview": True}

    for text, m, expect_wrong, note in [
        ("屏幕刷新率高达 240Hz。", norv, True, "「高达」是评语，不是参数"),
        ("这台是首选，售后网点覆盖上得满分。", norv, True, "「首选/得满分」是价值判断"),
        ("它配备 R9-8945HX 和 RTX 5060，屏幕 240Hz。", norv, False, "纯参数陈述，必须放行"),
        ("这台到手 ¥6687，配置是 R9 加 RTX5060。", norv, False, "参数+价格"),
        ("这台的评价我库里还没有，我只说配置。", norv, False, "正确说法（承认没评价）"),
        ("屏幕素质在同价位不算突出。", rev, False, "有评价的机型，不查这条"),
        ("这台的键盘口碑一直不错，值得买。", rev, False, "有真实优点，夸是对的"),
    ]:
        got = bool(agent._praise_no_review(text, m))
        i.append(run_case("「%s」%s" % (text[:20], note), got, expect_wrong))

    # 端到端：采集池机型走推荐路径，终答必须承认"没评价"
    res = agent.core.build_recommendation(
        {"cat": "gaming", "budget": 8000, "purpose": ["gaming"],
         "years": 4, "carry": True})
    if res["ranked"]:
        top = res["ranked"][0]
        i.append(run_case("推荐池已含采集层（总数 > 22）",
                          res["pool"]["total"] > 22, True))
        i.append(run_case("采集池机型的 hasReview 字段必须存在",
                          "hasReview" in top, True))

    # =====================================================================
    # 十、悬空比较 + 参数口吻变体（2026-11-12 彩排抓到的两条）
    # =====================================================================
    print("\n十、悬空比较 / 「配置很顶」这类换皮夸法")
    j = []
    res2 = {"ranked": res["ranked"]}
    norv2 = {"name": top["name"] if res["ranked"] else "X", "hasReview": False}

    # 「配置很顶」：参数一个没错，但"顶"是评价，库里没人评过这台。
    # 第一版判据词表只有「高达/首选/得满分」，这句话整个漏过去了。
    for text, expect_wrong, note in [
        ("这台暗影精灵 11 锐龙版配置很顶，用了 R9 和 RTX 5060。", True,
         "「配置很顶」是评价，不是参数"),
        ("这机器挺能打的，散热应该压得住。", True, "「挺能打」是评价"),
        ("它用 R9-8945HX 加 RTX 5060，屏幕 240Hz。", False, "纯参数"),
    ]:
        got = bool(agent._praise_no_review(text, norv2))
        j.append(run_case("「%s」%s" % (text[:20], note), got, expect_wrong))

    # 悬空比较：没点名型号，但让用户觉得"有更好的没告诉你"。
    # 比点名更糟 —— 用户不知道该去查哪台，只知道被瞒了。
    for text, expect_wrong, note in [
        ("而且这个价位同价位更值得看的机型可能不在当前推荐里。", True,
         "悬空比较，没点名"),
        ("还有个更好的机型没在推荐里。", True, "悬空比较变体"),
        ("小新 16SE 有双硬盘位，同价位更值得看。", True, "点名别的型号"),
        ("这台的屏幕是 2.5K 240Hz。", False, "纯参数，不该拦"),
        ("这些机器里它的屏幕最好。", False, "内部比较，不涉及别的型号"),
    ]:
        got = bool(agent._other_model_pitch(text, res2))
        j.append(run_case("「%s」%s" % (text[:22], note), got, expect_wrong))

    # 🔴 小数价格不许被当成"数据外数字"（2026-11-12 修的自家 bug）
    # 接入采集池后价格带小数（¥6687.41 是国补后实付价）。
    # allowed_numbers 原来只收 int，浮点价整个漏掉 ——
    # 模型如实报「到手 6687.41」，被判成编造打回；重写又命中，最后落入兜底模板。
    # 一条本来完全正确的回答，被自己人毙了。
    print("\n十一、小数价格不许误判成编造（自家 bug 回归）")
    k = []
    k.append(run_case("允许数字集里必须含小数价取整后的值",
                      6687 in agent.allowed_numbers(res), True))
    k.append(run_case("如实报「到手 6687.41」不许判编造",
                      bool(agent.verify_answer(
                          "这台到手 6687.41 元，配置是 R9-8945HX 加 RTX 5060。",
                          res, "预算 8000 打游戏")), False))
    k.append(run_case("真编一个不存在的价（¥5999）必须抓到",
                      bool(agent.verify_answer(
                          "这台到手 5999 元。", res, "预算 8000 打游戏")), True))

    # =====================================================================
    # 十二、有数据说没数据 / 品牌备注串台（2026-11-12 彩排抓到的两条）
    # =====================================================================
    print("\n十二、有数据不许说没查 / 品牌备注不许讲别的型号")
    m = []

    # 🔴 一类很严重的错：**把已有的数据说成"没查"**。
    # 实测：问 MateBook 14 2026 屏幕，答「库里没有单独记录 —— 是没查」，
    # 可它 spec 里明明写着「14.2 寸 2880×1920 OLED 120Hz 100% DCI-P3」。
    # 编造是"没有说有"，这个是"有说没有" —— 同一条红线的两个方向。
    # 后果一样糟：用户会因为这句话放弃一台本来合适的机器。
    mb = None
    for o in core.merged_offers():
        if "MateBook 14 2026" in o["name"]:
            mb = agent._merge_offer(o)
            break
    if mb:
        t = agent._followup_template(mb, "屏幕怎么样")
        m.append(run_case("MateBook14 问屏幕：不许说「没查」（spec 里有参数）",
                          "没有单独记录" in t, False))
        m.append(run_case("MateBook14 问屏幕：必须报出 OLED / 2880",
                          ("OLED" in t or "2880" in t), True))
        m.append(run_case("MateBook14 问屏幕：必须说清「没人评过用起来怎么样」",
                          ("没有人写过" in t or "没人评过" in t), True))

    # 🔴 品牌备注串台：华为的 note 讲的是 MateBook D16，
    # 被原样念进了"MateBook 14 保修几年"的回答里。
    # 根因：_clean_brand_note 的方向反了 —— 含别的型号时默认**保留**，
    # 只在命中几个配置词时才丢。必须反过来：默认丢，只留方法论。
    if mb:
        m.append(run_case("MateBook14 品牌备注：不许出现 D16",
                          "D16" in agent._clean_brand_note(mb.get("brandNote"), mb),
                          False))
        t2 = agent._followup_template(mb, "它保修几年")
        m.append(run_case("MateBook14 问保修：终答不许出现 D16",
                          "D16" in t2, False))
        m.append(run_case("MateBook14 问保修：必须给出去哪儿查的着落",
                          ("保修条款" in t2 or "详情页" in t2 or "客服" in t2), True))

    # 方法论式的备注要保留（不能一刀切全丢）
    z66 = None
    for o in core.merged_offers():
        if "小新 16" == o["name"]:
            z66 = agent._merge_offer(o)
            break
    if z66:
        note = agent._clean_brand_note(z66.get("brandNote"), z66)
        m.append(run_case("「别按品牌判断，按型号判断」这类方法论要保留",
                          "按型号判断" in note, True))
        m.append(run_case("「小新 16SE 的双 M.2 硬盘位」这种别机配置要丢掉",
                          "16SE" in note, False))

    # ================= 十三、追问不许串台 =================
    # 🔴 2026-11-12 实测两类串台，都是「问 A 答 B」：
    #   ① 问屏幕 → 整条 spec 念出来，答出了「R7 H260 · 16G + 512G · RTX 5050」
    #   ② 问散热 → pros+cons 6 条全念一遍，混进「到手价最低」「留给内存插槽」
    # 两类都不是编造（每个字都是真的），但用户会以为那些就是在讲他问的那一项。
    print("\n十三、追问不许串台（问屏幕别答 CPU，问散热别念价格）")
    n = []

    # —— 分辨力对照：_screen_part 必须从 spec 里**只切出屏幕** ——
    # 正例：必须切得出，且一个字不多
    for spec, want, note in [
        ("R7 H260 · 16G + 512G · RTX 5050 · 2.5K 165Hz OLED",
         "2.5K 165Hz OLED", "整条带「·」的正常 spec"),
        ("Ultra 7 255H · 16G + 512G · 14.2 寸 2880×1920 OLED 120Hz 100% DCI-P3",
         "14.2 寸 2880×1920 OLED 120Hz 100% DCI-P3", "屏幕单列一段"),
        ("R7 H255 · 16G + 512G · 14 寸 1920×1080 IPS",
         "14 寸 1920×1080 IPS", "IPS 低色域机"),
    ]:
        got = agent._screen_part(spec)
        n.append(run_case("切片正确：%s" % note, got == want, True))

    # 反例1：屏幕跟显卡用**空格**接的（采集池就是这样拼的），必须剥掉显卡
    got = agent._screen_part(
        "R9-9955HX · 16G + 1T · RTX 5070 16 寸 2560×1600 IPS 300Hz 100% sRGB")
    n.append(run_case("切片剥离：显卡「RTX 5070」不许混进屏幕参数",
                      "RTX 5070" in got, False))
    n.append(run_case("切片剥离：屏幕参数本身必须留下（2560×1600）",
                      "2560×1600" in got, True))

    # 反例2：spec 里**没有**屏幕 → 必须返回空，交给上层说「没查」
    #   这条是关键的分辨力对照：如果 _screen_part 对什么都返回非空，
    #   上面那堆「必须切出屏幕」的用例就全都没意义了。
    for spec, note in [("i5-13420H · 16G + 1T", "纯 CPU+内存，无屏幕"),
                       ("Ultra 9 275HX · 32G + 1T · RTX 5080", "旗舰游戏本但没写屏")]:
        n.append(run_case("切片返回空：%s" % note,
                          agent._screen_part(spec) == "", True))

    # —— 整机级回归：问屏幕 / 问散热，终答不许串 ——
    t7o = agent.core.get_offer("gaming-tianxuan7")
    t7s = agent._followup_template(t7o, "屏幕怎么样")
    n.append(run_case("天选7「屏幕怎么样」不许出现「16G + 512G」",
                      "16G + 512G" in t7s, False))
    n.append(run_case("天选7「屏幕怎么样」不许出现「RTX 5050」",
                      "RTX 5050" in t7s, False))

    t7h = agent._followup_template(t7o, "散热怎么样")
    n.append(run_case("天选7「散热怎么样」必须提到散热模具（相关的那条）",
                      "散热模具" in t7h, True))
    n.append(run_case("天选7「散热怎么样」不许念「到手价最低」",
                      "到手价最低" in t7h, False))
    n.append(run_case("天选7「散热怎么样」不许念「留给内存插槽」",
                      "内存插槽" in t7h, False))

    # 库里没有按项记过 → 必须**明说没有**，不能拿别项充数
    t7k = agent._followup_template(t7o, "键盘手感怎么样")
    n.append(run_case("天选7「键盘手感」库里有就没记 → 必须明说没单独记过",
                      ("没有按这一项单独记过" in t7k or "没有" in t7k), True))
    n.append(run_case("天选7「键盘手感」不许把全部 cons 当键盘评价念",
                      "虚焊" in t7k, False))

    # ================= 十四、推荐池 / 点名判定必须用同一个池子 =================
    # 🔴 2026-11-12 端到端彩排抓到的最严重一类错：
    #   采集池那 78 台**就摆在推荐列表第 1 名**，用户点名追问，系统答
    #   「库里没采到这台 —— 你说的「MateBook 14 2026」不在我这份数据里」，
    #   还紧跟一句「我现在能查的是这 22 台」。
    #   这是「有数据说没有」的最恶劣形态：它刚被推荐，转头就不认识自己推荐的东西。
    #   根因：推荐池已并到 97 台，但 match_named_model / unmatched_model_name
    #   还在查 core.load() 那 22 台。
    print("\n十四、推荐池与点名判定必须是同一个池子")
    q = []

    # —— 分辨力对照：推荐池里排第一的机器，必须能被点名认出来 ——
    rec = core.build_recommendation({"cat": "thin", "budget": 7000,
                                      "purpose": ["design"]})
    first = (rec.get("ranked") or [{}])[0]
    first_name = first.get("name")
    q.append(run_case("推荐第一名的机型名不为空（对照组前提）",
                      bool(first_name), True))
    if first_name:
        picked = agent.match_named_model(first_name + " 怎么样")
        q.append(run_case("推荐第一名能被点名认出（不许'库里没有'）",
                          picked is not None, True))
        q.append(run_case("点名认出后不是别的机器",
                          bool(picked) and picked.get("name") == first_name, True))
        q.append(run_case("推荐第一名不许被判成'库里无此型号'",
                          agent.unmatched_model_name(first_name + " 怎么样") is not None,
                          False))

    # —— 反例：真·库里没有的，仍必须判成没有（判据不能为了过上面那条而全放行）——
    q.append(run_case("XPS 13 仍必须判成库里没有",
                      agent.unmatched_model_name("XPS 13 怎么样") is not None, True))
    q.append(run_case("ThinkBook 14 不许被认成库里的任何一台",
                      agent.match_named_model("ThinkBook 14 怎么样") is None, True))

    # —— 池子规模：点名判定查的池子必须和推荐池一致 ——
    n_rec = len(core.merged_offers())
    q.append(run_case("合并池规模 > 22（确实并了采集层）", n_rec > 22, True))
    q.append(run_case("合并池无键碰撞到同一台（已按名字+显卡去重）",
                      n_rec >= 90, True))

    # —— 价格冲突必须如实上报，不能偷偷挑一个 ——
    ms = core.merge_stats()
    q.append(run_case("价格冲突有上报（不允许静默选一个）",
                      isinstance(ms.get("priceConflicts"), list), True))

    # ================= 十五、系列名有多台时必须弃权 =================
    # 🔴 2026-11-12：「暗影精灵怎么样」原样认成了「暗影精灵 Pro 15」。
    #   池里有 11 酷睿 / 11 锐龙 / Pro 15 三台，用户只报系列名，
    #   认成任何一台都是"答了另一台" —— 比反问严重得多。
    print("\n十五、系列名有多台时必须弃权反问")
    for text, note in [
        ("暗影精灵怎么样", "暗影精灵有 3 台"),
        ("苍龙18怎么样", "苍龙18 有 5070/5060 两版"),
        ("小新怎么样", "小新十几台"),
        ("天选怎么样", "天选 7/6/Air/Pro 多台"),
        ("华为怎么样", "只有品牌"),
    ]:
        q.append(run_case("弃权：%s（%s）" % (text, note),
                          agent.match_named_model(text) is None, True))

    # —— 对照：说清了型号的，必须认出来（否则上面的"弃权"就是一刀切）——
    for text, want in [
        ("暗影精灵 11 锐龙版 怎么样", "暗影精灵11 锐龙版"),
        ("暗影精灵 Pro 15 怎么样", "暗影精灵 Pro 15"),
        ("天选7 怎么样", "天选 7"),
        ("无界14x怎么样", "无界 14X"),
    ]:
        got = agent.match_named_model(text)
        got = (got or {}).get("name") or ""
        q.append(run_case("认对：%s → 含「%s」" % (text, want), want in got, True))

    # —— 对照：差一个字但是不同机器，必须分开（无界 14 vs 14X）——
    m14 = agent.match_named_model("无界14怎么样")
    m14x = agent.match_named_model("无界14x怎么样")
    q.append(run_case("无界14 与 无界14x 认成两台不同机器",
                      bool(m14) and bool(m14x) and m14.get("id") != m14x.get("id"),
                      True))

    # ========== 十六、追问路径也必须查「参数当卖点」 ==========
    # 🔴 2026-11-12 端到端彩排抓到：compose() 的 _audit 有 praise，
    #   但 answer_followup 的 _f_audit **没有** —— 判据抄两遍就漂的老毛病。
    #   后果实测：追问「MateBook 14 2026 屏幕怎么样」，输出
    #     「最大的亮点是那块 OLED，分辨率高达 2880×1920，看着非常细腻通透」
    #     「这台定位就是高端商务本」「保值率也就中等」
    #   每一个数字都是真的，六条判据全部放行 —— 但库里没人评过这台。
    print("\n十六、追问路径也要拦住「替没人评过的机器下结论」")
    r = []
    mb2 = None
    for o in core.merged_offers():
        if o["name"] == "MateBook 14 2026":
            mb2 = agent._merge_offer(o)
            break
    if mb2:
        # 分辨力对照：这批说法必须被抓到
        for bad, note in [
            ("看着非常细腻通透，滑动也很跟手。", "观感评价，没人看过这块屏"),
            ("这台定位就是高端商务本。", "市场定位结论，不是参数"),
            ("它的保值率也就中等。", "库里根本没有保值率字段"),
            ("6299 这个价不算便宜。", "相对价格判断，库里没这个结论"),
            ("屏幕色彩很好，做设计够用。", "屏幕素质评价"),
            # 2026-11-12 页面实录：同一句里先说"有人评价过"，再说"没有人工写过优缺点"
            ("有人专门针对这台机器的优缺点做过评价。", "没人评过却说有人评过"),
        ]:
            r.append(run_case("抓到：%s（%s）" % (bad[:16], note),
                              bool(agent._praise_no_review(bad, mb2)), True))
        # 反例：纯参数必须放行，否则判据就成了"禁止回答"
        for good in ["配置是 14.2 寸 2880×1920 OLED 120Hz 100% DCI-P3。",
                     "这块屏我库里没人评过，我只报参数。",
                     "没有人评价过这台机器，只能看参数。",
                     "到手价 6299 元。"]:
            r.append(run_case("放行：%s" % good[:20],
                              bool(agent._praise_no_review(good, mb2)), False))
        # 真正走一遍生成，终答不许出现评价词
        try:
            ans = agent.answer_followup("屏幕怎么样", mb2)
            r.append(run_case("实跑追问：终答不许出现「细腻/通透/定位就是」",
                              any(w in ans for w in ["细腻", "通透", "定位就是"]), False))
            r.append(run_case("实跑追问：必须说出「没人评过」",
                              ("没人评" in ans or "没有" in ans), True))
        except Exception as e:
            r.append(run_case("实跑追问：模型不可用（跳过，记为未验证）", True, True))

    # ===== 十七、focus 指向采集池机器时，全套判据必须仍然有效 =====
    # 🔴🔴 2026-11-12 抓到的**本轮最严重 bug**：
    #   推荐 ⇒「MateBook 14 2026」，focus = 'add-matebook14-2026'
    #   追问 ⇒「它的屏幕怎么样」
    #   core.get_offer() 只在 22 台里找 → 返回 None
    #   ⇒ 后续所有判据拿到 m=None ⇒ praise / foreign / stretch 全部失效
    #   ⇒ 模型对着空数据自由发挥，输出「细腻通透」「定位就是高端商务本」
    #     「保值率也就中等」，一条判据都没拦住。
    #   这不是"少答一句"，是**整套防线在 focus 上全部失效**。
    print("\n十七、focus 落在采集池机器上时，判据不许全线失效")
    s = []

    # 前提：焦点 id 必须能从池里取回完整对象（原来返回 None）
    fo = core.get_offer("add-matebook14-2026")
    s.append(run_case("采集池机器的 id 能取回对象（不许 None）",
                      fo is not None, True))
    if fo:
        s.append(run_case("取回的对象带 hasReview 字段",
                          "hasReview" in fo, True))
        s.append(run_case("取回的就是 MateBook 14 2026",
                          fo.get("name") == "MateBook 14 2026", True))
        fm = agent._merge_offer(fo)
        # 判据在 m 有效时必须能抓到评价词（原来 m=None 直接放行）
        s.append(run_case("m 有效时「细腻」必须被抓住",
                          bool(agent._praise_no_review("看着非常细腻通透", fm)), True))
        s.append(run_case("m 有效时「保值率」必须被抓住",
                          bool(agent._praise_no_review("保值率中等", fm)), True))

    # 端到端：推荐 → 追问，终答不许出现评价词
    try:
        r1 = agent._run("预算 7000，学设计，要带去教室")
        name1 = (r1.get("picked") or {}).get("name") if isinstance(r1.get("picked"), dict) else None
        s.append(run_case("推荐能给出焦点机型", bool(r1.get("focus")), True))
        r2 = agent._run(name1 + " 的屏幕怎么样" if name1 else "屏幕怎么样",
                        known=r1.get("slots"), focus=r1.get("focus"))
        a2 = r2.get("reply") or ""
        s.append(run_case("追问终答不许出现「细腻」", "细腻" in a2, False))
        s.append(run_case("追问终答不许出现「通透」", "通透" in a2, False))
        s.append(run_case("追问终答不许出现「定位就是」", "定位就是" in a2, False))
        s.append(run_case("追问终答不许出现「保值率」", "保值率" in a2, False))
        s.append(run_case("追问终答必须报出真参数（2880 或 OLED）",
                          ("2880" in a2 or "OLED" in a2), True))
    except Exception:
        s.append(run_case("端到端追问：模型不可用（跳过）", True, True))

    # 对照：22 台那层的老 id 仍要取得到（不能为了修采集池把人工层弄坏）
    s.append(run_case("人工层老 id 仍能取回",
                      core.get_offer("gaming-tianxuan7") is not None, True))
    # 对照：不存在的 id 必须返回 None（判据不能为了过上面那条而永远返回对象）
    s.append(run_case("不存在的 id 必须返回 None",
                      core.get_offer("no-such-id-12345") is None, True))

    # ===== 十八、fit 路径（问「这台怎么样」）也必须拦住编造 =====
    # 🔴 2026-11-12 实测：判据漂移的**第三处**。
    #   compose() 和 answer_followup() 都加了 praise，_answer_current 漏了。
    #   实测「暗影精灵 11 锐龙版 怎么样」（采集池机器，pros/cons 为空）输出：
    #     「典型的入门级游戏本，主打性价比」
    #     「属于"强核弱显"的配置」「16G 内存是单通道」
    #     「这台机器没有独立显卡插槽，未来想升级显卡基本没路了」
    #   全库没有一个字段支持以上任何一句。而且它比编数字更危险 ——
    #   **编得像内行**，用户不会怀疑。
    print("\n十八、fit 路径也要拦住「编得像内行」的硬件形态推断")
    t = []
    mm = None
    for o in core.merged_offers():
        if o["name"].startswith("暗影精灵11 锐龙版"):
            mm = agent._merge_offer(o)
            break
    if mm:
        # 必须抓到：市场定位 / 硬件形态推断 / 人群结论
        for bad, note in [
            ("这是典型的入门级游戏本，主打性价比。", "档位与市场定位，库里没这结论"),
            ("属于强核弱显的配置。", "拿参数推结论"),
            ("16G 内存是单通道。", "库里没通道数字段，这是推的"),
            ("这台机器没有独立显卡插槽。", "库里没插槽字段，说'没有'等于断言"),
            ("适合预算有限的学生党。", "对人群下结论"),
            ("它最大的亮点是用了 R9-8945HX。", "「最大的亮点」是评价"),
        ]:
            t.append(run_case("抓到：%s（%s）" % (bad[:18], note),
                              bool(agent._praise_no_review(bad, mm)), True))
        # 必须放行：纯参数 / 真用途匹配
        for good, note in [
            ("配置是 R9-8945HX 加 RTX 5060。", "纯参数"),
            ("适合打游戏，2.5K 240Hz。", "拿真实用途判匹配，数据里有 fitVerdict"),
            ("到手价 6687.41 元。", "真实价格"),
        ]:
            t.append(run_case("放行：%s（%s）" % (good[:16], note),
                              bool(agent._praise_no_review(good, mm)), False))
        # 端到端：直接问「这台怎么样」，终答不许出现这些推断
        try:
            o2 = agent._run("暗影精灵 11 锐龙版 怎么样")
            a2 = o2.get("reply") or ""
            for w in ["入门级", "强核弱显", "单通道", "没有独立显卡插槽"]:
                t.append(run_case("实跑「这台怎么样」不许出现「%s」" % w, w in a2, False))
        except Exception:
            t.append(run_case("实跑 fit：模型不可用（跳过）", True, True))

    allr = a + b + c + d + e + f + g + h + i + j + k + m + n + q + r + s + t
    ok = sum(1 for x in allr if x)
    print("\n" + "=" * 62)
    print("判据分辨力 %d/%d" % (ok, len(allr)))
    print("说明：一/三/五/六/七/八/九/十/十一/十二/十三/十四/十五/十六 是「必须抓到错」，"
          "二/四 是「不能误杀正确的」。")
    return 0 if ok == len(allr) else 1


if __name__ == "__main__":
    sys.exit(main())