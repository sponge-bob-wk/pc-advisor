# -*- coding: utf-8 -*-
"""
把现有测试结果 + 真实日志，导出成 LoRA 训练集（train.jsonl）。

用途只有一个：让「能不能训」这个问题变成可量化的数字，而不是感觉。
不判断数据好坏，只如实导出 + 标注来源，让缺失一目了然。

用法：
    python export_lora_data.py            # 只统计
    python export_lora_data.py --write    # 同时写出 lora_data/train.jsonl
"""
import json, os, re, glob, sys, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "lora_data")

# 判据：这条样本值不值得进训练集
MIN_REPLY = 30          # 回复太短的（"好的""这台不行"）没有训练价值
MAX_REPLY = 1200        # 太长的截断风险大


def _norm(t):
    return re.sub(r"\s+", "", t or "")


def dedup(rows):
    """按 (用户输入 归一化) 去重，**保留最后一条**。

    🔴 为什么是"最后"不是"第一条"（2026-11-13 实测改的）：
      同一句用户原话在日志里会出现多次，而 bug 修复是**按时间往后**发生的 ——
      所以同一句话的**最后一次**回答才代表现在的系统。
      原来的写法保留第一条，实测把下面这条错误答案当成了标签：
        用户：「我想要带独立显卡的」
        16:34 的回答（修复前，捏造 purpose=gaming）：「先说要紧的：你要打游戏…」
        16:48 的回答（修复后，正确）：「先说要紧的：你要独立显卡，但¥8000 以内…」
      去重保留第一条 ⇒ 训练集里躺着的正是修好的那个 bug。
      按宪法第 6 条，这是"把错焊进权重"，比少一条样本严重得多。
    """
    seen, out = set(), []
    for r in reversed(rows):                    # 从后往前扫 → 先见到的就是最后一条
        u = _norm(r["messages"][0]["content"])       # index 0 是 user，别写错
        if u in seen:
            continue
        seen.add(u)
        out.append(r)
    out.reverse()                                # 顺序还原成时间正序，便于人读
    return out


