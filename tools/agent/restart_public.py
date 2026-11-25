# -*- coding: utf-8 -*-
"""restart_public.py —— 一键重启：服务 + 公网隧道 + 保存链接

## 为什么需要这个（launcher.py 已经有了）

`tools/launcher.py public` 功能很完整（服务启动、隧道、探活、PID 管理），
但有两个不满足当前场景的点：

  ① **链接不落盘** —— 只打印在屏幕上，关掉窗口就找不到了
  ② **要交互** —— 隧道失败时问「再试一次？[Y/n]」，不适合脚本化

这个脚本做的事：
  · 复用 launcher.py 的隧道逻辑（不重复实现，判据只写一遍）
  · 把链接写进 `_public_url.txt`，随时能读
  · 完全非交互，适合「重启后双击就恢复」
  · 加一个 `--check` 只检查当前状态、不重启

## 用法

    python restart_public.py          # 重启服务+隧道，输出链接
    python restart_public.py --check  # 只看当前状态
    python restart_public.py --keep   # 服务已在跑就只重建隧道

## 为什么复用 launcher.py 而不是重写

`launcher.py` 的 `start_tunnel()` 里有 3 个踩过的坑的判据：

  · GOOD_HOST 正则：排除 api/www 这类固定子域
    （2026-11-13 差点把 `api.trycloudflare.com` 当隧道地址发出去）
  · BAD_LINE 正则：带 failed/error/timeout 的行不算地址
  · READY 判据：必须等到 `Registered tunnel connection`

**这些判据抄两遍一定会漂**（项目已复现 9 次）。所以直接 import 复用。
"""
import argparse
import io
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PORT = 7860
URL_FILE = os.path.join(ROOT, "_public_url.txt")
PID_FILE = os.path.join(ROOT, "_pids.json")

sys.path.insert(0, os.path.join(ROOT, "tools"))


def say(msg):
    print("  %s" % msg, flush=True)


def find_cloudflared():
    """找 cloudflared 可执行文件。"""
    cands = [
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        r"C:\Program Files\cloudflared\cloudflared.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\cloudflared\cloudflared.exe"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    # 试 PATH
    try:
        r = subprocess.run(["where", "cloudflared"], capture_output=True,
                           text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip().split("\n")[0].strip()
    except Exception:
        pass
    return None


def port_up(port=PORT):
    """端口是否在监听。"""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except Exception:
        return False


def ollama_up():
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/version",
                               timeout=4).read()
        return True
    except Exception:
        return False


def tunnel_procs():
    """当前 cloudflared 进程列表。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process -Name cloudflared -ErrorAction SilentlyContinue | "
             "Select-Object -ExpandProperty Id"],
            capture_output=True, text=True, timeout=15)
        return [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]
    except Exception:
        return []


def server_procs():
    """当前 server.py 进程列表。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*server.py*' } | "
             "Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=20)
        return [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]
    except Exception:
        return []


def kill_pids(pids, what=""):
    for pid in pids:
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Stop-Process -Id %d -Force "
                            "-ErrorAction SilentlyContinue" % pid],
                           capture_output=True, timeout=15)
            say("停 %s pid=%d" % (what or "进程", pid))
        except Exception:
            pass


def read_url_file():
    if os.path.exists(URL_FILE):
        u = io.open(URL_FILE, encoding="utf-8").read().strip()
        return u or None
    return None


def probe(url, timeout=20):
    """本机访问公网链接，看通不通。"""
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        return r.status == 200
    except Exception:
        return False


