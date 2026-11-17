# -*- coding: utf-8 -*-
"""refresh_all.py —— 一条命令把「数据 → 判定 → 页面」整条链跑完

## 为什么需要它

这个项目的产物是**四张 HTML**，但它们不会自己跟着数据更新。
2026-11-16 就出过这个事故：数据补到 11-16，页面还停在 11-08~11-10，
用户打开看到的是两周前的机型数和价格。

而且顺序有依赖，跑错顺序会得到自相矛盾的页面：
    判定（coverage）必须早于建页（make_pick_page），否则页面嵌的是旧判定；
    统计（refresh_stats）必须早于建页，否则页面上的机型统计是旧数字。

## 步骤与依赖

    1. refresh_stats.py        重算 meta.stats（页面要读）
    2. coverage.py             重算 243×10 判定（页面要读）
    3. price_report.py         重算价格覆盖率（报告要读）
    4. make_pick_page.py       按专业选电脑.html      ← 依赖 1、2
    5. make_energy_report.py   能效数据看板.html
    6. make_crawl_report.py    采集数据看板.html
    7. build.py                电脑涨价趋势.html（读 prices.json，独立数据源）

## 用法

    python tools/refresh_all.py            # 全跑，失败即停
    python tools/refresh_all.py --skip-build   # 跳过涨价趋势页（它用独立数据源）
"""
import argparse
import os
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (脚本, 说明, 是否必需, 额外参数)
# 🔴 `refresh_stats.py` 默认是演练模式，必须显式传 --apply，否则
#    「✓ 0.1s」看着成功、其实一个字节都没写（首版就踩了）。
STEPS = [
    ("refresh_stats.py", "重算机型统计（页面要读）", True, ["--apply"]),
    ("coverage.py", "重算 243×10 判定（页面要读）", True, []),
    ("price_report.py", "重算价格覆盖率报告", True, []),
    ("make_pick_page.py", "生成 按专业选电脑.html", True, []),
    ("make_energy_report.py", "生成 能效数据看板.html", True, []),
    ("make_crawl_report.py", "生成 采集数据看板.html", True, []),
    ("build.py", "生成 电脑涨价趋势.html", False, []),
]


def run(name, desc, extra=None):
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        return False, "脚本不存在"
    print("\n" + "=" * 66)
    print("▶ %s —— %s" % (name, desc))
    print("=" * 66)
    t0 = time.time()
    cmd = [sys.executable, path] + list(extra or [])
    p = subprocess.run(cmd, cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if out:
        # 只显示最后几行，避免刷屏
        lines = out.splitlines()
        for ln in (lines if len(lines) <= 8 else lines[:3] + ["  …"] + lines[-4:]):
            print("  " + ln)
    if p.returncode != 0:
        if err:
            print("  !! stderr: " + err.splitlines()[-1][:200])
        return False, "退出码 %d" % p.returncode
    print("  ✓ %.1fs" % (time.time() - t0))
    return True, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-build", action="store_true",
                    help="跳过 build.py（它读 prices.json，与 machine_pool 是两套数据）")
    args = ap.parse_args()

    print("刷新全链路：数据 → 判定 → 页面")
    ok_n = fail_n = 0
    failed = []
    for name, desc, required, extra in STEPS:
        if name == "build.py" and args.skip_build:
            print("\n— 跳过 %s（--skip-build）" % name)
            continue
        ok, why = run(name, desc, extra)
        if ok:
            ok_n += 1
        else:
            fail_n += 1
            failed.append((name, why, required))
            print("  ✗ %s：%s" % (name, why))
            if required:
                print("\n必需步骤失败，后续页面会嵌旧数据 —— 停在这里。")
                break

    print("\n" + "=" * 66)
    print("完成：成功 %d，失败 %d" % (ok_n, fail_n))
    for name, why, required in failed:
        print("  ✗ %s（%s）%s" % (name, why, "【必需】" if required else "【可选】"))

    # 打印产物时间戳，确认真的更新了
    print("\n产物：")
    for f in ("按专业选电脑.html", "能效数据看板.html", "采集数据看板.html", "电脑涨价趋势.html"):
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            mt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p)))
            print("  %-22s %8.0f KB  %s" % (f, os.path.getsize(p) / 1024, mt))

    sys.exit(1 if fail_n else 0)


if __name__ == "__main__":
    main()
