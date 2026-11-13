import json
import os
import sys
import time
import traceback
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent

BASE = os.path.dirname(os.path.abspath(__file__))

# ── 运行配置（2026-11-13 为"别人连我这台"改的，全部走环境变量）─────────
# 原来 PORT 写死 7860、只绑 127.0.0.1 —— 组内其他人和测试同学都连不上。
# 现在：
#   PORT        换个端口
#   HOST        0.0.0.0 = 允许外部访问（等同 --lan）
#   ACCESS_CODE 设了就要带访问码（公网暴露时**务必设**，否则谁都能刷你的显卡）
#   MODEL / OLLAMA_HOST  见 agent.py
PORT = int(os.environ.get("PORT", "7860") or 7860)
HOST = (os.environ.get("HOST") or "").strip()
ACCESS_CODE = (os.environ.get("ACCESS_CODE") or "").strip()

LOG_DIR = os.path.join(BASE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def _clean_uid(v):
    """客户端标识：只留字母数字和 -_，最长 32。

    为什么要有：测试同学和我都连同一台服务，日志里必须分得清是谁的一轮，
    否则收上来的样本没法按人排查（也没法回滚某个人的坏数据）。
    不做成"账号"——不收集任何个人信息，就是个随机串，用户自己也可以改成昵称。
    """
    v = (v or "").strip()
    v = "".join(ch for ch in v if ch.isalnum() or ch in "-_")
    return v[:32] or "anon"


def log_event(rec):
    """一次请求一行 JSON，方便按时间 grep / jq 排查。"""
    rec["ts"] = datetime.datetime.now().isoformat(timespec="milliseconds")
    path = os.path.join(LOG_DIR, "chat-%s.log" % datetime.date.today().isoformat())
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # 控制台也打一行，方便看实时
    flag = "!" if rec.get("issues") or not rec.get("ok", True) else " "
    print("%s %-9s %-8s %s" % (flag, rec.get("stage", "-"), rec.get("ms", "-"), (rec.get("text") or "")[:40]))


def slim_result(r):
    out = {"stage": r.get("stage"), "reply": r.get("reply", ""), "slots": r.get("slots", {}),
           "asked": r.get("asked", []), "focus": r.get("focus"),
           "focusName": r.get("focusName")}
    # 后端给的「下一步可以做什么」按钮。前端渲染成 chips 直接发，
    # 不让用户打「1/2/3」——那正是 2026-11-12 死循环的入口。
    if r.get("suggest"):
        out["suggest"] = r["suggest"]
    if r.get("stage") == "recommend":
        res = r.get("result", {})
        # 固定字段版回答（2026-11-13）：前端主视图改成"结论 + 字段卡"，
        # 正文 reply 退到「详细说明」折叠里。两边的数字同源于 result。
        if r.get("answer"):
            out["answer"] = r["answer"]
        out["kernel"] = {
            "mode": r.get("mode"),
            "dataAsOf": r.get("dataAsOf"),
            "warnings": r.get("warnings", []),
            "questions": r.get("questions", []),
            "untraceable": r.get("untraceable_numbers", []),
            "outside": r.get("outside_products", []),
            "fabricated": r.get("fabricated_words", []),
            "ranked": [{
                "id": x.get("id"), "name": x["name"], "price": x["price"], "spec": x["spec"],
                "perf": x["perf"], "upgrade": x["upgrade"], "portable": x["portable"],
                "tag": x["tag"], "caution": x["caution"],
                "original": (x["subsidy"] or {}).get("original"),
                "subsidy": (x["subsidy"] or {}).get("subsidy"),
                "sources": x.get("sources", []),
                "overBudget": x.get("overBudget"),
                # 用途硬伤 + 缺数据标记：前端要标出来，不能只靠正文那句话
                "blockers": x.get("blockers") or [],
                "noPerfData": x.get("noPerfData"),
                # 🔴 2026-11-12 给前端补的三个字段：
                #   hasReview=False 的机器只能报参数，卡片上必须有"仅参数"标识，
                #   否则用户会把它当成"推荐度最高的好机器"（库里 97 台里 75 台是这种）。
                "hasReview": bool(x.get("hasReview")),
                "weightKg": x.get("weightKg"),
                "ram": x.get("ram"),
            } for x in res.get("ranked", [])],
        }
    elif r.get("stage") == "clarify":
        out["missing"] = r.get("missing", [])
    elif r.get("stage") == "followup":
        out["focus"] = r.get("focus")
        out["outside"] = r.get("outside_products", [])
        out["dataAsOf"] = r.get("dataAsOf")
    # 追问 / 把不匹配说清 / 对比 / 翻完 —— 这些回答只有一两句事实，
    # 也套同一个"结论 + 字段"的壳（2026-11-13：用户要求每次回答都是规范格式）。
    # ⚠️ 这里**不新造任何事实**：headline 就是正文本身，rows 只补"说的是哪台"。
    if r.get("stage") in ("followup", "fit", "compare", "exhausted") and r.get("reply"):
        rows = []
        if r.get("focusName"):
            rows.append({"k": "机型", "v": r["focusName"]})
        if r.get("dataAsOf"):
            rows.append({"k": "数据快照", "v": str(r["dataAsOf"])})
        out["answer"] = {"headline": r["reply"], "rows": rows, "gaps": [],
                         "blocks": [], "candidates": [], "warnings": [],
                         "inline": True}
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ── 访问控制（2026-11-13）─────────────────────────────────────────
    # 公网暴露时服务端要有一道门。做法刻意简单：一个共享访问码，
    # 前端从 ?k=xxx 拿到后存 localStorage，之后每次请求带 X-Access-Code 头。
    # 不是账号体系（PRD 明确不做），只是"别让陌生人拿你的显卡当免费 API"。
    def _uid(self, qs=None):
        u = self.headers.get("X-Client-Id")
        if not u:
            try:
                u = (qs or {}).get("u", [""])[0]
            except Exception:
                u = ""
        return _clean_uid(u)

    def _gate(self, qs=None):
        """返回 True 表示放行；不放行时已经回过 401。"""
        if not ACCESS_CODE:
            return True
        given = self.headers.get("X-Access-Code") or ""
        if not given:
            try:
                given = (qs or {}).get("k", [""])[0]
            except Exception:
                given = ""
        if given == ACCESS_CODE:
            return True
        self._send(401, json.dumps(
            {"error": "需要访问码", "hint": "用带 ?k=访问码 的链接打开"},
            ensure_ascii=False))
        return False

    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        # 页面本身不挡（挡了连输入访问码的机会都没有），只挡数据接口
        if path.startswith("/api/") and not self._gate(qs):
            return
        if path in ("/", "/index.html"):
            with open(os.path.join(BASE, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/health":
            try:
                import urllib.request
                with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=5) as r:
                    v = json.loads(r.read().decode()).get("version")
                self._send(200, json.dumps({"ok": True, "ollama": v}))
            except Exception as e:
                self._send(200, json.dumps({"ok": False, "err": str(e)[:80]}))
        elif path == "/api/stats":
            # 演示页顶部要显示"库里到底有多少台" —— 这个数不能写死在 HTML 里，
            # 否则数据一更新页面就说谎。这里现算。
            #
            # 🔴 2026-11-12 起用 **merged_offers()**（人工层 + 京东采集池），
            # 不再是只用 prices.json 那 22 台。用户看到的"只有 22 台"就是这个差别。
            # 同时必须把「其中多少台只有参数、没人写过评价」一起报出去 ——
            # 只报总数会让用户以为 100 台都能答"值不值得买"。
            try:
                offers = agent.core.merged_offers()
                ms = agent.core.merge_stats()
                by_cat = {}
                for o in offers:
                    k = o.get("cat")
                    by_cat[k] = by_cat.get(k, 0) + 1
                self._send(200, json.dumps({
                    "ok": True,
                    "total": len(offers),
                    "reviewed": ms.get("base"),
                    "noReview": ms.get("poolNoReview"),
                    "withPerf": sum(1 for o in offers if o.get("perf") is not None),
                    "noPerf": sum(1 for o in offers if o.get("perf") is None),
                    "priceConflicts": ms.get("priceConflicts") or [],
                    "byCat": [{"cat": k, "name": agent.core.CAT_NAMES.get(k, k),
                               "n": by_cat[k]} for k in
                              ("thin", "gaming", "desktop", "upgrade") if k in by_cat],
                    "dataAsOf": agent.core.data_as_of(),
                }, ensure_ascii=False))
            except Exception as e:
                self._send(200, json.dumps({"ok": False, "err": str(e)[:120]},
                                           ensure_ascii=False))
        elif path == "/api/model":
            # 单台详情：右侧「当前这台」面板用（2026-11-13）。
            # 为什么单独开一个口子，而不是让前端从上一轮的 ranked 里翻：
            #   点名问某台时（「MateBook 14 怎么样」）它根本不在这轮推荐列表里，
            #   前端手里没有它的字段，硬凑就会错位。
            # 数字全部来自 core.brief / purpose_blockers，这里只做取舍不做计算。
            try:
                qs = parse_qs(urlparse(self.path).query)
                mid = (qs.get("id") or [""])[0]
                purpose = []
                for p in (qs.get("purpose") or []):
                    purpose += [x for x in p.split(",") if x]
                o = agent.core.get_offer(mid)
                if not o:
                    self._send(200, json.dumps({"ok": False, "err": "库里没有这台"},
                                               ensure_ascii=False))
                    return
                b = agent.core.brief(o, purpose or None)
                # 🔴 caution 必须走出口清洗，否则右栏会把「小新 16SE 更值得看」
                #   这种讲别家的句子原样贴出来 —— 那正是 2026-11-12 修过的串台。
                b["caution"] = agent.core.clean_caution(o.get("caution"), o)
                b["blockers"] = agent.core.purpose_blockers(o, purpose, include_soft=True)
                b["hasReview"] = bool(o.get("hasReview"))
                b["weightKg"] = o.get("weightKg")
                b["ram"] = agent.core.ram_of(o)
                b["perf"] = o.get("perf")
                b["upgrade"] = o.get("upgrade")
                b["priceBasis"] = o.get("priceBasis")
                self._send(200, json.dumps({"ok": True, "model": b}, ensure_ascii=False))
            except Exception as e:
                self._send(200, json.dumps({"ok": False, "err": str(e)[:120]},
                                           ensure_ascii=False))
        elif path == "/api/overview":
            # 可视数据专用。**所有数字在这里算好**，前端只负责画 ——
            # 前端自己 group by / 求中位数，等于把"数字零写权限"从模型挪到了 JS 里，
            # 一样会算错、而且没人审计。
            # 只允许统计**库里真有的字段**；续航、帧数、返修率这类没有的维度，
            # 这里不返回，前端也就不许画（宪法第 2 条：没数据不能说成 0）。
            try:
                offers = agent.core.merged_offers()
                cats = ("thin", "gaming", "desktop", "upgrade")

                def _med(vals):
                    vals = sorted(vals)
                    n = len(vals)
                    if not n:
                        return None
                    mid = n // 2
                    return (vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0)

                by_cat = []
                for k in cats:
                    rows = [o for o in offers if o.get("cat") == k]
                    if not rows:
                        continue
                    prices = [o["price"] for o in rows if o.get("price")]
                    by_cat.append({
                        "cat": k, "name": agent.core.CAT_NAMES.get(k, k),
                        "n": len(rows),
                        "reviewed": sum(1 for o in rows if o.get("hasReview")),
                        "priceMin": min(prices) if prices else None,
                        "priceMax": max(prices) if prices else None,
                        "priceMedian": _med(prices),
                        "withWeight": sum(1 for o in rows if o.get("weightKg")),
                    })

                edges = [(0, 4000), (4000, 5000), (5000, 6000), (6000, 7000),
                         (7000, 8000), (8000, 10000), (10000, 10 ** 9)]
                labels = ["4000 以下", "4000–5000", "5000–6000", "6000–7000",
                          "7000–8000", "8000–10000", "1 万以上"]
                buckets = []
                for (lo, hi), lab in zip(edges, labels):
                    rows = [o for o in offers
                            if o.get("price") and lo <= o["price"] < hi]
                    buckets.append({"label": lab, "min": lo,
                                    "max": None if hi > 10 ** 8 else hi,
                                    "n": len(rows),
                                    "reviewed": sum(1 for o in rows if o.get("hasReview"))})

                weights = [o["weightKg"] for o in offers if o.get("weightKg")]
                ups = {}
                for o in offers:
                    u = o.get("upgrade")
                    if u is not None:
                        ups[int(u)] = ups.get(int(u), 0) + 1

                self._send(200, json.dumps({
                    "ok": True,
                    "dataAsOf": agent.core.data_as_of(),
                    "total": len(offers),
                    "review": {"reviewed": sum(1 for o in offers if o.get("hasReview")),
                               "noReview": sum(1 for o in offers if not o.get("hasReview"))},
                    "perf": {"withPerf": sum(1 for o in offers if o.get("perf") is not None),
                             "noPerf": sum(1 for o in offers if o.get("perf") is None)},
                    "weight": {"withWeight": len(weights),
                               "noWeight": len(offers) - len(weights),
                               "min": min(weights) if weights else None,
                               "max": max(weights) if weights else None,
                               # ⚠️ 人工层 22 台没有 kg，只有 1-3 档便携分 —— 不能混进来算
                               "note": "只有采集池记了实测重量；人工层没有，不参与统计"},
                    "byCat": by_cat,
                    "priceBuckets": buckets,
                    "upgrade": [{"score": k, "n": ups[k]} for k in sorted(ups)],
                    "priceConflicts": (agent.core.merge_stats().get("priceConflicts") or []),
                }, ensure_ascii=False))
            except Exception as e:
                self._send(200, json.dumps({"ok": False, "err": str(e)[:160]},
                                           ensure_ascii=False))
        elif path == "/api/demo":
            # 演示话术脚本。放后端而不是写死在前端，
            # 是为了让"页面上的示例"和"模型实际能答对的问题"永远来自同一份清单。
            self._send(200, json.dumps({
                "ok": True,
                "acts": [
                    {"title": "从零开始推荐",
                     "note": "四句话以内拿到一台具体机型 + 短板 + 到手价",
                     "turns": ["学设计的，预算 5100，要带去教室"]},
                    {"title": "追问细节不编",
                     "note": "库里没写保修年限，系统明说没有并给出着落",
                     "turns": ["学设计的，预算 5100，要带去教室", "这机器保修几年"]},
                    {"title": "问「这台行不行」",
                     "note": "不换品类、不改条件，只判用途匹配度",
                     "turns": ["学设计的，预算 5100，要带去教室", "这台行不行"]},
                    {"title": "指名问型号",
                     "note": "用户直接点名，系统认出是哪台并切过去",
                     "turns": ["华为MateBook D16怎么样"]},
                    {"title": "库里没有的型号",
                     "note": "不硬凑、不拿别的机器糊过去",
                     "turns": ["ThinkBook 14 怎么样"]},
                    {"title": "价格位置由代码算",
                     "note": "不在市场里瞎比，只报库内排名",
                     "turns": ["学设计的，预算 5100，要带去教室",
                               "这个价在市场上算什么水平"]},
                ]}, ensure_ascii=False))
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if urlparse(self.path).path != "/api/chat":
            self._send(404, "not found", "text/plain")
            return
        _qs = parse_qs(urlparse(self.path).query)
        if not self._gate(_qs):
            return
        t0 = time.time()
        req = {}
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            text = (body.get("text") or "").strip()
            known = body.get("known") or None
            asked = body.get("asked") or None
            last_stage = body.get("last_stage")
            seen = body.get("seen") or None
            focus = body.get("focus") or None
            req = {"text": text, "known": known, "asked": asked,
                   "last_stage": last_stage, "seen": seen, "focus_in": focus,
                   "uid": self._uid(_qs)}
            if not text:
                self._send(400, json.dumps({"error": "empty text"}))
                return
            r = agent.run(text, known=known, asked=asked, last_stage=last_stage,
                          seen=seen, focus=focus)

            # 记录这一轮发生了什么，重点标出可疑项
            issues = []
            if r.get("untraceable_numbers"):
                issues.append("数字不可追溯:%s" % r["untraceable_numbers"])
            if r.get("outside_products"):
                issues.append("库外机型:%s" % r["outside_products"])
            if r.get("fabricated_words"):
                issues.append("疑似编造:%s" % r["fabricated_words"])
            if r.get("stage") == "slot_error":
                issues.append("槽位解析失败")
            if r.get("stage") == "exhausted":
                issues.append("候选已翻完")

            rec = dict(req)
            rec.update({
                "ok": True,
                "ms": int((time.time() - t0) * 1000),
                "stage": r.get("stage"),
                "slots": r.get("slots"),
                "asked_out": r.get("asked"),
                "focus_out": r.get("focus"),
                "focusName_out": r.get("focusName"),
                "picked": r.get("picked") or (r.get("ranked")[0]["name"] if r.get("ranked") else None),
                "reply": (r.get("reply") or "")[:600],
                "warnings": r.get("warnings"),
                "issues": issues,
            })
            if r.get("result", {}).get("ranked"):
                rec["ranked"] = [(x["name"], x["price"]) for x in r["result"]["ranked"]]
            log_event(rec)

            self._send(200, json.dumps(slim_result(r), ensure_ascii=False))
        except Exception as e:
            rec = dict(req)
            rec.update({"ok": False, "ms": int((time.time() - t0) * 1000),
                        "error": str(e)[:300],
                        "trace": traceback.format_exc()[-900:],
                        "issues": ["异常"]})
            log_event(rec)
            self._send(500, json.dumps({"error": str(e)[:200]}, ensure_ascii=False))


if __name__ == "__main__":
    import argparse
    import socket

    ap = argparse.ArgumentParser()
    ap.add_argument("--lan", action="store_true",
                    help="允许同一局域网的其他设备访问（默认只允许本机）")
    ap.add_argument("--host", default="", help="监听地址，如 0.0.0.0")
    ap.add_argument("--port", type=int, default=0, help="端口，默认 7860")
    args = ap.parse_args()

    # 优先级：命令行 > 环境变量 HOST > --lan > 只绑本机
    host = args.host or HOST or ("0.0.0.0" if args.lan else "127.0.0.1")
    if args.port:
        PORT = args.port

    def lan_ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "查不到"
        finally:
            s.close()

    print("=" * 56)
    print("电脑选机助手 · 测试版")
    print("本机访问   http://127.0.0.1:%d" % PORT)
    if host == "0.0.0.0":
        print("局域网访问 http://%s:%d" % (lan_ip(), PORT))
        print("注意：同一网络下任何人打开这个地址都能用，且会占用你的显卡")
        if ACCESS_CODE:
            print("访问码     %s   （链接要带 ?k=%s）" % (ACCESS_CODE, ACCESS_CODE))
        else:
            print("⚠️  当前没有访问码。只在可信局域网这样做；")
            print("   要放到公网，先设 ACCESS_CODE 环境变量再启动。")
    else:
        print("（只允许本机访问。想让别人测，加参数 --lan 重启）")
    print("模型       %s @ %s" % (agent.MODEL, agent.API))
    print("日志       %s" % LOG_DIR)
    print("按 Ctrl+C 停止")
    print("=" * 56)
    ThreadingHTTPServer((host, PORT), H).serve_forever()
