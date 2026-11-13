#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专业细分表校验。

用法：
    python tools/check_majors.py

为什么要有这个脚本：
这张表的价值全在「能不能追到依据」上。人写的门槛一旦混进一个想当然的数字，
整个模块就退化成「凭印象推荐」。所以和 build.py 一样，任何一条不过就直接失败，
宁可麻烦一步，也不让没依据的数字进产品。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "majors.json"

# 现有引擎的四种侧重（data/prices.json 的 weights 里就是这个集合）
ENGINE_MODES = {"value", "perf", "future", "carry"}
# 可用作硬门槛的维度：原有五维 + 本模块新增三维
GATE_DIMS = {"price", "perf", "upgrade", "brand", "portable", "gpu", "ram", "storage"}
SOURCE_TYPES = {"curriculum", "sysreq"}

errs = []
warns = []
ok = []


def fail(msg):
    errs.append(msg)


def warn(msg):
    warns.append(msg)


def pass_(msg):
    ok.append(msg)


def main():
    if not DATA.exists():
        print("找不到 data/majors.json")
        return 1
    d = json.loads(DATA.read_text(encoding="utf-8"))

    # ---------- meta ----------
    meta = d.get("meta", {})
    for k in ("version", "updated", "scope", "basis", "evidenceRule", "disclaimer"):
        if not meta.get(k):
            fail("meta 缺字段：" + k)
    pass_("meta 完整")

    # ---------- sources ----------
    src = {}
    for s in d.get("sources", []):
        sid = s.get("id")
        if not sid:
            fail("有来源缺 id")
            continue
        if sid in src:
            fail("来源 id 重复：" + sid)
        for k in ("title", "publisher", "date", "url", "type"):
            if not s.get(k):
                fail("来源 %s 缺 %s" % (sid, k))
        if s.get("type") not in SOURCE_TYPES:
            fail("来源 %s 的 type 不认识：%s" % (sid, s.get("type")))
        if s.get("url", "").startswith("http") is False:
            fail("来源 %s 的 url 不是 http 链接" % sid)
        src[sid] = s
    if not src:
        fail("没有任何来源")
    n_cur = sum(1 for s in src.values() if s["type"] == "curriculum")
    n_sys = sum(1 for s in src.values() if s["type"] == "sysreq")
    pass_("来源 %d 条（培养方案 %d / 官方系统要求 %d）" % (len(src), n_cur, n_sys))

    # ---------- software ----------
    sw = {}
    for s in d.get("software", []):
        sid = s.get("id")
        if not sid:
            fail("有软件缺 id")
            continue
        if sid in sw:
            fail("软件 id 重复：" + sid)
        if not s.get("name"):
            fail("软件 %s 缺 name" % sid)
        if s.get("verified"):
            # 取证过的，必须能被反推：有来源、有内存数字
            if s.get("source") not in src:
                fail("软件 %s 标了 verified 但 source 不存在" % sid)
            elif src[s["source"]]["type"] != "sysreq":
                fail("软件 %s 的来源不是 sysreq 类：%s" % (sid, s["source"]))
            if s.get("ramMin") is None:
                fail("软件 %s 标了 verified 却没有 ramMin" % sid)
            if s.get("ramMin") is not None and s.get("ramRec") is not None:
                if s["ramMin"] > s["ramRec"]:
                    fail("软件 %s 的 ramMin 大于 ramRec" % sid)
            if s.get("vramMin") is not None and s.get("vramRec") is not None:
                if s["vramMin"] > s["vramRec"]:
                    fail("软件 %s 的 vramMin 大于 vramRec" % sid)
            if not s.get("note"):
                warn("软件 %s 没有备注，读者不知道这个数字怎么用" % sid)
        else:
            # 没取证的，只允许留空，不许写数字冒充
            for f in ("ramMin", "ramRec", "vramMin", "vramRec"):
                if s.get(f) is not None:
                    fail("软件 %s 未取证却填了 %s，这等于编数字" % (sid, f))
            if not s.get("note"):
                fail("软件 %s 未取证，必须写明为什么没取证" % sid)
        sw[sid] = s
    n_ver = sum(1 for s in sw.values() if s.get("verified"))
    pass_("软件 %d 条（已取证 %d / 待取证 %d）" % (len(sw), n_ver, len(sw) - n_ver))

    # ---------- dimensions ----------
    dims = d.get("dimensions", {})
    if not dims:
        fail("没有声明新增维度")
    for k, v in dims.items():
        if k not in GATE_DIMS:
            fail("维度 %s 不在允许的维度集合里" % k)
        for f in ("label", "scale", "why"):
            if not v.get(f):
                fail("维度 %s 缺 %s" % (k, f))

    # ---------- groups ----------
    groups = d.get("groups", [])
    if not groups:
        fail("没有任何组")
    seen_group = set()
    major_owner = {}          # (name, level) -> 组名
    major_levels = {}         # name -> set(level)
    n_ben = n_zhuan = 0

    for g in groups:
        gid = g.get("id")
        if not gid:
            fail("有组缺 id")
            continue
        if gid in seen_group:
            fail("组 id 重复：" + gid)
        seen_group.add(gid)
        label = g.get("name") or gid

        for f in ("name", "tagline", "trap", "evidence"):
            if not g.get(f):
                fail("组 %s 缺 %s" % (label, f))

        majors = g.get("majors") or []
        if len(majors) < 2:
            fail("组 %s 专业少于 2 个，不成组" % label)
        for item in majors:
            # 纯字符串 = 本科；对象 = {name, level}
            if isinstance(item, str):
                name, lv = item, "本"
            elif isinstance(item, dict):
                name, lv = item.get("name"), item.get("level", "本")
            else:
                fail("组 %s 里有无法识别的专业条目：%r" % (label, item))
                continue
            if not name:
                fail("组 %s 有空专业名" % label)
                continue
            if lv not in ("本", "专"):
                fail("专业「%s」的 level 只能是 本 / 专，写的是「%s」" % (name, lv))
            key = (name, lv)
            if key in major_owner:
                fail("专业「%s（%s）」同时出现在 %s 和 %s"
                     % (name, lv, major_owner[key], label))
            major_owner[key] = label
            major_levels.setdefault(name, set()).add(lv)
            if lv == "本":
                n_ben += 1
            else:
                n_zhuan += 1

        # 侧重必须能被现有引擎接住
        if g.get("mode") not in ENGINE_MODES:
            fail("组 %s 的 mode「%s」不是引擎已有的侧重（%s）"
                 % (label, g.get("mode"), "/".join(sorted(ENGINE_MODES))))

        # 软件引用
        sws = g.get("software") or []
        if not sws:
            fail("组 %s 没有关联任何软件" % label)
        for sid in sws:
            if sid not in sw:
                fail("组 %s 引用了不存在的软件：%s" % (label, sid))

        # 门槛数字的大小关系
        req = g.get("req") or {}
        for f in ("ramMin", "ramRec", "vramMin", "vramRec", "storageMinGB"):
            if f not in req:
                fail("组 %s 的 req 缺 %s" % (label, f))
        if req.get("ramMin") is not None and req.get("ramRec") is not None:
            if req["ramMin"] > req["ramRec"]:
                fail("组 %s 的 ramMin 大于 ramRec" % label)
        if req.get("vramMin") is not None and req.get("vramRec") is not None:
            if req["vramMin"] > req["vramRec"]:
                fail("组 %s 的 vramMin 大于 vramRec" % label)
        if req.get("gpu") not in ("either", "discrete", "none", "certified"):
            fail("组 %s 的 gpu 取值不认识：%s" % (label, req.get("gpu")))

        # 证据规则：这是整个脚本的核心
        ev = g.get("evidence")
        if ev == "official":
            derived = req.get("derivedFrom") or []
            if not derived:
                fail("组 %s 声称有官方依据，却没写 derivedFrom" % label)
            for d_id in derived:
                if d_id not in sw:
                    fail("组 %s 的 derivedFrom 引用了不存在的软件：%s" % (label, d_id))
                elif not sw[d_id].get("verified"):
                    fail("组 %s 声称依据 %s，但这条软件未取证" % (label, d_id))
            # 门槛不能低于所依据软件里的最严一项
            for d_id in derived:
                s = sw.get(d_id) or {}
                if not s.get("verified"):
                    continue
                if s.get("ramMin") and req.get("ramMin") is not None:
                    if req["ramMin"] < s["ramMin"]:
                        fail("组 %s 的 ramMin %s 低于 %s 的官方最低 %s"
                             % (label, req["ramMin"], s["name"], s["ramMin"]))
                if s.get("vramMin") and req.get("vramMin") is not None:
                    if req["vramMin"] < s["vramMin"]:
                        fail("组 %s 的 vramMin %s 低于 %s 的官方最低 %s"
                             % (label, req["vramMin"], s["name"], s["vramMin"]))
            if not req.get("rationale"):
                fail("组 %s 缺 rationale，读者不知道为什么是这个数" % label)
        elif ev == "judgment":
            r = req.get("rationale") or ""
            if len(r) < 40:
                fail("组 %s 标为判断，但理由太短（%d 字），等于没解释" % (label, len(r)))
            if req.get("derivedFrom"):
                warn("组 %s 标为判断却写了 derivedFrom，容易被误认为有官方依据" % label)
        else:
            fail("组 %s 的 evidence 必须是 official 或 judgment" % label)

        # 硬门槛
        for hg in g.get("hardGates") or []:
            if hg.get("dim") not in GATE_DIMS:
                fail("组 %s 的硬门槛维度不认识：%s" % (label, hg.get("dim")))
            if not hg.get("why"):
                fail("组 %s 的硬门槛 %s 没写 why" % (label, hg.get("dim")))
            if "min" not in hg:
                fail("组 %s 的硬门槛 %s 缺 min" % (label, hg.get("dim")))

        if not g.get("preferCats"):
            warn("组 %s 没有 preferCats，接引擎时会失去品类倾向" % label)
        if not g.get("followups"):
            warn("组 %s 没有 followups，对话层会问不出关键问题" % label)

    pass_("组 %d 个 / 专业 %d 个（本科 %d / 专科 %d）"
          % (len(groups), n_ben + n_zhuan, n_ben, n_zhuan))

    both = [n for n, lvs in major_levels.items() if len(lvs) == 2]
    if both:
        pass_("同名本专科各收录 %d 个：%s" % (len(both), "、".join(both[:6]) + ("等" if len(both) > 6 else "")))

    # ---------- 别名表 ----------
    alias = d.get("majorAliases", {})
    if not alias:
        fail("别名表是空的。没有别名表，「计科」「CS」「计算机」会被判成三个不同专业")
    canon = set(major_levels)
    for k, v in alias.items():
        if k in canon:
            fail("别名「%s」和正式专业名重名，会互相覆盖" % k)
        if v not in canon:
            fail("别名「%s」指向的专业「%s」不存在" % (k, v))
    pass_("别名 %d 条，覆盖 %d 个专业名" % (len(alias), len(set(alias.values()))))

    # ---------- 覆盖率提示 ----------
    heavy = [g for g in groups if g.get("hardGates")]
    pass_("带硬门槛的组 %d 个：%s" % (len(heavy), "、".join(g["name"] for g in heavy)))

    # ---------- 输出 ----------
    print("=" * 56)
    for m in ok:
        print("  [ok] " + m)
    for m in warns:
        print("  [warn] " + m)
    for m in errs:
        print("  [FAIL] " + m)
    print("=" * 56)
    if errs:
        print("校验失败：%d 项问题" % len(errs))
        return 1
    print("校验通过：%d 项检查全过（%d 条提示）" % (len(ok), len(warns)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
