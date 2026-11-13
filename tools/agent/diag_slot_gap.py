# -*- coding: utf-8 -*-
"""
诊断：模型原生能力 vs 正则/后处理修补，各占多少。

为什么必须分开：
    extract_slots() = 模型输出 → normalize_slots() → guard_budget()
    最终 slots 是三者混合的产物。直接拿它当训练目标，
    会把「正则的功劳」记到「模型头上」，训出来的模型学不到东西。

用法：
    python diag_slot_gap.py            # 跑内置样本
    python diag_slot_gap.py --from-tests  # 从 test_*.py 抽输入
"""
import json, re, sys, glob, os

import agent

HERE = os.path.dirname(os.path.abspath(__file__))


def raw_model(user_text):
    """只要模型原生输出，不经过任何后处理。"""
    out = agent.chat([{"role": "system", "content": agent.SLOT_SYS},
                      {"role": "user", "content": user_text}],
                     fmt="json", num_predict=160)
    try:
        return json.loads(out), None
    except Exception:
        return None, out[:80]


def norm(s):
    """只保留 SLOT_SYS 定义的 6 个字段，用于公平对比。"""
    if not isinstance(s, dict):
        return {}
    keys = ("cat", "budget", "purpose", "years", "carry", "dormWatt")
    o = {}
    for k in keys:
        v = s.get(k)
        if isinstance(v, str) and k == "purpose":
            v = [v]
        if v in ([], "", "null"):
            v = None
        o[k] = v
    return o


def diff(a, b):
    """返回字段级差异 [(字段, 模型值, 最终值)]"""
    out = []
    for k in ("cat", "budget", "purpose", "years", "carry", "dormWatt"):
        va, vb = a.get(k), b.get(k)
        # purpose 空列表和 None 视为等价
        if (va in (None, []) and vb in (None, [])):
            continue
        if va != vb:
            out.append((k, va, vb))
    return out


def collect_from_tests():
    texts = []
    for fn in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
        src = open(fn, encoding="utf-8").read()
        for m in re.finditer(r'\(\s*"[A-Z]\d{1,2}[a-z]?"\s*,\s*"([^"]{2,60})"', src):
            texts.append(m.group(1))
    # 去重保序
    seen, out = set(), []
    for t in texts:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


BUILTIN = [
    "学设计的，五千，要用四年，平时背去教室",
    "七千五的轻薄本，上课用",
    "预算7000，要32G内存，不要荣耀",
    "打游戏，一万二，宿舍限电800W",
    "四万五的台式机，做视频剪辑",
    "五千八能买到什么",
    "一万二的游戏本",
    "两千五左右，就上上网",
    "九千九以内，要能剪视频",
    "一万五，越便宜越好",
]


def main():
    texts = collect_from_tests() if "--from-tests" in sys.argv else BUILTIN

    stats = {"total": 0, "raw_ok": 0, "raw_fail": 0, "changed": 0,
             "fields_changed": 0, "changes": []}

    for t in texts:
        stats["total"] += 1
        raw, err = raw_model(t)
        if raw is None:
            stats["raw_fail"] += 1
            stats["changes"].append((t, "解析失败", [], err))
            continue
        stats["raw_ok"] += 1

        # 最终值（走完整管线）
        final, _ = agent.extract_slots(t)

        a, b = norm(raw), norm(final)
        d = diff(a, b)
        if d:
            stats["changed"] += 1
            stats["fields_changed"] += len(d)
            stats["changes"].append((t, "被修正", d, None))
        else:
            stats["changes"].append((t, "一致", [], None))

    print("=" * 62)
    print("样本数            : %d" % stats["total"])
    print("模型原生可解析    : %d" % stats["raw_ok"])
    print("模型原生解析失败  : %d" % stats["raw_fail"])
    print("被后处理修正的样本: %d  (%.0f%%)" % (
        stats["changed"], 100.0 * stats["changed"] / max(1, stats["total"])))
    print("被修正的字段总数  : %d" % stats["fields_changed"])
    print()
    print("字段级修正明细（模型给的值 → 正则/后处理改成）：")
    for t, kind, d, err in stats["changes"]:
        if kind == "一致":
            print("  [一致] %s" % t)
        elif kind == "解析失败":
            print("  [失败] %s  ← %s" % (t, err))
        else:
            print("  [修正] %s" % t)
            for f, va, vb in d:
                print("         %-9s %r → %r" % (f, va, vb))

    print()
    print("=" * 62)
    print("解读要点：")
    print("  「修正」多 → 模型原生弱，LoRA 有真实增量空间")
    print("  「一致」多 → 模型已经够用，LoRA 增量接近 0，不值得训")


if __name__ == "__main__":
    main()