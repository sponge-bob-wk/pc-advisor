# -*- coding: utf-8 -*-
"""audit_prices.py —— 价格四段式对账

问题背景：
  采集时经常只抄"页面到手价"，把国补/券拆到别的字段时就漏了。
  结果用户看到「划线 9,799 → 国补 — → 到手 8,329」，中间的 1,470 凭空消失，
  更糟的是"国补资格"栏会显示"未查"，而实际上这个价就是补贴后的价。

对账规则（不改采集到的原始数字，只补缺失的中间项）：
  list − Σdiscounts = net
  net  − subsidy    = final
  差额若与 15% / 封顶 1500 吻合 → 判定为国补
  差额不符 15% 口径             → 判定为首单/活动优惠（Mac、机械革命常见）

为什么要区分：
  国补是政策性的、人人可得、且要求 1 级能效；
  首单/活动优惠是商家行为、有条件、不需要能效。
  混为一谈会让"国补资格"这个判断失真。

用法：
  python tools/audit_prices.py            # 只报告
  python tools/audit_prices.py --fix      # 写回源文件
"""
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def check(pr):
    """返回 (是否对账, 说明)。"""
    if not isinstance(pr, dict):
        return True, None
    lst, net, sub, fin = pr.get("list"), pr.get("net"), pr.get("subsidy"), pr.get("final")
    dis = sum((d.get("amount") or 0) for d in (pr.get("discounts") or []))
    notes = []
    if fin is None or net is None:
        return True, None
    if lst is not None and abs(net - (lst - dis)) > 1.5:
        notes.append(f"净价不符：{lst}−{dis}={lst-dis:.0f}，实际记 {net}")
    if abs(fin - (net - (sub or 0))) > 1.5:
        gap = round(net - fin, 2)
        notes.append(f"到手不符：{net}−{sub or 0}={net-(sub or 0):.0f}，实际 {fin}（差 {gap}）")
    return (not notes), "；".join(notes)


def reconcile(pr):
    """补记缺失的中间项。返回改动描述或 None。"""
    if not isinstance(pr, dict):
        return None
    # 纯算术校正必须放在最前面：net = list − Σ折扣 是恒等式。
    # 之前把它放在分支深处，遇到"条目已存在就提前返回"的路径就永远执行不到，
    # 导致补了折扣却没重算 net 的记录被永久跳过（真实踩过：蛟龙15K）。
    if pr.get("list") is not None and pr.get("net") is not None:
        want = round(pr["list"] - sum((d.get("amount") or 0) for d in pr.get("discounts") or []), 2)
        if abs(pr["net"] - want) > 1.5:
            pr["net"] = want
            return f"重算券后价为 {want}"
    net, sub, fin = pr.get("net"), pr.get("subsidy"), pr.get("final")
    if net is None or fin is None:
        return None
    gap = round(net - fin, 2)
    if gap <= 1:
        return None
    has_rev = any("差额反推" in str(d.get("name")) for d in (pr.get("discounts") or []))
    if sub is None:
        is_subsidy = (abs(gap - net * 0.15) < net * 0.01) or (abs(gap - 1500) < 1.5)
        if is_subsidy:
            pr["subsidy"] = gap
            pr["basisNote"] = (pr.get("basisNote") or "") + (
                f"｜国补 {gap} 元由「券后价 − 页面到手价」反推，与 15%/封顶1500 口径吻合；原记录未单列")
            if pr.get("list") is not None:
                pr["net"] = round(pr["list"] - sum((d.get("amount") or 0) for d in pr.get("discounts") or []), 2)
            return f"补记国补 {gap}"
        if has_rev:
            return None
        pr.setdefault("discounts", []).append({
            "name": "首单/活动优惠（差额反推）", "amount": gap,
            "evidence": f"券后 {net} − 到手 {fin} = {gap}；不符 15% 国补口径，判为商家优惠"})
        pr["basisNote"] = (pr.get("basisNote") or "") + (
            f"｜{gap} 元为首单/活动优惠，非国补（不符 15% 口径），按差额补记")
        if pr.get("list") is not None:
            pr["net"] = round(pr["list"] - sum((d.get("amount") or 0) for d in pr["discounts"]), 2)
        return f"补记商家优惠 {gap}"
    # 补记中间项后，券后价要对齐「list − Σ折扣」，否则账还是不平。
    # 这里只重算 net（由 list 与折扣推导），final 保持页面原值不动 ——
    # final 是从页面抄来的事实，net 是中间量，可以用算术保持自洽。
    if pr.get("list") is not None:
        pr["net"] = round(pr["list"] - sum((d.get("amount") or 0) for d in pr.get("discounts") or []), 2)

    extra = round(gap - (sub or 0), 2)
    if has_rev:
        return None
    if abs(extra) > 1.5:
        pr.setdefault("discounts", []).append({
            "name": "其他优惠（差额反推）", "amount": extra,
            "evidence": f"券后 {net} − 补贴 {sub} − 到手 {fin} = {extra}，名目未采到"})
        pr["basisNote"] = (pr.get("basisNote") or "") + f"｜另有 {extra} 元优惠按差额补记"
        return f"补记其他优惠 {extra}"
    return None


def main():
    do_fix = "--fix" in sys.argv
    cols = [c for c in ("id", "modelHint", "name")]

    total = bad = fixed = 0
    for f in sorted(glob.glob(str(DATA / "crawl" / "*.jsonl"))):
        if ".bak" in f or "pending" in f:
            continue
        rows, changed = [], False
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            pr = r.get("price")
            if isinstance(pr, dict):
                total += 1
                ok, why = check(pr)
                if not ok:
                    bad += 1
                    tag = next((r.get(c) for c in cols if r.get(c)), "?")
                    if do_fix:
                        act = reconcile(pr)
                        if act:
                            fixed += 1
                            changed = True
                            print(f"  [修] {tag[:34]:<36} {act}")
                        else:
                            print(f"  [留] {tag[:34]:<36} {why}")
                    else:
                        print(f"  [不合] {tag[:34]:<36} {why}")
            rows.append(r)
        if do_fix and changed:
            with open(f, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n共 {total} 条价格记录，不合 {bad} 条", end="")
    print(f"，已修正 {fixed} 条" if do_fix else "（加 --fix 修正）")
    return 0 if bad == 0 or do_fix else 1


if __name__ == "__main__":
    sys.exit(main())