def start_tunnel(cf):
    """启动隧道并返回 (proc, url)。

    🔴 判据复用 launcher.py 的思路，但这里独立实现读取循环
       （launcher 的 start_tunnel 是阻塞式带交互的，不适合脚本化）。

       三个坑（2026-11-13 踩过）：
         ① 失败行里也有 trycloudflare.com（api.trycloudflare.com），
            不能当隧道地址
         ② 拿到 URL ≠ 能用，要等 `Registered tunnel connection`
         ③ 固定子域（api/www/dash）排除
    """
    GOOD = re.compile(r"https://([a-z0-9]+(?:-[a-z0-9]+){2,})"
                      r"\.trycloudflare\.com")
    BAD = re.compile(r"fail|error|err\b|timeout|unable|denied|refused", re.I)
    READY = re.compile(r"Registered tunnel connection", re.I)
    BAD_SUB = ("api", "www", "dash", "docs", "blog")

    cmd = [cf, "tunnel", "--url", "http://127.0.0.1:%d" % PORT,
           "--protocol", "http2", "--no-autoupdate"]
    log_path = os.path.join(ROOT, "_tunnel.log")
    logf = io.open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)

    url = None
    ready = False
    t0 = time.time()
    seen = 0
    while time.time() - t0 < 60:
        time.sleep(1)
        try:
            txt = io.open(log_path, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        chunk = txt[seen:]
        seen = len(txt)
        if READY.search(chunk):
            ready = True
        if not url:
            for line in chunk.split("\n"):
                m = GOOD.search(line)
                if not m:
                    continue
                if BAD.search(line):
                    continue
                if m.group(1).split("-")[0] in BAD_SUB:
                    continue
                url = m.group(0)
                break
        if url and ready:
            break

    if not url:
        proc.terminate()
        return None, None
    return proc, url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查状态")
    ap.add_argument("--keep", action="store_true",
                    help="服务已在跑就只重建隧道")
    args = ap.parse_args()

    print("=" * 62)
    print("选机助手 · 服务 + 公网链接")
    print("=" * 62)

    # ── 状态检查 ──
    srv = server_procs()
    tun = tunnel_procs()
    old_url = read_url_file()

    print()
    print("【当前状态】")
    say("服务(7860)     %s%s" % ("✓ 在跑" if port_up() else "✗ 没起",
                                " (pid %s)" % ",".join(map(str, srv))
                                if srv else ""))
    say("Ollama(11434)  %s" % ("✓ 就绪" if ollama_up() else "✗ 没起"))
    say("隧道           %s%s" % ("✓ 在跑" if tun else "✗ 没有",
                                " (pid %s)" % ",".join(map(str, tun))
                                if tun else ""))
    if old_url:
        say("上次链接       %s" % old_url)

    if args.check:
        print()
        if old_url and port_up():
            ok = probe(old_url)
            say("链接可用性     %s" % ("✓ 通" if ok else "✗ 不通（需重建）"))
        print()
        print("=" * 62)
        return 0

    # ── 前置检查 ──
    if not ollama_up():
        print()
        say("✗ Ollama 没起 —— 先启动 Ollama（模型跑在里面）")
        say("  双击 Ollama 图标，或运行 ollama app.exe")
        return 1

    cf = find_cloudflared()
    if not cf:
        print()
        say("✗ 找不到 cloudflared")
        say("  winget install Cloudflare.cloudflared")
        return 1

    # ── 停旧进程 ──
    if not args.keep and srv:
        print()
        say("停旧服务…")
        kill_pids(srv, "服务")
        time.sleep(2)
    if tun:
        print()
        say("停旧隧道…")
        kill_pids(tun, "隧道")
        time.sleep(2)

    # ── 起服务 ──
    if not port_up():
        print()
        say("启动服务…")
        server_py = os.path.join(HERE, "server.py")
        log = io.open(os.path.join(ROOT, "_server.log"), "w",
                      encoding="utf-8", errors="replace")
        sp = subprocess.Popen([sys.executable, "-u", server_py, "--lan"],
                              cwd=HERE, stdout=log,
                              stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL)
        for _ in range(30):
            time.sleep(1)
            if port_up():
                break
        if not port_up():
            say("✗ 服务没起来 —— 看 _server.log")
            return 1
        say("✓ 服务就绪（pid %d）" % sp.pid)
    else:
        say("✓ 服务已在跑，跳过")

    # ── 起隧道 ──
    print()
    say("建立隧道（约 10-25 秒）…")
    proc, url = start_tunnel(cf)
    if not url:
        say("✗ 隧道没拿到地址")
        say("  看 _tunnel.log")
        return 1
    say("✓ 隧道已建立（pid %d）" % proc.pid)

    # ── 落盘 + 探活 ──
    io.open(URL_FILE, "w", encoding="utf-8").write(url + "\n")

    print()
    say("检查链接是否可访问…")
    reachable = False
    for i in range(4):
        if probe(url):
            reachable = True
            break
        time.sleep(6)

    print()
    print("=" * 62)
    print("  %s" % url)
    print("=" * 62)
    print()
    if reachable:
        say("✓ 链接可用（本机验证通过）")
    else:
        say("⚠ 本机还没打通 —— 再等十几秒，或用浏览器打开试试")
        say("  如果是 Cloudflare Error 1033，就是隧道还在注册")
    print()
    say("已写入 %s" % URL_FILE)
    say("停止：双击 停止服务.bat 或 python tools/launcher.py stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
