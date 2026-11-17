# -*- coding: utf-8 -*-
"""把主产品前端做成可直接双击打开的静态预览页。

为什么需要：
  tools/agent/index.html 的示例按钮来自后端 /api/demo，直接双击打开会是一片空白，
  老师看到会以为页面坏了。这个脚本做三件事：
    1. 把示例问题写死进页面（来自真实 /api/demo 响应，不编造）
    2. 顶部加一条说明条，讲清「这是静态预览，能对话要启动服务」
    3. 把 /api/chat 的失败提示改成人话，指向截图和启动方式
"""
import json
import os
import re
import urllib.request

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "交付包", "选机助手-界面预览.html")

# 从正在运行的服务拉真实示例（不手写、不编造）
def fetch_demo():
    req = urllib.request.Request(
        "http://127.0.0.1:7860/api/demo",
        headers={"X-Access-Code": "dev123"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    html = open(SRC, encoding="utf-8").read()
    try:
        d = fetch_demo()
        acts = [a for a in (d.get("acts") or []) if a.get("turns")]
        print("拉到示例 %d 条" % len(acts))
    except Exception as e:
        print("拉示例失败（%s），页面仍可打开，只是没有示例按钮" % e)
        acts = []

    # ① 把 demo() 的调用改成用内联数据
    if acts:
        html = html.replace(
            "jget('/api/demo').then(function(d){ if(d.ok) demo(d) }).catch(function(){});",
            "demo(%s);" % json.dumps({"ok": True, "acts": acts}, ensure_ascii=False))

    # ② 顶部说明条：让老师一眼知道这不是坏了
    banner = """<style>
#staticNotice{position:fixed;left:0;right:0;top:0;z-index:9999;
 background:#1d1a16;border-bottom:1px solid rgba(201,162,39,.34);color:#f2ebdd;
 font:13px/1.6 "PingFang SC","Microsoft YaHei",system-ui,sans-serif;
 padding:9px 16px;text-align:center}
#staticNotice b{color:#e3c766}
#staticNotice span{opacity:.72}
body{padding-top:38px!important}
</style>
<div id="staticNotice"><b>静态界面预览</b> ·
 <span>本文件只展示界面与交互布局，未连接模型。<b>要真正对话</b>：双击项目根目录
 <code>启动助手.bat</code> 选第 1 项，浏览器打开 http://127.0.0.1:7860。
 完整对话效果见 <code>原型截图/agent_rec.png</code>、<code>agent_multi.png</code>。</span></div>
"""
    html = html.replace("<body>", "<body>\n" + banner, 1)

    # ③ 请求失败时给人话提示，而不是技术错误
    html = html.replace(
        "如果一直这样，说明本地模型没起来，可以先用上面的示例问题。",
        "如果一直这样，说明本地模型没起来 —— 当前是<b>静态预览</b>，点发送不会有回答；"
        "请双击项目根目录的 启动助手.bat 后再试。")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write(html)
    print("已写出:", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()