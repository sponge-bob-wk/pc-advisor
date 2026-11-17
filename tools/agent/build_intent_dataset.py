# -*- coding: utf-8 -*-
"""预算动作分类器 —— 数据集构建器（2026-11-13）

## 它解决什么

2026-11-13 的 bug：系统自己问「要不要放宽预算」，用户答「可以，放宽预算推荐几台」，
系统把同一台又推一遍。修的时候发现根因不只是漏了一个分支，而是**这一层本来就不该用正则**：

  同一批 16 句，正则 88%，模型 94%。
  正则错的正是「预算提高到7000」「放宽预算到7500」——
  这两句里"放宽"和"具体金额"同时出现，**两种意思可以在同一句话里共存**。
  正则只能回答"有没有某个词"，两个词都在场就得分先后，
  而我按先后猜错了一次，把用户亲口说的 7000 改成了 8000。

⇒ 意图互斥，词可以共存。这一层该由模型判断。

## 这个脚本干什么

产出三份文件，喂给 LoRA 训练 / 或直接当评测集：

  lora_data/intent_train.jsonl     训练集（有标签）
  lora_data/intent_eval.jsonl      评测集（有标签，与训练集不重叠）
  lora_data/intent_adversarial.jsonl  对抗集（专挑规则犯过错的那类）

## 🔴 标签从哪来（这是最关键的设计）

**规则不能单独当标签。** 实测：`is_relax_budget("预算提高到7000")` 返回 True，
而正确答案是"用户给了明确金额 7000，该用 7000 不该放宽"。拿这个当标签
就是**把 bug 焊进权重**（宪法第 6 条）。

所以标签按三层优先级合成，只让最可靠的那层说了算：

  L1 数字事实层：原话里能追溯到一个金额（_budget_in_text）
                 → set_budget。数字是可验证事实，优先级最高。
                 「预算提高到7000」在这里就被数字接管，不再看"提高"。
  L2 动作词层：没数字，但说了放宽/加钱/不差钱
                 → relax
  L3 兜底层：都没有
                 → none

分歧处理：规则和"人工标注过的小样本"不一致时，**人工优先**，并记录下来。
这些分歧点恰好是最有价值的训练样本。

## 用法

    python build_intent_dataset.py                # 统计，不写文件
    python build_intent_dataset.py --write        # 写出三份 jsonl
    python build_intent_dataset.py --write --show 5   # 顺便看几条
"""
import argparse
import glob
import hashlib
import io
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent  # noqa: E402

OUT_DIR = os.path.join(HERE, "lora_data")

# 三个互斥标签。为什么只有三个而不是细分更多：
# 标签越细，规则打标的错误率越高，而错误标签比少一条样本严重得多。
# relax     = 要求/同意提高预算，但没给具体数 → 由系统按比例加
# set_budget= 用户给了具体金额 → 必须以用户数字为准，系统不许改
# none      = 这句不是在动预算
LABELS = ("relax", "set_budget", "none")


# ══════════════════════════════════════════════════════════════
# 一、标签合成（规则三层 + 人工覆盖）
# ══════════════════════════════════════════════════════════════
def rule_label(text):
    """用分层规则打标签。返回 (label, 依据)。

    🔴 顺序不能换：数字层必须在动作层之前。
       反过来（先看动作词）就是 2026-11-13 那个 bug 的写法。
    """
    t = text or ""
    has_num = bool(agent._budget_in_text(t))
    relax = agent.is_relax_budget(t)

    if has_num:
        # 有明确金额 → 无论说不说"提高"，都以用户给的数为准。
        return "set_budget", "L1:数字可追溯"
    if relax:
        return "relax", "L2:放宽动作词"
    return "none", "L3:无预算信号"


