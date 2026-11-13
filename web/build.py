# -*- coding: utf-8 -*-
"""
构建只读前端：读取 data/*.json → 生成 web/index.html（单文件，双击可开）
不修改任何数据文件，只做读取与裁剪。
用法： python web/build.py
"""
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
TPL = os.path.join(ROOT, "web", "template.html")
OUT = os.path.join(ROOT, "web", "index.html")


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def pick(d, keys):
    return {k: d.get(k) for k in keys if k in d}


def slim_machine(m):
    out = {
        "id": m.get("id"),
        "name": m.get("name"),
        "brand": m.get("brand"),
        "cat": m.get("cat"),
        "storageGB": m.get("storageGB"),
        "batteryWh": m.get("batteryWh"),
        "weightKg": m.get("weightKg"),
        "efficiency": m.get("efficiency"),
        "efficiencyStatus": m.get("efficiencyStatus"),
        "confidence": m.get("confidence"),
        "priceCapturedAt": m.get("priceCapturedAt"),
        "shopName": m.get("shopName"),
        "shopType": m.get("shopType"),
        "url": m.get("url"),
        "dataGaps": m.get("dataGaps") or [],
    }
    cpu = m.get("cpu") or {}
    out["cpu"] = pick(cpu, ["model", "vendor", "cores", "threads"])
    ram = m.get("ram") or {}
    out["ram"] = pick(ram, ["gb", "type", "form", "slots", "maxGB", "config"])
    gpu = m.get("gpu") or {}
    out["gpu"] = pick(gpu, ["model", "vendor", "type", "vramGB", "vramType"])
    sc = m.get("screen") or {}
    out["screen"] = pick(sc, ["sizeInch", "widthPx", "heightPx", "refreshHz", "panel", "colorGamut", "brightnessNit"])
    p = m.get("price") or {}
    out["price"] = {
        "list": p.get("list"),
        "net": p.get("net"),
        "subsidy": p.get("subsidy"),
        "final": p.get("final"),
        "basisNote": p.get("basisNote"),
        "discounts": [
            ({"name": d.get("name"), "amount": d.get("amount")} if isinstance(d, dict) else {"name": str(d), "amount": 0})
            for d in (p.get("discounts") or [])
        ],
    }
    th = m.get("throttle")
    if isinstance(th, dict):
        out["throttle"] = pick(th, ["cpuSustainedW", "gpuSustainedW", "totalSustainedW", "ratedTgpW",
                                    "sustainedNote", "note", "source"])
    else:
        out["throttle"] = None
    out["ports"] = m.get("ports") or []
    return out


def slim_group(g):
    req = dict(g.get("req") or {})
    req = {k: req.get(k) for k in ("ramMin", "ramRec", "vramMin", "vramRec", "gpu",
                                   "storageMinGB", "cpu", "basis", "rationale",
                                   "platformUnfit", "portNeeds", "preferActiveCooling") if k in req}
    majors = []
    for m in g.get("majors") or []:
        if isinstance(m, str):
            majors.append(m)
        elif isinstance(m, dict):
            majors.append({"name": m.get("name"), "level": m.get("level")})
    return {
        "id": g.get("id"),
        "name": g.get("name"),
        "tagline": g.get("tagline"),
        "stackText": g.get("stackText"),
        "software": g.get("software") or [],
        "majors": majors,
        "req": req,
        "hardGates": g.get("hardGates") or [],
        "mode": g.get("mode"),
        "preferCats": g.get("preferCats") or [],
        "followups": g.get("followups") or [],
        "trap": g.get("trap"),
        "evidence": g.get("evidence"),
    }


def main():
    prices = load("prices.json")
    pool = load("machine_pool.json")
    majors = load("majors.json")
    coverage = load("coverage.json")

    pm = prices.get("meta", {})
    data = {
        "meta": {
            "version": pm.get("version"),
            "pricesUpdated": pm.get("updated"),
            "poolUpdated": (pool.get("meta") or {}).get("updated"),
            "scope": pm.get("scope"),
            "priceBasis": pm.get("priceBasis"),
            "perfBasis": pm.get("perfBasis"),
            "disclaimer": pm.get("disclaimer"),
            "subsidyNote": pm.get("subsidyNote"),
        },
        "sources": [pick(s, ["id", "title", "publisher", "date", "url"]) for s in prices.get("sources", [])],
        "categoryChange": prices.get("categoryChange", []),
        "dramQoQ": prices.get("dramQoQ", []),
        "sameModel": [pick(s, ["kind", "name", "before", "after", "change"]) for s in prices.get("sameModel", [])],
        "partsChange": prices.get("partsChange", []),
        "decision": prices.get("decision", []),
        "budgetBands": prices.get("budgetBands", []),
        "defects": {
            "title": (prices.get("defects") or {}).get("title"),
            "cap": (prices.get("defects") or {}).get("cap"),
            "items": [pick(i, ["name", "symptom", "who", "why", "fix"]) for i in (prices.get("defects") or {}).get("items", [])],
        },
        "trapsByKind": prices.get("trapsByKind", {}),
        "subsidyRules": prices.get("subsidyRules", {}),
        "subsidyFacts": prices.get("subsidyFacts", []),
        "outlook": prices.get("outlook", {}),
        "groups": [slim_group(g) for g in majors.get("groups", [])],
        "majorAliases": majors.get("majorAliases", {}),
        "majorsMeta": pick(majors.get("meta", {}), ["version", "updated", "scope", "basis", "coverage", "disclaimer"]),
        "machines": [slim_machine(m) for m in pool.get("machines", []) if (m.get("price") or {}).get("final")],
        "coverage": coverage.get("totals", {}),
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # 防止意外闭合 script 标签
    blob = blob.replace("</", "<\\/")

    with open(TPL, encoding="utf-8") as f:
        tpl = f.read()
    if "__DATA__" not in tpl:
        sys.exit("模板缺少 __DATA__ 占位符")
    html = tpl.replace("__DATA__", blob)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)

    kb = os.path.getsize(OUT) / 1024
    n_best = len(data["machines"])
    print("已生成 web/index.html  %.1f KB" % kb)
    print("机型 %d 台 / 专业组 %d 个 / 专业 %d 个 / 来源 %d 条" % (
        n_best, len(data["groups"]),
        sum(len(g["majors"]) for g in data["groups"]),
        len(data["sources"])))


if __name__ == "__main__":
    main()
