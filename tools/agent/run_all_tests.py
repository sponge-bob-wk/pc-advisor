# -*- coding: utf-8 -*-
r"""run_all_tests.py —— 一条命令跑全部测试

## 为什么需要

项目里有 25 个测试文件，但：

  · 输出格式各不相同（有的 `通过 N/M`，有的 `N/M=xx%`，有的只有末行）
  · 8 个需要 Ollama，服务没起就全报 `URLError`，看不出是"没跑"还是"跑挂了"
  · 之前每次都是手写 `foreach ($f in ...)`，容易漏

这个脚本统一处理：

  ① **前置检查** —— Ollama 没起就明确说"跳过 N 个需要模型的测试"
  ② **统一解析** —— 多种输出格式都认
  ③ **汇总表** —— 一眼看到哪个挂了
  ④ **超时保护** —— 单个测试最多 15 分钟

## 用法

    & 'E:\ai2026\Scripts\python.exe' run_all_tests.py
    & 'E:\ai2026\Scripts\python.exe' run_all_tests.py --fast    # 只跑不依赖模型的
    & 'E:\ai2026\Scripts\python.exe' run_all_tests.py --only test_scope,test_analyze
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
OLLAMA = "http://127.0.0.1:11434"

# (文件名, 是否依赖模型, 说明)
TESTS = [
    ("test_scope", False, "越界拦截"),
    ("test_weight", False, "重量字段"),
    ("test_source", False, "数据可追溯"),
    ("test_analyze", True, "数据分析"),
    ("test_named", True, "点名机型"),
    ("test_multiturn", True, "多轮语义"),
    # 🔴 2026-11-25：补上八轮对抗测试的前五轮。
    #    之前只注册了 attack6/7/8，前三轮漏了 —— 它们**还能跑**
    #    （实测 test_attack 有 29 条攻击），只是输出格式不同。
    #    漏注册 = 那 1300 多行测试从来没进过回归。
    ("test_attack", True, "对抗第1轮：刁难"),
    ("test_attack2", True, "对抗第2轮：看着对其实错"),
    ("test_attack3", True, "对抗第3轮：金额解析"),
    ("test_attack4", True, "对抗第4轮：多轮状态"),
    ("test_attack5", True, "对抗第5轮：状态机"),
    ("test_attack6", True, "对抗第6轮：数字对标签错"),
    ("test_attack7", True, "对抗第7轮：指代与否定"),
    ("test_attack8", True, "对抗第8轮：真实用户视角"),
    ("test_neg", True, "否定判据"),
    ("test_chain", True, "思维链"),
    ("test_intent", True, "意图分类"),
    ("test_fuzz", True, "模糊输入"),
    ("test_answer", True, "答案质量"),
    ("test_social", True, "社交寒暄"),
    ("test_e2e", True, "端到端"),
    ("test_relax_budget", True, "预算放宽"),
    ("test_slot_hard", True, "硬约束"),
]

# 各种输出格式 → (通过数, 总数)
#
# 🔴 2026-11-25：扩展了 4 种格式。
#    八轮对抗测试的汇总行各写各的：
#      test_attack   攻击 N 条，硬问题 M 条，可接受项 K 条
#      test_attack2  第二轮攻击：通过 N/M
#      test_attack3  第三轮攻击：全部通过 / 失败 N 条
#    这个项目已经因为「判据抄两遍会漂」吃过 9 次亏，
#    测试输出格式不统一是同一个病根 —— 但改 1300 行测试不如改解析。
PARSE_PATTERNS = [
    (r"通过\s*(\d+)\s*/\s*(\d+)", "通过 N/M"),
    (r"(\d+)\s*/\s*(\d+)\s*=\s*[\d.]+%", "N/M=x%"),
    (r"判据分辨力\s*(\d+)\s*/\s*(\d+)", "判据分辨力"),
    (r"违规\s*(\d+)\s*/\s*(\d+)", "违规 N/M"),      # 违规 0/60 = 全过
    (r"(\d+)\s*个通过\s*/\s*(\d+)", "N 个通过"),
    # 「攻击 29 条，硬问题 2 条」→ 通过 27/29
    (r"攻击\s*(\d+)\s*条[，,]\s*硬问题\s*(\d+)\s*条", "攻击N硬问题M"),
]



def ollama_up():
    try:
        urllib.request.urlopen(OLLAMA + "/api/version", timeout=4).read()
        return True
    except Exception:
        return False


def parse_result(out, rc):
    """从输出里解析 (通过, 总数, 备注)。"""
    # 「攻击 N 条，硬问题 M 条」→ 通过 = N - M
    m = re.search(r"攻击\s*(\d+)\s*条[，,]\s*硬问题\s*(\d+)\s*条", out)
    if m:
        n, hard = int(m.group(1)), int(m.group(2))
        return n - hard, n, "攻击 %d（硬问题 %d）" % (n, hard)

    # 「第 N 轮攻击：全部通过」→ 没有数字，用 exit code 判定
    if re.search(r"第[一二三四五六七八]轮攻击：全部通过", out):
        return None, None, "全部通过"

    # 「第 N 轮攻击：失败 M 条」→ 有失败，具体总数未知
    m = re.search(r"第[一二三四五六七八]轮攻击：失败\s*(\d+)\s*条", out)
    if m:
        return None, None, "失败 %s 条" % m.group(1)

    for pat, name in PARSE_PATTERNS:
        m = re.search(pat, out)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if name == "违规 N/M":
                # 违规 0/60 → 60 通过
                return b - a, b, "%s（违规 %d）" % (name, a)
            return a, b, name

    if "全绿" in out:
        return None, None, "全绿（无数字）"
    if rc == 0:
        return None, None, "exit=0（无汇总）"
    return None, None, "exit=%d（无汇总）" % rc


def run_one(name, timeout=900):
    """跑一个测试，返回 dict。"""
    p = os.path.join(HERE, name + ".py")
    if not os.path.exists(p):
        return {"name": name, "status": "missing", "msg": "文件不存在"}

    t0 = time.time()
    try:
        r = subprocess.run([PY, name + ".py"], cwd=HERE,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout)
        dt = time.time() - t0
        out = (r.stdout or "") + (r.stderr or "")

        # 服务没起 = 跳过，不算失败
        if "URLError" in out or "10061" in out or "Connection refused" in out:
            return {"name": name, "status": "skipped", "msg": "模型服务未就绪",
                    "sec": round(dt, 1)}

        passed, total, note = parse_result(out, r.returncode)
        if total is not None:
            ok = passed == total
            return {"name": name, "status": "pass" if ok else "fail",
                    "passed": passed, "total": total, "note": note,
                    "sec": round(dt, 1),
                    "fails": _extract_fails(out)}
        return {"name": name,
                "status": "pass" if r.returncode == 0 else "fail",
                "note": note, "sec": round(dt, 1),
                "fails": _extract_fails(out)}
    except subprocess.TimeoutExpired:
        return {"name": name, "status": "timeout",
                "msg": "超过 %ds" % timeout}
    except Exception as e:
        return {"name": name, "status": "error",
                "msg": "%s: %s" % (type(e).__name__, str(e)[:60])}


def _extract_fails(out, limit=3):
    """从输出里抠失败项（[FAIL] 开头的行）。"""
    fails = []
    for ln in out.split("\n"):
        s = ln.strip()
        if s.startswith("[FAIL]") or s.startswith("[FAILED]"):
            fails.append(s[:70])
            if len(fails) >= limit:
                break
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true",
                    help="只跑不依赖模型的测试")
    ap.add_argument("--only", default="",
                    help="只跑这些（逗号分隔）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    todo = TESTS
    if args.fast:
        todo = [x for x in TESTS if not x[1]]
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        todo = [x for x in TESTS if x[0] in want]

    has_model = ollama_up()
    need_model = [x for x in todo if x[1]]
    no_model = [x for x in todo if not x[1]]

    print("=" * 72)
    print("跑全部测试")
    print("=" * 72)
    print("  模型服务: %s" % ("✓ Ollama 就绪" if has_model
                              else "✗ 未就绪（%d 个测试会跳过）"
                                   % len(need_model)))
    print("  计划跑 %d 个（%d 个不依赖模型 / %d 个依赖）"
          % (len(todo), len(no_model), len(need_model)))
    print()

    results = []
    # 先跑不依赖模型的（快，先给反馈）
    for name, dep, desc in no_model + need_model:
        if dep and not has_model:
            results.append({"name": name, "status": "skipped",
                            "msg": "模型服务未就绪"})
            print("  %-20s 跳过（模型服务未就绪）" % name)
            continue
        
        r = run_one(name)
        results.append(r)
        st = r["status"]
        if st == "pass":
            n = r.get("total")
            print("  %-20s ✓ %s" % (
                name, ("%d/%d" % (r["passed"], n)) if n else r.get("note", "")))
        elif st == "fail":
            n = r.get("total")
            print("  %-20s ✗ %s" % (
                name, ("%d/%d" % (r["passed"], n)) if n else r.get("note", "")))
            for f in r.get("fails") or []:
                print("      %s" % f)
        else:
            print("  %-20s ! %s" % (name, r.get("msg", st)))

    # ── 汇总 ──
    print()
    print("=" * 72)
    by = {}
    for r in results:
        by.setdefault(r["status"], []).append(r)

    p = len(by.get("pass", []))
    f = len(by.get("fail", []))
    s = len(by.get("skipped", []))
    o = len(results) - p - f - s

    # 断言总数
    tot_pass = sum(r.get("passed", 0) for r in results
                   if r.get("total") is not None)
    tot_all = sum(r.get("total", 0) for r in results
                  if r.get("total") is not None)

    print("  通过 %d 个" % p, end="")
    if f:
        print("  |  **失败 %d 个**" % f, end="")
    if s:
        print("  |  跳过 %d 个" % s, end="")
    if o:
        print("  |  异常 %d 个" % o, end="")
    print()
    if tot_all:
        print("  断言合计: %d / %d" % (tot_pass, tot_all))
    print()

    if f:
        print("  失败的：")
        for r in by["fail"]:
            print("    %-20s %s" % (r["name"], r.get("note", "")))
            for x in r.get("fails") or []:
                print("        %s" % x)
    if s:
        print("  跳过的（模型服务未就绪）：")
        print("    %s" % ", ".join(r["name"] for r in by["skipped"]))
    if o:
        print("  异常的：")
        for r in results:
            if r["status"] not in ("pass", "fail", "skipped"):
                print("    %-20s %s" % (r["name"], r.get("msg", "")))

    print("=" * 72)

    if args.json:
        print()
        print(json.dumps(results, ensure_ascii=False, indent=1))

    return 0 if f == 0 and o == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