# 人工标注：这些是规则会判错、但我知道正确答案的句子。
# 每一条都带理由 —— 将来谁改这里，能看见当初为什么这么定。
MANUAL = [
    # 「放宽」和「金额」同句，但金额才是用户的真实意思
    ("放宽预算到 7500", "set_budget", "用户说死了 7500，系统不许按比例自己加"),
    ("预算提高到7000，屏幕素质我有要求", "set_budget", "7000 是用户定的，放宽动作让位"),
    ("预算加到8000", "set_budget", "同上"),
    ("预算涨到9000吧，可以放宽", "set_budget", "同句有精确数字，以数字为准"),

    # 「够了」是拒绝，不是要求加
    ("预算够了", "none", "够了 = 不用加，误判会把预算偷偷改掉"),
    ("7000 够了吧", "set_budget", "这句是确认已有预算，附了数字"),
    ("不用加了，就这样", "none", "明确拒绝"),
    ("预算不是问题", "relax", "等于不差钱，可以往上走"),

    # 「加」的对象不是预算
    ("内存能加吗", "none", "加的是内存，不是预算"),
    ("内存可以加装吗", "none", "同上"),
    ("加个硬盘", "none", "同上"),
    ("内存加到32G", "none", "同上，这是改配置"),
    ("显卡能加吗", "none", "同上"),

    # 要便宜 ≠ 要放宽
    ("能不能便宜点", "none", "要降价，反向"),
    ("太贵了", "none", "抱怨价格，不是加钱"),
    ("便宜点的有没有", "none", "要降价"),

    # 纯放宽，无数字
    ("可以，放宽预算推荐几台", "relax", "系统自己提的建议，用户答应了"),
    ("那就放宽点预算吧", "relax", "标准说法"),
    ("再放宽点", "relax", "承接上一轮系统建议，可以不带'预算'二字"),
    ("放宽吧", "relax", "承接上一轮"),
    ("不差钱", "relax", "预算不限"),
    ("加点预算", "relax", "标准说法"),
    ("多点预算", "relax", "标准说法"),
    ("可以加点", "relax", "口语"),
    ("往上加", "relax", "口语"),
    ("提高预算", "relax", "标准说法"),

    # 与预算无关的常见句
    ("这台屏幕怎么样", "none", "追问配置"),
    ("换一台", "none", "换台，不动预算"),
    ("还有别的品牌吗", "none", "换品牌"),
    ("推荐一台游戏本", "none", "提新需求"),
    ("我要 32G 内存", "none", "改配置约束"),
    ("能用四年吗", "none", "问耐用性"),
    ("多重啊", "none", "问重量"),
    ("", "none", "空输入"),
]

MANUAL_MAP = {}
for _text, _lab, _why in MANUAL:
    MANUAL_MAP[re.sub(r"\s+", "", _text)] = (_lab, _why)


def manual_label(text):
    key = re.sub(r"\s+", "", text or "")
    return MANUAL_MAP.get(key)


def label_of(text):
    """最终标签：人工覆盖 > 分层规则。返回 (label, 依据, 是否与规则冲突)。"""
    man = manual_label(text)
    rule, why = rule_label(text)
    if man:
        m_lab, m_why = man
        return m_lab, "人工:" + m_why, (m_lab != rule)
    return rule, why, False


# ══════════════════════════════════════════════════════════════
# 二、对抗集（专挑规则犯错的那类）
# ══════════════════════════════════════════════════════════════
def adversarial_cases():
    """构造对抗样本。

    🔴 为什么要单独一份：普通训练集里"放宽"和"金额"很少同句，
    模型学不到这个冲突。但这恰恰是规则翻车的地方，也是真实用户最可能说的
    （系统问"放宽吗"，用户答"行，那放到8000"—— 一句话里两种意思俱全）。
    对抗集就是拿这些冲突句去试：谁会栽。
    """
    cases = []

    # A 类：放宽动作 + 精确金额同句（规则必然栽）
    for n in (6000, 6500, 7000, 7500, 8000, 8500, 9000, 10000, 12000):
        cases += [
            ("放宽预算到 %d" % n, "set_budget", "A 同句冲突"),
            ("预算提高到%d，屏幕要好" % n, "set_budget", "A 同句冲突"),
            ("那就放到 %d 吧" % n, "set_budget", "A 口语版"),
            ("可以，加到%d" % n, "set_budget", "A 口语版"),
        ]

    # B 类：同句冲突 + 上轮 exhausted 语境（真实事故形态）
    for tail in ("推荐几台", "再看看", "有别的吗", "多来几台"):
        cases += [
            ("可以，放宽预算%s" % tail, "relax", "B 承接系统建议，无数字"),
            ("行，%s吧" % tail, "none", "B 只说看几台，没动预算"),
        ]

    # C 类：否定与拒绝（误判代价最大的方向）
    for t in ("预算够了", "不用加", "不用放宽", "预算已经够用",
              "不想加钱", "不能超预算", "就这些钱", "预算不能再高了"):
        cases.append((t, "none", "C 拒绝加钱"))

    # D 类：加的对象不是预算（同形异义）
    for t in ("内存能加吗", "加个固态", "加装内存条", "能不能加显卡",
              "加个散热底座", "运存加到16G", "硬盘能加大吗"):
        cases.append((t, "none", "D 加的不是预算"))

    # E 类：便宜 vs 放宽（方向相反）
    for t in ("能不能便宜点", "太贵了", "有更便宜的吗", "价格能再压压吗",
              "降点价", "预算降到5000"):
        cases.append((t, "none", "E 要降价（反向）"))

    # F 类：承接上一轮但表达极简
    for t in ("行", "可以", "好", "嗯", "随便", "你定"):
        cases.append((t, "none", "F 极简回复，无法判断 -> 宁可归 none"))

    # G 类：正好在边界上，容易被两方都判错
    for t in ("预算大约七千", "七千左右", "七八千吧", "一万以内都行",
              "五六千", "三千到五千"):
        cases.append((t, "set_budget", "G 中文数字/区间"))

    return cases


