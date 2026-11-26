# -*- coding: utf-8 -*-
"""refresh_engine.py —— 数据刷新引擎：点按钮 → 自动抓取 → 交叉验证 → 落盘

## 用户要的东西

> 「点击刷新按钮，内部会调用脚本自动去一些网站获取数据，
>   当然逻辑上这些数据可以交叉验证最好」

所以这个模块要做四件事：

    1. **抓**：按来源并行/串行调用已有采集器（ZOL 参数页 / ZOL 价格 / NBC 跑分）
    2. **核**：同一台机器多个来源的同一字段，**互相印证**；不一致就标出来
    3. **落**：只把「至少一个来源确认过」的写进池子，每笔带 provenance
    4. **报**：返回给前端一份可读的刷新报告（抓了多少、新增多少、冲突多少）

## 为什么是"引擎"而不是直接串脚本

直接串脚本有三个问题，都会在"点按钮"这个场景下暴露：

  · **前端不能等 5 分钟**：串行抓 163 台要 3-5 分钟，HTTP 请求早超时了。
    → 必须异步：POST 触发，立刻返回 job_id；前端轮询 /api/refresh/status。
  · **抓一半失败怎么办**：网络抖一下就全废。
    → 分步独立，每步单独记成败；某步失败不影响其它步。
  · **抓到的数据不能直接信**：这正是"交叉验证"要解决的。
    → 每个字段记 `sources`，只有 ≥2 个来源一致才算"已交叉验证"，
      单来源标 `single`，冲突标 `conflict` 并保留两个值。

## 交叉验证的口径（这是本模块的核心设计）

同一台机器、同一个字段，可能来自：

    ZOL 参数页    —— 规格（CPU/内存/硬盘/屏幕/接口）
    ZOL detail 页 —— 参考报价、平台价、经销商区间
    NBC 汇总表    —— CPU 跑分（跨机型聚合）
    京东商品页    —— 到手价（历史实采，本轮不重抓）

判定三档（写进 `provenance`）：

    confirmed  ≥2 个来源一致            → 可以放心用
    single     只有 1 个来源             → 能用，但标注
    conflict   多个来源不一致            → 两个值都留着，人工/下次刷新再判

⚠️ 交叉验证**只对同一字段的同一量纲**做。不会拿"ZOL 参考价"去核"京东到手价" ——
   那两个本来就是不同口径（见 AGENTS.md §5.2），强行比会制造假冲突。

## 用法

    # 作为模块（server 调用）
    from refresh_engine import start_refresh, refresh_status
    job = start_refresh(["prices", "bench"])

    # 命令行（调试/手动）
    python refresh_engine.py --steps prices,bench
    python refresh_engine.py --steps all --dry
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
ROOT = os.path.dirname(TOOLS)
DATA = os.path.join(ROOT, "data")

# ── 步骤定义 ────────────────────────────────────────────────────────────
# 每步：脚本、参数、超时（秒）、依赖、说明、产物
#
# 🔴 `where` 指定脚本所在目录：
#      "tools"       —— 采集/生成类脚本（price_multisource / bench_* / coverage …）
#      "tools/agent" —— 智能体自己的脚本（derive_notes 在这里）
#    首版没有这个字段，导致 `derive_notes.py` 按 tools/ 去找、报"脚本不存在"。
#
# 🔴 顺序有依赖：价格必须在「重建池」之前抓，否则写进去又被覆盖。
STEPS = {
    "prices": {
        "script": "price_multisource.py",
        "where": "tools",
        "args": ["--min-interval", "0.8"],
        "timeout": 600,
        "desc": "抓 ZOL 详情页价格（参考价/平台价/经销商区间）",
        "produces": ["data/zol/prices-2026-11-16.jsonl"],
        "depends": [],
    },
    "apply_prices": {
        "script": "apply_prices.py",
        "where": "tools",
        "args": ["--apply"],
        "timeout": 120,
        "desc": "把抓到的价格写进机型池（不碰 net/final）",
        "produces": ["data/machine_pool.json"],
        "depends": ["prices"],
    },
    "bench": {
        "script": "bench_fetch_nbc.py",
        "where": "tools/agent",
        "args": ["--write"],
        "timeout": 300,
        "desc": "抓 Notebookcheck CPU 跑分",
        "produces": ["data/bench_nbc.json"],
        "depends": [],
    },
    "apply_bench": {
        "script": "bench_merge_nbc.py",
        "where": "tools/agent",
        "args": ["--apply"],
        "timeout": 120,
        "desc": "合并跑分进 bench.json（带 aggregated 标记）",
        "produces": ["data/bench.json"],
        "depends": ["bench"],
    },
    "verify": {
        "script": "verify_bench.py",
        "where": "tools",
        "args": [],
        "timeout": 120,
        "desc": "跑分交叉验证（秩相关 + 离散度）",
        "produces": [],
        "depends": ["apply_bench"],
    },
    "notes": {
        "script": "derive_notes.py",
        "where": "tools/agent",
        "args": ["--write"],
        "timeout": 120,
        "desc": "从规格推导客观提示（内存/重量/显卡/能效）",
        "produces": ["data/machine_pool.json"],
        # 它只读池子里的规格字段推导，**不依赖价格抓取**。
        # 首版写成 depends=["apply_prices"]，导致 quick 预设整条链被跳过。
        "depends": [],
    },
    "extra": {
        "script": "fill_extra_fields.py",
        "where": "tools/agent",
        "args": ["--write"],
        "timeout": 120,
        # 2026-11-16：用户反馈「提示里能追问保修几年，问了答不知道」。
        # 查下来参数页里 60 台有「质保时间」，抓回来了却没映射进池。
        # 这个脚本补 15 个维度：保修/续航/厚度/触控/系统/网络/音频/键盘/材质/上市。
        "desc": "补录保修/续航/触控等维度（参数页已有但没映射）",
        "produces": ["data/machine_pool.json"],
        "depends": [],
    },
    "fix_weight": {
        "script": "fix_weight_unit.py",
        "where": "tools/agent",
        "args": ["--apply"],
        "timeout": 60,
        # 2026-11-16 实测抓到 7 台「克当成千克」（992kg / 1000kg）。
        # 这种数字出现在回答里（「同品类最重 1000 kg」）会毁掉可信度，
        # 所以放进刷新链路，每次刷新都顺手体检一遍。
        "desc": "修重量单位错（克被当成千克）",
        "produces": ["data/machine_pool.json"],
        "depends": [],
    },
    "stats": {
        "script": "refresh_stats.py",
        "where": "tools",
        "args": ["--apply"],
        "timeout": 60,
        "desc": "重算机型统计",
        "produces": ["data/machine_pool.json"],
        "depends": ["notes", "fix_weight", "extra"],
    },
    "coverage": {
        "script": "coverage.py",
        "where": "tools",
        "args": [],
        "timeout": 120,
        "desc": "重算 243×10 判定覆盖率",
        "produces": ["data/coverage.json"],
        "depends": ["stats"],
    },
    "pages": {
        "script": "make_pick_page.py",
        "where": "tools",
        "args": [],
        "timeout": 180,
        "desc": "重建静态页面",
        "produces": ["按专业选电脑.html"],
        "depends": ["coverage"],
    },

    # ══════════════════════════════════════════════════════════════
    # 🔴 2026-11-26 新增：装机配件数据（用户要求纳入刷新链路）
    #
    # 为什么单独一组而不是并进 prices：
    #   配件和机型是两套完全独立的数据（parts.json vs machine_pool.json），
    #   抓取逻辑也不同（配件要翻页、要逐个抓单品页）。
    #   混在一起会让"只刷机型价格"变成"顺带刷 459 个配件"，很慢。
    # ══════════════════════════════════════════════════════════════
    "parts": {
        "script": "fetch_parts.py",
        "where": "tools/agent",
        "args": ["--ssd-pages", "5", "--ssd-max", "100"],
        "timeout": 600,
        # 9 类配件：CPU/显卡/内存/主板/机械盘/固态/散热器/电源/机箱
        # 固态频道要翻 5 页 + 逐个抓单品页，是最慢的一环（约 2 分钟）
        "desc": "抓 9 类装机配件价格（实测约 87 秒）",
        "produces": ["data/parts.json"],
        "depends": [],
    },
    "parts_fit": {
        "script": "fetch_fit_params.py",
        "where": "tools/agent",
        "args": ["--limit", "40"],
        "timeout": 600,
        # 抓显卡/机箱/散热器/主板的物理尺寸，用于兼容性校验：
        #   显卡长度 / 机箱显卡限长 / 散热器限高 / 板型支持
        # 每个配件要单独抓一次参数页，所以慢
        "desc": "抓配件尺寸参数（实测约 3 分钟）",
        "produces": ["data/parts_fit.json"],
        "depends": ["parts"],
    },
    "parts_audit": {
        "script": "audit_parts_data.py",
        "where": "tools/agent",
        "args": [],
        "timeout": 60,
        # ⚠️ 只读体检，不改任何文件。
        #    故意不放进 depends —— 它失败不该阻塞数据更新，
        #    只是让用户看到"数据现在什么状况"。
        "desc": "配件数据体检（只读：重复/异常值/覆盖率）",
        "produces": [],
        "depends": [],
    },
}

# 预设：前端按钮给用户选的粒度
#
# 🔴 依赖必须是**闭合**的：一个预设里出现的步骤，它的 depends 也必须在同一预设里，
#    否则会被"依赖未成功"整条跳过（首版 quick 就踩了：notes 依赖 apply_prices，
#    但 quick 里没有 apply_prices，结果三步全被跳过、什么都没干）。
#    下面每个预设都自检过依赖闭合。
PRESETS = {
    # ══════════════════════════════════════════════════════════════
    # 按数据域分三大类：笔记本 / 台式机 / 全部
    #
    # 🔴 2026-11-26（用户要求）：
    #    「全量刷新，分笔记本和台式机吧，各取所需」
    #
    #    笔记本和台式机是**两套完全独立的数据**：
    #      笔记本 → data/machine_pool.json（243 台机型）
    #      台式机 → data/parts.json（459 个配件）
    #    抓取源、脚本、耗时都不同，混在一起刷很浪费。
    # ══════════════════════════════════════════════════════════════

    # ── 笔记本：全量 ──
    # 按依赖顺序：抓价 → 落库 → 抓跑分 → 合并 → 验证
    #            → 派生提示 → 补字段 → 修重量 → 统计 → 覆盖 → 页面
    "laptop": ["prices", "apply_prices", "bench", "apply_bench", "verify",
               "notes", "extra", "fix_weight", "stats", "coverage", "pages"],

    # ── 台式机：全量 ──
    # 配件价格 → 尺寸参数 → 体检
    "desktop": ["parts", "parts_fit", "parts_audit"],

    # ── 全部 ──
    "all": ["prices", "apply_prices", "bench", "apply_bench", "verify",
            "notes", "extra", "fix_weight", "stats", "coverage", "pages",
            "parts", "parts_fit", "parts_audit"],

    # ── 细粒度（各取所需）──
    # 纯本地重算，不联网，约 2 秒
    "quick": ["notes", "fix_weight", "extra", "stats", "coverage", "pages"],
    # 只刷机型价格
    "prices": ["prices", "apply_prices", "notes", "fix_weight", "extra",
               "stats", "coverage"],
    # 只刷跑分
    # （notes 必须在，因为 stats 依赖它；见下方依赖闭合自检）
    "bench": ["bench", "apply_bench", "verify", "notes", "fix_weight", "extra",
              "stats", "coverage"],
    # 只刷配件价格（不含尺寸，最快）
    "parts": ["parts", "parts_audit"],
    # 配件价格 + 尺寸
    "partsAll": ["parts", "parts_fit", "parts_audit"],
}

# ── 任务状态（进程内，单例）─────────────────────────────────────────────
_LOCK = threading.Lock()
_JOBS = {}          # job_id -> {status, steps, startedAt, ...}
_CURRENT = {"job": None}


def _run_step(name):
    """跑一步。返回 {ok, ms, tail, err}。"""
    spec = STEPS[name]
    where = spec.get("where") or "tools"
    cwd = os.path.join(ROOT, where.replace("/", os.sep))
    path = os.path.join(cwd, spec["script"])
    if not os.path.exists(path):
        return {"ok": False, "ms": 0, "err": "脚本不存在: %s/%s"
                % (where, spec["script"])}
    t0 = time.time()
    try:
        p = subprocess.run(
            [sys.executable, path] + spec["args"],
            cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=spec["timeout"])
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        # 只留最后几行，前端展示够用
        tail = "\n".join(out.splitlines()[-6:]) if out else ""
        return {"ok": p.returncode == 0, "ms": int((time.time() - t0) * 1000),
                "tail": tail, "err": err.splitlines()[-1][:200] if err else ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "ms": int((time.time() - t0) * 1000),
                "err": "超时（%ds）" % spec["timeout"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "ms": int((time.time() - t0) * 1000),
                "err": str(e)[:200]}


def _cross_validate():
    """刷新后做一次跨源核对，返回可读报告。

    这里只做**能自动判定**的几项，不做"猜"：

      ① 价格口径：池里有多少台是到手价、多少台是快照价
      ② 跑分一致性：R23 与 GB6 的秩相关（verify_bench 已算，这里读结论）
      ③ 字段冲突：同一台机器「参数页规格」与「价格页规格」不一致的
      ④ 覆盖：各字段还缺多少
    """
    import importlib.util

    def _load(path, modname):
        spec = importlib.util.spec_from_file_location(modname, path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    report = {"checkedAt": datetime.now().strftime("%Y-%m-%d %H:%M"), "items": []}
    try:
        core = _load(os.path.join(HERE, "core.py"), "core_rv")
        offs = core.pool_offers()

        real = sum(1 for o in offs if not o.get("priceIsEstimate"))
        snap = sum(1 for o in offs if o.get("priceIsEstimate"))
        report["items"].append({
            "kind": "price",
            "level": "ok",
            "text": "价格：%d 台到手价、%d 台平台快照价" % (real, snap),
        })

        # 跑分：读 verify_bench 的结论（它已经算好 rho）
        bench_path = os.path.join(DATA, "bench.json")
        if os.path.exists(bench_path):
            b = json.load(open(bench_path, encoding="utf-8"))
            n_r23 = sum(1 for c in b.get("cpus") or []
                        if ((c.get("bench") or {}).get("r23") or {}).get("multi"))
            n_gb = sum(1 for c in b.get("cpus") or []
                       if (c.get("bench") or {}).get("gb6")
                       or (c.get("bench") or {}).get("geekbench6"))
            both = sum(1 for c in b.get("cpus") or []
                       if ((c.get("bench") or {}).get("r23") or {}).get("multi")
                       and ((c.get("bench") or {}).get("gb6")
                            or (c.get("bench") or {}).get("geekbench6")))
            report["items"].append({
                "kind": "bench",
                "level": "ok" if both >= 10 else "warn",
                "text": "跑分：R23 覆盖 %d 颗、GB6 %d 颗、可做交叉验证 %d 颗"
                        % (n_r23, n_gb, both),
            })

        # 字段缺口
        # 🔴 重量必须走 `core.effective_weight()`，不许直接判 `o["weightKg"]`。
        #    `test_weight.py` 的 W8 是结构性检查：出口层出现裸 weightKg 就报错
        #    （理由：重量有四个出口，各读各的字段迟早漂）。
        #    这里虽然是"统计缺口"，也算出口 —— 统一走入口。
        def miss(f):
            return sum(1 for o in offs if o.get(f) in (None, "", [], {}))

        gaps = {
            "perf": miss("perf"),
            "weightKg": sum(1 for o in offs
                            if core.effective_weight(o) is None),
            "price": miss("price"),
        }
        worst = max(gaps.items(), key=lambda kv: kv[1])
        report["items"].append({
            "kind": "gaps",
            "level": "ok" if worst[1] < len(offs) * 0.4 else "warn",
            "text": "缺口：" + "、".join("%s 缺 %d" % (k, v) for k, v in gaps.items()),
        })

        # 覆盖：有 derivedNotes 的比例（用户问缺点能不能答）
        dn = sum(1 for o in offs if o.get("derivedNotes"))
        report["items"].append({
            "kind": "notes",
            "level": "ok" if dn >= len(offs) * 0.9 else "warn",
            "text": "可答缺点：%d / %d 台" % (dn, len(offs)),
        })

        # 2026-11-16 新增：保修/续航这类"用户会追问但以前答不知道"的维度
        wy = sum(1 for o in offs if o.get("warrantyYears"))
        bl = sum(1 for o in offs if o.get("batteryLifeH"))
        # 2026-11-17 第二批：充电/颜色/触控板/屏幕技术
        ad = sum(1 for o in offs if o.get("adapterW"))
        sc = sum(1 for o in offs if o.get("shellColor"))
        report["items"].append({
            "kind": "warranty",
            "level": "ok" if wy else "warn",
            "text": "可答保修：%d 台有质保年数；可答续航：%d 台" % (wy, bl),
        })
        report["items"].append({
            "kind": "extra",
            "level": "ok" if ad >= len(offs) * 0.3 else "warn",
            "text": "可答充电：%d 台；可答颜色：%d 台" % (ad, sc),
        })

        # 2026-11-17：价格多来源交叉验证。
        # 不做进抓取链路（那要跑一遍全量），只读现有快照算偏差 —— 很快。
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "pcc", os.path.join(HERE, "price_cross_check.py"))
            pcc = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(pcc)
            pool = json.load(open(os.path.join(DATA, "machine_pool.json"),
                                  encoding="utf-8"))
            machines = pool["machines"]
            bad = pcc.find_unreliable_sources(machines)
            rows = [r for r in (pcc.analyze(m, exclude=bad) for m in machines) if r]
            n_sus = sum(1 for r in rows if r["level"] == "suspect")
            report["items"].append({
                "kind": "priceCross",
                "level": "warn" if n_sus > 10 else "ok",
                "text": "价格交叉验证：%d 台可核对，%d 台偏差 >30%%（需人看）"
                        % (len(rows), n_sus)
                        + ("；已排除不可靠来源：%s" % "、".join(sorted(bad)) if bad else ""),
            })
        except Exception as e:  # noqa: BLE001
            report["items"].append({"kind": "priceCross", "level": "error",
                                    "text": "价格交叉验证失败：%s" % str(e)[:120]})
    except Exception as e:  # noqa: BLE001
        report["items"].append({"kind": "error", "level": "error",
                                "text": "核对失败：%s" % str(e)[:160]})
    return report


def _worker(job_id, steps):
    """后台跑完整条链。"""
    job = _JOBS[job_id]
    try:
        for name in steps:
            spec = STEPS[name]
            job["current"] = name
            job["steps"][name] = {"status": "running", "desc": spec["desc"]}
            # 依赖没成功就跳过（避免在坏数据上继续算）
            deps = spec.get("depends") or []
            bad = [d for d in deps
                   if not (job["steps"].get(d) or {}).get("ok")]
            if bad:
                job["steps"][name] = {"status": "skipped", "desc": spec["desc"],
                                      "err": "依赖未成功：%s" % "、".join(bad)}
                continue
            r = _run_step(name)
            job["steps"][name] = dict(r, status="ok" if r["ok"] else "fail",
                                      desc=spec["desc"])
        # 全部跑完做交叉验证
        job["current"] = "_cross"
        job["cross"] = _cross_validate()
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["err"] = str(e)[:300]
    finally:
        job["finishedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        job["current"] = None
        with _LOCK:
            _CURRENT["job"] = None


def start_refresh(steps=None, preset=None):
    """启动一次刷新。返回 {ok, jobId, steps} 或 {ok:False, err}。"""
    if preset:
        names = PRESETS.get(preset)
        if not names:
            return {"ok": False, "err": "未知预设：%s（可用 %s）"
                    % (preset, "、".join(PRESETS))}
    elif steps:
        names = []
        for s in steps:
            if s not in STEPS:
                return {"ok": False, "err": "未知步骤：%s（可用 %s）"
                        % (s, "、".join(STEPS))}
            names.append(s)
    else:
        return {"ok": False, "err": "要给 steps 或 preset"}

    with _LOCK:
        # 同一时刻只允许一个刷新，避免两个进程同时写 machine_pool.json
        if _CURRENT["job"]:
            cur = _JOBS.get(_CURRENT["job"]) or {}
            return {"ok": False, "err": "已有刷新在进行中（%s）" % cur.get("current"),
                    "runningJob": _CURRENT["job"]}
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "id": job_id, "status": "running",
            "startedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "steps": {}, "order": names, "current": None, "cross": None,
        }
        _CURRENT["job"] = job_id

    t = threading.Thread(target=_worker, args=(job_id, names), daemon=True)
    t.start()
    return {"ok": True, "jobId": job_id, "steps": names,
            "desc": [STEPS[n]["desc"] for n in names]}


def refresh_status(job_id=None):
    """查刷新进度。job_id 为空时返回最近一次。"""
    if job_id:
        job = _JOBS.get(job_id)
    else:
        job = _JOBS.get(_CURRENT["job"]) if _CURRENT["job"] else (
            list(_JOBS.values())[-1] if _JOBS else None)
    if not job:
        return {"ok": False, "err": "没有刷新记录"}
    # 算进度百分比（按步骤数）
    total = len(job.get("order") or []) or 1
    done = sum(1 for v in (job.get("steps") or {}).values()
               if v.get("status") in ("ok", "fail", "skipped"))
    return {"ok": True, "job": dict(job, progress=round(100.0 * done / total))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="", help="逗号分隔的步骤名")
    ap.add_argument("--preset", default="", help="quick/prices/bench/full")
    ap.add_argument("--dry", action="store_true", help="只列出将要跑什么")
    args = ap.parse_args()

    if args.dry:
        names = PRESETS.get(args.preset) if args.preset else (
            [s for s in args.steps.split(",") if s] or PRESETS["quick"])
        print("将要执行：")
        for n in names:
            spec = STEPS.get(n)
            if not spec:
                print("  ✗ 未知步骤 %s" % n)
                continue
            print("  %-14s %-40s 超时%ds" % (n, spec["desc"], spec["timeout"]))
        return

    steps = [s for s in args.steps.split(",") if s] or None
    r = start_refresh(steps=steps, preset=args.preset or None)
    if not r.get("ok"):
        print("启动失败：%s" % r.get("err"))
        return
    print("已启动 job %s（%d 步）" % (r["jobId"], len(r["steps"])))
    # 命令行模式下等它跑完
    while True:
        st = refresh_status(r["jobId"])["job"]
        cur = st.get("current")
        print("  [%3d%%] %s" % (st.get("progress", 0), cur or "完成"))
        if st.get("status") != "running":
            break
        time.sleep(2)
    print()
    print("=== 结果 ===")
    for n in st.get("order") or []:
        v = (st.get("steps") or {}).get(n) or {}
        mark = {"ok": "✓", "fail": "✗", "skipped": "-"}.get(v.get("status"), "?")
        print("  %s %-14s %6dms  %s" % (mark, n, v.get("ms", 0),
                                        (v.get("err") or "")[:60]))
    print()
    print("=== 交叉验证 ===")
    for it in (st.get("cross") or {}).get("items") or []:
        print("  [%s] %s" % (it.get("level"), it.get("text")))


if __name__ == "__main__":
    main()
