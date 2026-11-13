#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据体检 + 重建。

用法：
    python tools/refresh.py            体检，告诉你哪些数据过期了、该去看哪个链接
    python tools/refresh.py --build    只重建页面
    python tools/refresh.py --stamp    把数据截止日改成今天，然后重建

说清楚一件事：这个脚本**不自动抓网页**。
理由：这几个来源的页面结构各不相同、随时会改，自动抓要么抓空、要么抓到旧值，
而你最不能接受的恰恰是"数字看着对、其实是错的"。所以这里走"体检 + 人工改数 + 构建时校验"：
脚本负责告诉你哪条该更新、去哪看；改完 prices.json 后 build.py 会重算一遍涨幅，
对不上就直接构建失败 —— 让错的数据进不了页面。
"""

import calendar
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "prices.json"
STALE_DAYS = 45


def parse_date(s):
    """日期可能是 2026-10-12，也可能只到月 2026-10（按当月最后一天算）。"""
    s = str(s).strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        pass
    try:
        d = datetime.strptime(s, "%Y-%m").date()
        last = calendar.monthrange(d.year, d.month)[1]
        return date(d.year, d.month, last)
    except ValueError:
        return None


def main():
    args = sys.argv[1:]
    if not DATA.exists():
        print("找不到 " + str(DATA))
        return 1
    data = json.loads(DATA.read_text(encoding="utf-8"))

    if "--build" in args:
        return rebuild()

    today = date.today()
    stale, fresh = [], []
    for s in data["sources"]:
        d = parse_date(s.get("date"))
        if not d:
            stale.append((s, None))
            continue
        age = (today - d).days
        (stale if age > STALE_DAYS else fresh).append((s, age))

    print("数据截止日：%s（距今 %d 天）" % (data["meta"]["updated"], (today - (parse_date(data["meta"]["updated"]) or today)).days))
    print("来源 %d 条，其中 %d 条超过 %d 天未核对\n" % (len(data["sources"]), len(stale), STALE_DAYS))

    if stale:
        print("需要重新核对：")
        for s, age in stale:
            tag = "日期无法解析" if age is None else ("已 %d 天" % age)
            print("  [%s] %s · %s" % (tag, s["publisher"], s["title"]))
            print("        %s" % s["url"])
            for use in s.get("usedFor", []):
                print("        → 这条数据支撑：" + use)
            print()
        print("改法：打开上面链接，把新数字写进 data/prices.json 里对应的字段，")
        print("      再把该来源的 date 改成核对日期。然后跑 python tools/build.py。")
        print("      构建时会重算「去年→现在」的涨幅，和 change 字段对不上会直接报错。\n")
    else:
        print("全部在 %d 天内，暂时不用动。\n" % STALE_DAYS)

    if "--stamp" in args:
        print("提示：--stamp 只在确认数据真的核对过之后再用，否则等于把旧数据标成新的。")

    return rebuild()


def rebuild():
    import subprocess
    sys.stdout.flush()  # 先把自己的报告刷出去，否则子进程的输出会插到前面
    return subprocess.call([sys.executable, str(ROOT / "tools" / "build.py")])


if __name__ == "__main__":
    sys.exit(main())