# ══════════════════════════════════════════════════════════════
# 三、从日志与测试集挖真实句子
# ══════════════════════════════════════════════════════════════
def from_logs():
    """从对话日志里抽出用户原话。

    日志是 JSONL（每行一个 turn），用户原话在 `text` 字段。
    只抽 `text` —— assistant 的话不进训练集，那是系统的说话风格，
    不是待分类的输入（拿它当输入会教模型"复读上一轮"）。

    附加信息：日志里还有 `known.budget` 和 `slots.budget`。
    两者不相等时，说明**这一轮预算真的变了**，这条样本价值更高 ——
    它是"用户做了什么动作"的直接证据，不用猜。
    """
    rows = []
    for fp in sorted(glob.glob(os.path.join(HERE, "logs", "chat-*.log"))):
        try:
            fh = io.open(fp, encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            u = (rec.get("text") or "").strip()
            if not (1 <= len(u) <= 120):
                continue
            # 从日志里直接读出"这一轮预算变没变"—— 这是最硬的证据
            kb = (rec.get("known") or {}).get("budget")
            sb = (rec.get("slots") or {}).get("budget")
            hint = None
            if kb is not None and sb is not None and kb != sb:
                hint = ("moved", kb, sb)      # 预算从 kb 变成 sb
            rows.append((u, os.path.basename(fp), hint))
        fh.close()
    return rows


def from_tests():
    """从测试脚本里抽 user 那一侧的话。

    这些句子的好处是**都是真人语气写的**，而且已经过一轮人工挑选，
    比日志里的更干净（日志里有大量重复和打字错误）。
    """
    rows = []
    for fp in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
        if os.path.basename(fp) in ("test_intent.py",):
            # test_intent 的句子是"意图"标注，不是预算标注，单列
            pass
        try:
            txt = io.open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        # turns/ASK 这类列表里的字符串
        for m in re.finditer(r'"([\u4e00-\u9fa5][^"]{2,60})"', txt):
            s = m.group(1)
            # 过滤掉代码里的说明性长句（含英文变量名、括号等）
            if re.search(r"[A-Za-z_]{3,}|==|\.py|\{|\}", s):
                continue
            rows.append((s, os.path.basename(fp)))
    return rows


# ══════════════════════════════════════════════════════════════
# 四、组装
# ══════════════════════════════════════════════════════════════
SYS_PROMPT = (
    "你是电脑选购助手的意图分类器。判断用户这句话对「预算」做了什么。\n"
    "只输出 JSON：{\"budget_action\": \"...\"}\n\n"
    "三个取值：\n"
    "- relax       要求或同意提高预算上限，但没有给出具体金额\n"
    "- set_budget  用户给出了一个具体金额，必须以这个数为准\n"
    "- none        这句话不是在调整预算（可能在问配置、要换台、要更便宜）\n\n"
    "重要：如果一句话里既有「放宽/提高」又有具体数字，选 set_budget —— "
    "用户给的数字优先于放宽这个动作。"
)


def make_sample(text, label, src, note=""):
    return {
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": text},
            {"role": "assistant", "content": json.dumps(
                {"budget_action": label}, ensure_ascii=False)},
        ],
        "_src": src,
        "_note": note,
    }