def from_logs():
    """真实日志：最可信的来源，因为那是真正发生过的对话。"""
    rows = []
    logdir = os.path.join(HERE, "logs")
    for fn in sorted(glob.glob(os.path.join(logdir, "*.log"))):
        # 🔴 铁律 2 的第二层：**修复前的输出不算答案**。
        #   2026-11-12 那版把措辞、判据、品类护栏全换过一轮（PRD §13），
        #   在那之前的日志回答是旧行为的产物 —— 比如 11-11 的
        #   「学设计的，五千…」那条答的是「最推荐的是惠普战 66…非常实用」，
        #   语气和今天的口径都不一样。拿它当标签，等于把旧版本焊进权重。
        #   所以只收 2026-11-12（含）之后的日志；旧日志留着不删，只是不进训练集。
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(fn))
        if m and m.group(1) < "2026-11-12":
            continue
        with open(fn, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not d.get("ok"):
                    continue
                # 🔴 铁律 2（《样本积攒提示词》§1）：**系统自己知道错了的轮次，不许进训练集**。
                #   `issues` 非空 = 出口审计当场检出问题（编造词 / 数据外数字 /
                #   库外机型…），那条回复即使最后被重写过，也无法从日志判定
                #   "落盘的是重写后的还是原句"。宁可少几条样本，不能把坏答案焊进权重。
                #   2026-11-13 实测过的具体例子：08:46「预算 6000 左右，轻薄本」
                #   那条推了 MacBook Air，issues 里写着 库外机型:['MacBook'] —— 它
                #   在修复前会以"标准答案"的身份进池子，那正是铁律 2 要防的事。
                if d.get("issues"):
                    continue
                reply = (d.get("reply") or "").strip()
                text = (d.get("text") or "").strip()
                if len(reply) < MIN_REPLY or not text:
                    continue
                if any(k in reply for k in ("我不给你算",)):   # 拒答样本，无训练价值
                    continue
                rows.append({
                    "messages": [
                        {"role": "user", "content": text},
                        {"role": "assistant", "content": reply[:MAX_REPLY]},
                    ],
                    "_src": "log:" + os.path.basename(fn),
                    "_stage": d.get("stage"),
                    "_has_result": bool(d.get("ranked")),
                })
    return rows


def from_auto():
    """测试即采样：`lora_data/auto-<日期>.jsonl`（`WB_SAMPLE_LOG=1` 时由 agent.run 写出）。

    为什么单列一个来源（2026-11-13 用户要求「测试的数据也要能当样本」）：
      测试脚本是直接调 agent.run 的，**不经过 server，因而没有 chat-*.log**。
      不接这条路，跑上千条测试也留不下一条样本。

    ⚠️ 只收**自包含**的提问：auto 日志里不存上下文，所以「换一台」「它的屏幕怎么样」
      这种脱离上文就没意义的句子必须剔掉 —— 拿它训练等于教模型答非所问。
      判据很简单：去掉标点后长度 >= 10 且不是纯指代句。
    """
    rows = []
    for fn in sorted(glob.glob(os.path.join(OUT_DIR, "auto-*.jsonl"))):
        with open(fn, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("issues"):
                    continue
                if d.get("stage") not in ("recommend", "followup", "compare", "fit"):
                    continue
                text = (d.get("text") or "").strip()
                reply = (d.get("reply") or "").strip()
                if len(reply) < MIN_REPLY or not text:
                    continue
                if len(_norm(text)) < 10:
                    continue
                if CONTEXT_ONLY.match(text):
                    continue
                rows.append({
                    "messages": [
                        {"role": "user", "content": text},
                        {"role": "assistant", "content": reply[:MAX_REPLY]},
                    ],
                    "_src": "auto:" + os.path.basename(fn),
                })
    return rows


# 脱离上文就没意义的句子（auto 日志不保存上下文）
CONTEXT_ONLY = re.compile(
    r"^(换一台|换台|再来一台|下一台|就这些|还有吗|这些呢|那这台呢|它呢|这个呢|"
    r"继续说|接着|然后呢|为什么|怎么说|详细点)[。！？?!\s]*$")


def from_tests():
    """
    测试用例里的用户输入 —— 这部分要如实区分：
    攻击用例（"不要七千的""六千万像素")是造出来的边界输入，
    真实用户不会这么说，混进训练集会把模型训歪。
    所以只收「听起来像真人说话」的，并单独打标。
    """
    ARTIFICIAL = ("六千万", "6000mAh", "165Hz", "2.5K", "16G+512G", "R7 8845H",
                  "12,000", "一万二千五", "六千万像素")
    rows = []
    for fn in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
        src = open(fn, encoding="utf-8").read()
        for m in re.finditer(r'\(\s*"[A-Z]\d{1,2}[a-z]?"\s*,\s*"([^"]{2,60})"', src):
            u = m.group(1)
            if any(a in u for a in ARTIFICIAL):
                continue
            rows.append({
                "messages": [
                    {"role": "user", "content": u},
                    {"role": "assistant", "content": ""},   # 空：需要重新跑一遍才有回复
                ],
                "_src": "test:" + os.path.basename(fn),
                "_stage": None,
                "_has_result": False,
            })
    return rows


def main():
    write = "--write" in sys.argv

    log_rows = from_logs()
    # 测试采样的结果接在后面：时间上更新，去重"取最后一条"时会覆盖旧日志的同句回答
    auto_rows = from_auto()
    test_rows = from_tests()

    print("=" * 58)
    print("① 真实对话日志（可直接训练，带真实回复）")
    print("   原始轮数        : %d" % (len(log_rows) + 0))
    print("   去重后          : %d" % len(dedup(log_rows)))
    texts = {_norm(r["messages"][0]["content"]) for r in log_rows}
    if texts:
        print("   平均提问字数    : %.1f" % (sum(len(t) for t in texts) / len(texts)))
        print("   平均回复字数    : %.1f" % (
            sum(len(r["messages"][1]["content"]) for r in log_rows) / max(1, len(log_rows))))

    print()
    print("② 测试用例（只有提问，没有回复 → 得重跑）")
    print("   原始条数        : %d" % len(test_rows))
    print("   去重后          : %d" % len(dedup(test_rows)))

    print()
    print("①b 测试即采样（lora_data/auto-*.jsonl）")
    print("   可用轮次        : %d" % len(auto_rows))
    log_rows = log_rows + auto_rows
    need_reply = dedup(test_rows)
    logset = {_norm(r["messages"][0]["content"]) for r in log_rows}
    need_reply = [r for r in need_reply if _norm(r["messages"][0]["content"]) not in logset]

    print()
    print("=" * 58)
    print("③ 现状合计")
    ready = dedup(log_rows)
    print("   立即可用（有问答对）      : %d 条" % len(ready))
    print("   需重跑补回复              : %d 条" % len(need_reply))
    print("   ─────────────────────────────")
    print("   满打满算上限              : %d 条" % (len(ready) + len(need_reply)))
    print()
    print("   参考基线：风格对齐 LoRA 通常 500~1000 条起步；")
    print("             低于 200 条，模型只会记住句子，学不到规律。")
    print("   → 缺口：%d 条" % max(0, 500 - (len(ready) + len(need_reply))))

    if write:
        os.makedirs(OUT_DIR, exist_ok=True)
        # 只写真正有问答对的，不写半成品 —— 空 assistant 会教模型说空话
        path = os.path.join(OUT_DIR, "train.jsonl")
        n = 0
        with open(path, "w", encoding="utf-8") as f:
            for r in ready:
                # 🔴 铁律 4：每条样本必须可追溯。原来只写 messages，
                #   将来发现某批数据有问题就整批撤不掉 —— 把 _src 一起写进去。
                clean = {"messages": r["messages"], "_src": r.get("_src", "")}
                f.write(json.dumps(clean, ensure_ascii=False) + "\n")
                n += 1
        print("\n已写出 %s（%d 条）" % (path, n))

        pend = os.path.join(OUT_DIR, "pending.jsonl")
        with open(pend, "w", encoding="utf-8") as f:
            for r in need_reply:
                f.write(json.dumps({"messages": r["messages"]}, ensure_ascii=False) + "\n")
        print("待补回复 %s（%d 条，assistant 为空，勿直接训练）" % (pend, len(need_reply)))


if __name__ == "__main__":
    main()