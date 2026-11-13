# -*- coding: utf-8 -*-
"""add_throttle.py —— 给指定机器写入持续性能（双烤）数据

用法：
  python tools/add_throttle.py <机器id包含串> <json文件>
  json 文件内容为 throttle 对象本体，例如：
    {"type":"gaming","cpuSustainedW":90,"gpuSustainedW":114,...}

已有 throttle 的机器默认跳过（防重复写入），加 --force 可覆盖。
"""
import json
import sys
import glob
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    needle, tf = sys.argv[1], sys.argv[2]
    force = "--force" in sys.argv
    th = json.loads(Path(tf).read_text(encoding="utf-8"))

    n = 0
    for f in glob.glob(str(ROOT / "data" / "crawl" / "*.jsonl")):
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
            if needle in str(r.get("id")) and (force or not r.get("throttle")):
                r["throttle"] = th
                changed = True
                n += 1
                print(f"写入 {r.get('id')[:48]}")
            rows.append(r)
        if changed:
            with open(f, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"共写入 {n} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