def _h(text):
    return hashlib.md5(re.sub(r"\s+", "", text or "").encode("utf-8")).hexdigest()[:12]


def _label_of_sample(row):
    return json.loads(row["messages"][2]["content"])["budget_action"]


def balance(rows, cap_ratio=0.5):
    """把少数类过采样，直到不超过多数类的 cap_ratio 倍。

    🔴 为什么必须做：实测原始分布 none:set_budget:relax ≈ 824:124:29。
       全答 none 就有 84% 准确率 —— 这个数字很好看，但模型什么都不会。
       微调最容易在这里骗人：**准确率高不等于学会了**。

    为什么是"补到半数"而不是"拉平"：
      拉平意味着 relax 要重复 28 倍，模型会把那 29 句原句直接背下来
      （过拟合），换个说法就不认了。补到半数是个折中：
      既让少数类有足够梯度信号，又不至于让它变成纯记忆。

    为什么是复制而不是造新句：
      这个脚本不生成文本。造句子需要模型，而模型造的句子标签不可信 ——
      拿不可信的标签训练，就是把噪声焊进权重。
      真正的扩样本要靠 harvest_samples.py 从真实日志补。
    """
    by_lab = {}
    for r in rows:
        by_lab.setdefault(_label_of_sample(r), []).append(r)

    counts = {k: len(v) for k, v in by_lab.items()}
    major = max(counts.values()) if counts else 0
    target = max(1, int(major * cap_ratio))

    out = []
    for lab, group in by_lab.items():
        out.extend(group)
        if len(group) < target and group:
            # 按顺序循环复制，保证均匀覆盖而不是把前几条复制几十遍
            need = target - len(group)
            for i in range(need):
                out.append(group[i % len(group)])
    return out


