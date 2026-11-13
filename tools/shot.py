# -*- coding: utf-8 -*-
"""shot.py —— 把 Edge 窗口截下来存成图片，供视觉读数。

为什么需要它：
  WorkBuddy 扩展的截图通道在本机全程超时（只恢复导航/DOM/点击），
  内置浏览器截图又超 10MiB 上限。唯一稳定的读图路径是
  「桌面级截图 + 视觉模型读数」，所以把这一套封成脚本。

用法：
  python tools/shot.py <输出名>            # 截当前前台 Edge 窗口
  python tools/shot.py <输出名> --maximize # 先最大化窗口（会带到前台）

输出：crawl/shots/<输出名>.jpg，并打印尺寸与 md5（便于判断是否同一张）。
注意：Edge 后台标签会"睡眠"导致内容区白屏，截前必须让目标标签在前台。
"""
import base64
import hashlib
import os
import sys
import urllib.parse

sys.path.insert(0, str(os.path.dirname(os.path.abspath(__file__))))
from cu import Client  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "crawl", "shots")


def find_edge():
    """找 Edge 进程与主窗口（多窗口时挑标题含浏览器字样的那个）。"""
    c = Client()
    try:
        st = c.call("list_apps", {})
        txt = "".join(x.get("text", "") for x in (st.get("result") or {}).get("content") or []
                      if x.get("type") == "text")
    finally:
        c.p.terminate()
    pid, win = None, None
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("Microsoft Edge [pid="):
            pid = int(line.split("pid=")[1].split(",")[0])
            continue
        # 只要 Edge 之后遇到的第一个窗口 —— 标题是页面名，不一定含 "Edge"
        if pid and win is None and line.startswith("windowId="):
            win = int(line.split("windowId=")[1].split()[0])
    return pid, win


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "shot"
    do_max = "--maximize" in sys.argv
    os.makedirs(SHOTS, exist_ok=True)
    pid, win = find_edge()
    if not pid or not win:
        print("EDGE_NOT_FOUND")
        return 1

    c = Client()
    try:
        def state(shot):
            args = {"pid": pid, "windowId": win}
            if shot:
                args["disableScreenshot"] = False
            else:
                args["disableScreenshot"] = True
                args["disableDiff"] = True
            return c.call("get_app_state", args)

        if do_max:
            st = state(False)
            tree = next((x.get("text", "") for x in (st.get("result") or {}).get("content") or []
                         if x.get("type") == "text"), "")
            # 只用同一次快照里的 id，否则 STALE_OBSERVATION
            import re
            m = re.search(r'button "最大化"[^\n]*?id=(\d+)', tree)
            if m:
                c.call("click", {"pid": pid, "element_id": m.group(1)})
                print("maximized")

        st2 = state(True)
        uri = next((x["uri"] for x in (st2.get("result") or {}).get("content") or []
                    if x.get("type") == "image-uri"), None)
        if not uri:
            print("NO_IMAGE")
            return 1
        src = os.path.normpath(urllib.parse.unquote(uri.replace("file:///", "")))

        from PIL import Image
        out = os.path.join(SHOTS, name + ".jpg")
        im = Image.open(src).convert("RGB")
        # 低质量重存：既压体积，也让内容哈希变化，避开视觉读图的同图去重
        im.save(out, "JPEG", quality=48)
        h = hashlib.md5(open(out, "rb").read()).hexdigest()[:8]
        print(f"SAVED {out} {im.size[0]}x{im.size[1]} md5={h}")
        return 0
    finally:
        c.p.terminate()


if __name__ == "__main__":
    sys.exit(main())
