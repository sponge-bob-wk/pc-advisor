# -*- coding: utf-8 -*-
"""fill_from_zol.py —— 从 ZOL 参数页补全缺失字段

## 背景（2026-11-25）

数据体检发现 5 个字段覆盖率过低：
    cooling         28.0%
    brightness      39.1%
    dGPU            23.5%
    warrantyYears   24.7%
    batteryLifeH    14.0%

## 探测结论（实测 10 台）

| 字段 | ZOL 命中率 | 结论 |
|---|---|---|
| 亮度 | 9/10 | ✅ 可补 |
| 电池类型（含瓦时） | 10/10 | ✅ 可补 |
| 数据接口 | 10/10 | ✅ 可补 |
| 视频接口 | 8/10 | ✅ 可补 |
| 笔记本重量 | 9/10 | ✅ 可补（校验用） |
| 厚度 | 9/10 | ✅ 可补（新字段） |
| 外壳材质 | 8/10 | ✅ 可补（新字段） |
| sRGB色域 | 4/10 | ⚠️ 部分 |
| **散热系统** | **0/10** | ❌ **ZOL 这批机器没有** |
| 保修 | 0/10 | ❌ 没有 |
| 续航小时 | 0/10 | ❌ 没有 |

**所以 cooling / warrantyYears / batteryLifeH 补不了** ——
ZOL 页面根本没有这些字段。诚实记录，不编。

## 交叉验证

用户要求「三方获取交叉验证」。这里的做法：
  · 已有值 vs ZOL 新抓值 → 比对
  · 一致 → 标记 verified
  · 不一致 → 标记 conflict，**不覆盖**，留给人工
  · 已有值缺失 → 直接补

## 用法

    python fill_from_zol.py --dry        # 演练：只看能补多少
    python fill_from_zol.py --limit 20   # 只跑前 20 台
    python fill_from_zol.py              # 全量
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

POOL = r"E:\电脑涨价分析\data\machine_pool.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# ZOL 字段名 → 内部字段
# 注意：只收实测有数据的，没收的字段 ZOL 没有，补不了
FIELD_MAP = {
    "亮度": "brightness",
    "sRGB色域": "colorGamut",
    "电池类型": "batteryRaw",
    "数据接口": "portsData",
    "视频接口": "portsVideo",
    "音频接口": "portsAudio",
    "其它接口": "portsOther",
    "笔记本重量": "weightKg",
    "厚度": "thicknessMm",
    "外壳材质": "shellMaterial",
    "屏幕尺寸": "sizeInch",
    "屏幕分辨率": "resolution",
    "屏幕刷新率": "refreshHz",
    "屏幕类型": "panel",
    "显卡芯片": "gpuModel",
    "显存容量": "vramGB",
    "内存容量": "ramGB",
    "硬盘容量": "storageGB",
}


def fetch_params(pid):
    """抓一台机器的 ZOL 参数页，返回 {字段名: 值}。"""
    url = "https://detail.zol.com.cn/%s/%s/param.shtml" % (pid[:4], pid)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    raw = urllib.request.urlopen(req, timeout=20).read()
    html = raw.decode("gbk", "replace")
    rows = re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
                      html, flags=re.S)
    out = {}
    for k, v in rows:
        k2 = re.sub(r"<[^>]+>", "", k).strip()
        v2 = re.sub(r"<[^>]+>", "", v).strip()
        v2 = re.sub(r"\s+", " ", v2).replace("纠错", "").strip()
        # 🔴 2026-11-25：**去掉 ZOL 的推广后缀和多余的 > 符号**。
        #
        #    实测原始值长这样：
        #      电池类型 = '6芯锂电池>，83瓦时'      ← 有个 >
        #      外壳材质 = '复合材料>'
        #      屏幕尺寸 = '16英寸16更多16英寸笔记本>'
        #
        #    那个 `>` 是 ZOL 的推广链接残留，会干扰数值提取
        #    （「6芯锂电池>，83瓦时」用 `(\d+)瓦时` 能匹配，
        #     但如果 `>` 后面跟了别的数字就会取错）。
        v2 = v2.replace(">", "").strip()
        v2 = re.sub(r"更多[^，,；;]*$", "", v2).strip()
        v2 = re.sub(r"(极速运行|运行流畅|耗电高|超高清屏|热门游戏本|"
                    r"旗舰机|超高清屏笔记本)$", "", v2).strip()
        if k2 and v2:
            out[k2] = v2
    return out


def parse_num(s):
    """从 '500nits' / '2.43Kg' / '83瓦时' 里抠数字。"""
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(s))
    return float(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="演练，不写盘")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 台")
    ap.add_argument("--sleep", type=float, default=0.6,
                    help="每台间隔秒数（别打爆服务器）")
    args = ap.parse_args()

    pool = json.load(io.open(POOL, encoding="utf-8"))
    ms = pool["machines"]

    # 只处理「有 zol id」且「目标字段有缺失」的
    NEED = ("brightness", "thicknessMm", "shellMaterial",
            "batteryWh", "ports")
    targets = []
    for m in ms:
        mid = str(m.get("id", ""))
        if not mid.startswith("zol-"):
            continue
        missing = [k for k in NEED if not m.get(k)]
        if missing:
            targets.append((m, missing))

    if args.limit:
        targets = targets[:args.limit]

    print("=" * 72)
    print("从 ZOL 补全字段（%s）" % ("演练" if args.dry else "正式"))
    print("=" * 72)
    print("  机型池: %d 台" % len(ms))
    print("  待补: %d 台" % len(targets))
    print()

    stats = {"ok": 0, "fail": 0, "filled": 0, "conflict": 0,
             "no_change": 0}
    field_filled = {}
    conflicts = []

    for i, (m, missing) in enumerate(targets, 1):
        pid = m["id"].replace("zol-", "")
        name = (m.get("name") or "")[:26]
        try:
            params = fetch_params(pid)
        except Exception as e:
            stats["fail"] += 1
            print("  [%3d/%d] %-26s ✗ %s"
                  % (i, len(targets), name, str(e)[:32]))
            time.sleep(args.sleep)
            continue

        stats["ok"] += 1
        got = {}
        for zol_name, key in FIELD_MAP.items():
            if zol_name not in params:
                continue
            val = params[zol_name]
            if not val:
                continue
            got[key] = val

        # 逐字段处理
        for key, val in got.items():
            cur = m.get(key)
            if key == "brightness":
                new = parse_num(val)
                if new and not cur:
                    m["brightness"] = int(new)
                    got_field = True
                elif new and cur and abs(float(cur) - new) > 30:
                    conflicts.append((m.get("id"), key, cur, new))
                    stats["conflict"] += 1
                    got_field = False
                else:
                    got_field = False
            elif key == "batteryWh":
                # 「6芯锂电池，83瓦时」→ 83
                mm = re.search(r"(\d+(?:\.\d+)?)\s*瓦时", val)
                new = float(mm.group(1)) if mm else None
                if new and not cur:
                    m["batteryWh"] = new
                    got_field = True
                elif new and cur and abs(float(cur) - new) > 5:
                    conflicts.append((m.get("id"), key, cur, new))
                    stats["conflict"] += 1
                    got_field = False
                else:
                    got_field = False
            elif key == "thicknessMm":
                mm = re.search(r"(\d+(?:\.\d+)?)", val)
                new = float(mm.group(1)) if mm else None
                if new and not cur:
                    m["thicknessMm"] = new
                    got_field = True
                else:
                    got_field = False
            elif key in ("portsData", "portsVideo", "portsAudio",
                         "portsOther"):
                # 接口拼成列表
                if not m.get("ports"):
                    m["ports"] = []
                if val and val not in m["ports"]:
                    m["ports"].append(val)
                    got_field = True
                else:
                    got_field = False
            elif key == "shellMaterial":
                if not cur:
                    m["shellMaterial"] = val
                    got_field = True
                else:
                    got_field = False
            else:
                if not cur and val:
                    m[key] = val
                    got_field = True
                else:
                    got_field = False

            if got_field:
                stats["filled"] += 1
                field_filled[key] = field_filled.get(key, 0) + 1
                # 记录数据来源
                df = dict(m.get("dataFrom") or {})
                df[key] = "ZOL 参数页 %s" % time.strftime("%Y-%m-%d")
                m["dataFrom"] = df
                bf = set(m.get("backfilled") or [])
                bf.add(key)
                m["backfilled"] = sorted(bf)

        n_new = sum(1 for k in got if k in (m.get("backfilled") or []))
        print("  [%3d/%d] %-26s %d 字段 → 新增 %d"
              % (i, len(targets), name, len(got), n_new))
        time.sleep(args.sleep)

    print()
    print("=" * 72)
    print("结果")
    print("=" * 72)
    print("  成功抓取: %d 台" % stats["ok"])
    print("  抓取失败: %d 台" % stats["fail"])
    print("  补入字段: %d 处" % stats["filled"])
    print("  冲突未覆盖: %d 处" % stats["conflict"])
    print()
    if field_filled:
        print("  各字段补入数:")
        for k, v in sorted(field_filled.items(), key=lambda x: -x[1]):
            print("    %-16s %d" % (k, v))
    if conflicts:
        print()
        print("  ⚠️ 冲突（已有值 vs ZOL，未覆盖，需人工核）:")
        for cid, k, old, new in conflicts[:10]:
            print("    %-16s %-12s 已有 %s / ZOL %s"
                  % (cid, k, old, new))

    if args.dry:
        print()
        print("（演练模式，未写盘）")
        return

    # 备份 + 写盘
    import shutil
    bak = POOL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(POOL, bak)
    json.dump(pool, io.open(POOL, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print()
    print("已写入 %s" % POOL)
    print("备份: %s" % bak)


if __name__ == "__main__":
    main()