def build():
    """返回 (train, eval, adv, stats)。按句子 hash 切分，绝不重叠。"""
    stats = {"manual": 0, "conflict": 0, "log": 0, "test": 0, "log_moved": 0}

    # 1) 人工标注 —— 全部进训练集（最可靠，也最该教会模型）
    manual_rows, conflicts = [], []
    for text, lab, why in MANUAL:
        manual_rows.append((text, lab, "manual", why, None))
        # 🔴 冲突检测比的是**旧规则**（单层 is_relax_budget），不是新的分层规则。
        #    为什么：新分层规则是照着这批人工答案的逻辑写的，两边必然一致，
        #    拿它比永远得 0 条 —— 那是**没有分辨力的判据**（宪法第 4 条）。
        #    要证明"为什么得换成模型"，就得拿**出事时的那个判据**去比：
        #    2026-11-13 翻车的就是"只看 is_relax_budget"这一步。
        if not manual_label(text):
            continue
        old = agent.is_relax_budget(text)
        has_num = bool(agent._budget_in_text(text))
        old_lab = "set_budget" if (has_num and old) else ("relax" if old else "none")
        if old_lab != lab:
            conflicts.append((text, old_lab, lab, "旧规则:is_relax_budget=%s + 有数字=%s"
                              % (old, has_num)))
    stats["manual"] = len(manual_rows)
    stats["conflict"] = len(conflicts)

    # 2) 日志与测试集 —— 用分层规则打标
    auto_rows, seen = [], set()

    for text, src, hint in from_logs():
        k = _h(text)
        if k in seen:
            continue
        seen.add(k)
        if hint:
            # 日志显示这一轮预算真变了：kb -> sb。
            # 变高 → 用户做了"加钱"动作；具体加了数就是 set_budget。
            kb, sb = hint[1], hint[2]
            if sb > kb:
                lab = "set_budget" if agent._budget_in_text(text) else "relax"
            else:
                # 预算反而降了 —— 那不是放宽。这句不进训练集，
                # 因为"降预算"是另一种动作，混进 relax 会教错。
                continue
            auto_rows.append((text, lab, "log:" + src, "L0:日志实证预算 %s→%s" % (kb, sb), hint))
            stats["log_moved"] += 1
            continue
        lab, why, _ = label_of(text)
        auto_rows.append((text, lab, "log:" + src, why, None))
    stats["log"] = len(auto_rows)

    n_before = len(auto_rows)
    for text, src in from_tests():
        k = _h(text)
        if k in seen:
            continue
        seen.add(k)
        lab, why, _ = label_of(text)
        auto_rows.append((text, lab, "test:" + src, why, None))
    stats["test"] = len(auto_rows) - n_before

    # 3) 对抗集 —— 全部进对抗文件，不混进训练集
    adv = [make_sample(t, l, "adversarial", n) for t, l, n in adversarial_cases()
           if not manual_label(t)]

    # 4) 切分
    train, evalset = [], []
    for text, lab, src, note, _h2 in manual_rows:
        train.append(make_sample(text, lab, src, note))
    for text, lab, src, note, _h2 in auto_rows:
        s = make_sample(text, lab, src, note)
        (evalset if int(_h(text)[:4], 16) % 5 == 0 else train).append(s)

    return train, evalset, adv, stats, conflicts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写出 jsonl")
    ap.add_argument("--show", type=int, default=0, help="打印几条样本")
    ap.add_argument("--no-balance", action="store_true", help="不做类别均衡（对比用）")
    args = ap.parse_args()

    train, evalset, adv, stats, conflicts = build()

    def dist(rows):
        c = {}
        for r in rows:
            lab = json.loads(r["messages"][2]["content"])["budget_action"]
            c[lab] = c.get(lab, 0) + 1
        return c

    print("=" * 62)
    print("预算动作分类器 · 数据集")
    print("=" * 62)
    print("人工标注       %4d 条" % stats["manual"])
    print("日志挖掘       %4d 条  （其中 %d 条有预算变动的实证）"
          % (stats["log"], stats["log_moved"]))
    print("测试集挖掘     %4d 条" % stats["test"])
    print("-" * 62)
    print("规则 vs 人工 冲突 %d 条  ← 这些是规则判错、必须训进模型的地方" % stats["conflict"])
    for text, r_lab, m_lab, r_why in conflicts[:12]:
        print("    %-26r 规则=%-11s 应为=%-11s (%s)" % (text[:24], r_lab, m_lab, r_why))
    if len(conflicts) > 12:
        print("    ... 另有 %d 条" % (len(conflicts) - 12))

    d_train = dist(train)
    print("-" * 62)
    print("训练集（原始） %4d 条  %s" % (len(train), d_train))
    print("评测集         %4d 条  %s" % (len(evalset), dist(evalset)))
    print("对抗集         %4d 条  %s" % (len(adv), dist(adv)))

    # 🔴 类别均衡：这步不做，训出来的模型只会答最多的那一类。
    #    实测分布 none 占 84% —— 全答 none 就有 84% 准确率，但完全没用。
    #    做法：少数类**过采样到多数类的 1/2 上限**（不拉平，因为拉平会让
    #    relax 这种本来就少的类别被重复太多次，直接记住原句）。
    if not args.no_balance:
        train = balance(train, cap_ratio=0.5)
        print("-" * 62)
        print("训练集（均衡后）%4d 条  %s" % (len(train), dist(train)))

    # 数据够不够训，要给判断而不是让人猜
    n_min = min(dist(train).values())
    n = len(train)
    if n < 200:
        verdict = "偏少：分类任务勉强能跑，但要靠对抗集补信号"
    elif n_min < 30:
        verdict = "够训，但最少的那类只有 %d 条，评测时别只看总准确率" % n_min
    else:
        verdict = "充足（最少类 %d 条）" % n_min
    print("-" * 62)
    print("结论：%s（训练集 %d 条）" % (verdict, n))

    if args.show:
        print("\n样例：")
        for r in train[:args.show]:
            u = r["messages"][1]["content"]
            a = r["messages"][2]["content"]
            print("  %-34r → %-18s [%s]" % (u[:32], a, r["_note"][:24]))

    if args.write:
        os.makedirs(OUT_DIR, exist_ok=True)
        for name, rows in (("intent_train.jsonl", train),
                           ("intent_eval.jsonl", evalset),
                           ("intent_adversarial.jsonl", adv)):
            fp = os.path.join(OUT_DIR, name)
            with io.open(fp, "w", encoding="utf-8", newline="\n") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print("写出 %s（%d 条）" % (name, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())