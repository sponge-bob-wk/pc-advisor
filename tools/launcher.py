# -*- coding: utf-8 -*-
"""电脑选机助手 · 启动器（2026-11-13）

════════════════════════════════════════════════════════════════════
为什么单独做一个启动器，而不是继续加 bat
════════════════════════════════════════════════════════════════════
原来 `scripts/` 下有 3 个 bat（local / lan / public），问题：

1. **中文全靠 chcp 65001 撑着**，中文 Windows 上 bat 里的中文时不时乱码，
   而报错信息恰恰是最需要看清的那几行。
2. **公网模式拿不到链接**：tunnel 启动后会打印一个随机的
   `https://xxx.trycloudflare.com`，bat 只能把它甩在屏幕上让你自己抄。
   而实际要发给人，得是 `https://xxx/?k=访问码` 拼好的完整链接。
3. **没有前置检查**：端口被占、Ollama 没开、cloudflared 没装，
   全都是启动失败之后才在日志里发现。
4. **停不干净**：`start` 开的窗口关掉后，python 进程有时还占着 7860，
   下次启动报 `Address already in use`。这是 2026-11-13 之前反复踩的坑。

所以这里用 Python 做（项目本来就依赖 Python），bat 只留一行壳。

════════════════════════════════════════════════════════════════════
三种模式各是什么
════════════════════════════════════════════════════════════════════
  本机     只绑 127.0.0.1 —— 自己用，别人碰不到
  局域网   绑 0.0.0.0 —— 同 WiFi/网线的设备能连，给同组人用
  公网     局域网 + Cloudflare 临时隧道 —— 任何人拿到链接就能开，
           链接**每次重启都会变**，且你的电脑必须开着

⚠️ 公网模式务必带访问码（ACCESS_CODE）。不带的话，任何拿到域名的人
   都能刷你的显卡跑模型。
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

# Windows 控制台在 chcp 65001 下偶尔仍用 gbk 输出，中文会炸。
# line_buffering：输出重定向到文件时（快捷方式、计划任务、日志），
# 不加这个整段输出会攒到最后才落盘，看起来像"卡住了"。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_agent_dir(root):
    """找出主产品源码所在目录 —— 同一个启动器要同时服务两种目录布局。

    ① 原仓库：      <root>/tools/agent/{server.py, core.py, ...}
    ② 交付包：      <root>/src/{server.py, core.py, ...}
       （交付给老师的包按「文档进 docs、页面在根目录」重新组织过，
         源码集中放在 src/，没有再套 tools/agent 两层）

    按顺序探测，第一个命中的就用。两边都没有就退回 ①，让后面的
    「找不到 server.py」检查给出准确报错，而不是在这里静默出错。
    """
    for rel in (("tools", "agent"), ("src",)):
        d = os.path.join(root, *rel)
        if os.path.isfile(os.path.join(d, "server.py")):
            return d
    return os.path.join(root, "tools", "agent")


AGENT_DIR = _find_agent_dir(ROOT)
TMP_DIR = os.path.join(ROOT, ".tmp")
PID_FILE = os.path.join(TMP_DIR, "pids.json")
LINK_FILE = os.path.join(ROOT, "测试链接.txt")

# ── 默认配置（想改就改这里，或者设同名环境变量）────────────────────────
DEFAULT_PORT = 7860
DEFAULT_CODE = "dev123"

PORT = int(os.environ.get("PORT") or DEFAULT_PORT)
ACCESS_CODE = (os.environ.get("ACCESS_CODE") or DEFAULT_CODE).strip()

# 控制台颜色（Windows 10+ 支持 ANSI）
C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_OK = "\033[32m"
C_WARN = "\033[33m"
C_ERR = "\033[31m"
C_KEY = "\033[36m"
C_BOLD = "\033[1m"


def line(ch="─", n=62):
    print(C_DIM + ch * n + C_RESET)


def title(s):
    print()
    line("═")
    print("  " + C_BOLD + s + C_RESET)
    line("═")


def ok(s):
    print("  " + C_OK + "✓" + C_RESET + " " + s)


def warn(s):
    print("  " + C_WARN + "!" + C_RESET + " " + s)


def err(s):
    print("  " + C_ERR + "✗" + C_RESET + " " + s)


def info(s):
    print("  " + C_DIM + s + C_RESET)


# ══════════════════════════════════════════════════════════════════
# 环境检查
# ══════════════════════════════════════════════════════════════════
def port_in_use(port):
    """端口有没有被别的程序占着。

    🔴 2026-11-13 踩到的坑：原来用 `s.bind()` + `SO_REUSEADDR` 判断 ——
    在 Windows 上 **`SO_REUSEADDR` 的语义和 Linux 不一样**，它允许两个 socket
    bind 同一个端口（相当于 Linux 的 REUSEADDR+REUSEPORT）。
    于是服务明明在跑，这里却返回 False，启动器的 `wait_server` 一直
    `continue` 跳过，白等 30 秒后报「服务没起来」—— 而服务其实是好的。

    改成「**能不能连上**」：端口上真有人在听，connect 就成功。
    这个语义才是我们需要的（"有没有服务在应答"），而且不受 Windows
    套接字选项差异影响。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.6)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def who_takes_port(port):
    """查是谁占着端口，用来告诉用户该杀哪个。查不到返回 None。"""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, encoding="gbk", errors="replace",
                             timeout=8).stdout or ""
    except Exception:
        return None
    for ln in out.splitlines():
        if "LISTENING" in ln and re.search(r":%d\b" % port, ln):
            parts = ln.split()
            if parts:
                return parts[-1]
    return None


def ollama_alive():
    """Ollama 在不在 11434。不在的话助手能启动但每句话都会失败。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.2)
    try:
        return s.connect_ex(("127.0.0.1", 11434)) == 0
    finally:
        s.close()


def start_ollama(wait_s=40):
    """自动拉起 Ollama。返回 (成功?, 说明)。

    🔴 2026-11-18 新增。用户反馈两个问题：
      ① `启动助手.bat` 双击后浏览器打不开 7860
      ② Ollama 得手动启动

    根因：`launcher.py` 原来**只检查不启动** Ollama ——
    检测到没跑就打印「另开一个窗口运行：ollama serve」，
    然后问「还是要启动吗？[y/N]」。用户直接回车（默认 N）→ 回菜单 → 服务没起来。

    这体验是错的：启动器就该把依赖拉起来，而不是让用户开第二个窗口。

    三种情况都要处理：
      · `ollama` 在 PATH 里          → 起 `ollama serve`
      · 只有桌面版 Ollama（托盘应用） → 起 `ollama app.exe`，它会带起后端
      · 都找不到                     → 返回失败，让上层给明确指引
    """
    # 已经在跑就别重复起
    if ollama_alive():
        return True, "已经在线"

    # 找可执行文件：优先 PATH 里的 ollama，其次桌面版安装位置
    exe = shutil.which("ollama")
    app = None
    if not exe:
        for cand in (
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
            r"C:\Program Files\Ollama\ollama.exe",
        ):
            if os.path.isfile(cand):
                exe = cand
                break
    for cand in (
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"),
        r"C:\Program Files\Ollama\ollama app.exe",
    ):
        if os.path.isfile(cand):
            app = cand
            break

    if not exe and not app:
        return False, "找不到 ollama（PATH 和常见安装位置都没有）"

    # 起进程。桌面版用 app（它会自己带起 serve），否则直接 serve
    try:
        if app:
            subprocess.Popen([app], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess,
                                                   "CREATE_NO_WINDOW", 0))
        else:
            subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess,
                                                   "CREATE_NO_WINDOW", 0))
    except Exception as e:                       # noqa: BLE001
        return False, "启动失败：%s" % str(e)[:60]

    # 等它就绪（首次加载模型可能要几秒）
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if ollama_alive():
            return True, "已启动（%.1fs）" % (time.time() - t0)
        time.sleep(0.8)
    return False, "等了 %ds 还没就绪（可能还在加载）" % wait_s


def find_cloudflared():
    """cloudflared 找得到吗。winget 装完不进当前 PATH，所以兜常见路径。"""
    p = shutil.which("cloudflared")
    if p:
        return p
    for c in (r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
              r"C:\Program Files\cloudflared\cloudflared.exe",
              os.path.expanduser(r"~\cloudflared\cloudflared.exe")):
        if os.path.isfile(c):
            return c
    return None


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


# ══════════════════════════════════════════════════════════════════
# 进程管理（用 pid 文件，比"按端口猜"可靠）
# ══════════════════════════════════════════════════════════════════
def save_pids(d):
    os.makedirs(TMP_DIR, exist_ok=True)
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def load_pids():
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def kill_pid(pid):
    if not pid:
        return False
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def stop_all(quiet=False):
    """停掉服务与隧道。**两条路都走**：pid 文件 + 端口兜底。

    只信 pid 文件不够 —— 用户可能用别的方式启动过一次，
    那个进程不在文件里，端口照样被占。所以最后按端口再扫一遍。
    """
    pids = load_pids()
    killed = []
    for name in ("tunnel", "server"):
        pid = pids.get(name)
        if pid and kill_pid(pid):
            killed.append((name, pid))
    # 兜底：端口还占着，说明有别的实例
    extra = None
    if port_in_use(PORT):
        extra = who_takes_port(PORT)
        if extra and extra.isdigit():
            kill_pid(extra)
            killed.append(("占用端口的进程", extra))

    # cloudflared 可能还有别的隧道残留
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
                             capture_output=True, text=True, encoding="gbk",
                             errors="replace", timeout=8).stdout or ""
        for ln in out.splitlines():
            if "cloudflared" in ln.lower():
                parts = ln.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    kill_pid(parts[1])
                    killed.append(("cloudflared", parts[1]))
    except Exception:
        pass

    try:
        os.remove(PID_FILE)
    except Exception:
        pass

    # 🔴 2026-11-18：**同时停掉 Ollama**。
    #
    #    用户反馈：「好像 ollama 得手动自己启动」——
    #    既然启动器会自动拉起它（见 start_ollama），停止时就该一起停，
    #    否则用户以为"停干净了"，实际显存还被占着
    #    （训练时会因此掉速，2026-11-18 实测过：8 秒/步 → 28 秒/步）。
    #
    #    ⚠️ 只在**确实是我们拉起的**或用户明确要停时才停？
    #       这里选择"总是停"—— 因为 Ollama 只服务这个项目，
    #       留着它占 900MB 显存没意义。停掉后下次启动器会自动拉起。
    # 🔴 顺序很关键：**先停 ollama app（托盘程序），再停后端**。
    #    反过来的话，app 会立刻把 ollama/llama-server 重新拉起来
    #    （2026-11-18 实测：停完 ollama 后 11434 又通了，因为 app 重拉了）。
    #
    # 🔴🔴 解析 tasklist 这条路走了两次都错，最后放弃，改用 taskkill /IM：
    #    ① 第一版取 parts[1] → `ollama app.exe` 名字带空格，parts[1] 是 'app.exe'
    #    ② 第二版从后往前找纯数字 → 取到了 '9'（Session# 那一列），杀了不存在的 PID
    #    根因：tasklist 的输出列数不固定（Mem Usage 可能带逗号、Session Name
    #    可能是 Services 或 Console），按位置解析必然出错。
    #
    #    改用 `taskkill /F /IM <name>.exe` —— 它按映像名杀，不关心 PID，
    #    且名字带空格也没问题（用列表参数传，不拼字符串）。
    olla = []
    for nm in ("ollama app", "ollama", "llama-server"):
        try:
            r = subprocess.run(["taskkill", "/F", "/IM", "%s.exe" % nm],
                               capture_output=True, text=True, encoding="gbk",
                               errors="replace", timeout=10)
            if r.returncode == 0:
                # 从输出里抠 PID 只为打印（"SUCCESS: ... PID 1234"）
                m2 = re.search(r"PID\s+(\d+)", r.stdout or "")
                olla.append((nm, m2.group(1) if m2 else "?"))
        except Exception:
            continue

    if not quiet:
        if killed:
            for name, pid in killed:
                ok("已停止 %s（PID %s）" % (name, pid))
        else:
            info("没有正在运行的实例。")
        if olla:
            for name, pid in olla:
                ok("已停止 %s（PID %s）—— 释放显存" % (name, pid))
        # 验证
        time.sleep(0.6)
        if port_in_use(PORT):
            warn("端口 %d 仍被占用，可能需要管理员权限" % PORT)
        else:
            ok("端口 %d 已释放" % PORT)
        if ollama_alive():
            # 🔴 2026-11-18：Ollama 桌面版的托盘程序会**自动重拉**后端。
            #    实测：杀掉 ollama 后 1 秒内 11434 又通了。
            #    所以再扫一轮，把重新起来的也杀掉。
            warn("Ollama 又被拉起来了，再停一次…")
            for nm in ("ollama app", "ollama", "llama-server"):
                try:
                    subprocess.run(["taskkill", "/F", "/IM", "%s.exe" % nm],
                                   capture_output=True, text=True, encoding="gbk",
                                   errors="replace", timeout=10)
                except Exception:
                    continue
            time.sleep(1.5)
        if ollama_alive():
            warn("Ollama 仍在运行（11434 还占着）—— 可能有别的实例或服务")
        else:
            ok("Ollama 已停止（11434 已释放）")
    return True


# ══════════════════════════════════════════════════════════════════
# 启动
# ══════════════════════════════════════════════════════════════════
def _pump(proc, prefix, sink=None):
    """把子进程输出转到本窗口，顺便抓隧道 URL。"""
    try:
        for raw in iter(proc.stdout.readline, ""):
            if not raw:
                break
            text = raw.rstrip()
            if not text:
                continue
            print("  " + C_DIM + "[" + prefix + "]" + C_RESET + " " + text[:200])
            if sink:
                sink(text)
    except Exception:
        pass


def open_browser(url):
    try:
        webbrowser.open(url)
    except Exception:
        pass


def put_clipboard(text):
    try:
        subprocess.run(["clip"], input=text, text=True, timeout=5)
        return True
    except Exception:
        return False


def start_server(lan=True):
    env = dict(os.environ)
    env["ACCESS_CODE"] = ACCESS_CODE
    env["PORT"] = str(PORT)
    env["PYTHONUNBUFFERED"] = "1"      # 不加这句 print 会攒着，窗口一片空白
    cmd = [sys.executable, "-u", "server.py"]
    if lan:
        cmd.append("--lan")
    proc = subprocess.Popen(cmd, cwd=AGENT_DIR, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)
    threading.Thread(target=_pump, args=(proc, "服务"), daemon=True).start()
    return proc


def wait_server(timeout=30):
    """等服务真正能应答。只看进程活着不够 —— 它可能绑端口失败了。

    🔴 2026-11-13 踩到的坑：`/api/health` 也在访问码护栏**里面**，
    不带 `X-Access-Code` 拿到的就是 401。原来判 `status == 200`，
    于是服务明明起来了（端口在听、接口在跑），启动器却报「服务没起来」。
    ⇒ 判据改成「能拿到 HTTP 响应且状态码 < 500」——401 也证明服务活着。
    """
    import urllib.error
    import urllib.request

    t0 = time.time()
    while time.time() - t0 < timeout:
        if not port_in_use(PORT):
            time.sleep(0.3)
            continue
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/api/health" % PORT,
                headers={"X-Access-Code": ACCESS_CODE})
            with urllib.request.urlopen(req, timeout=2) as r:
                if r.status < 500:
                    return True
        except urllib.error.HTTPError as e:
            # 服务活着，只是拒绝了我们（401/403 都算活着）
            if e.code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def start_tunnel(cf):
    cmd = [cf, "tunnel", "--url", "http://127.0.0.1:%d" % PORT,
           "--no-autoupdate", "--protocol", "http2"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    box = {"url": None, "ready": False, "bad": None}

    # 🔴 2026-11-13 踩到的坑（差点把假链接发给测试同学）：
    #   隧道建立失败时，cloudflared 会打这样一行：
    #       failed to request quick Tunnel:
    #       Post "https://api.trycloudflare.com/tunnel": context deadline exceeded
    #   原来用 `https://[a-z0-9-]+\.trycloudflare\.com` 去抓，**把这行里的
    #   api.trycloudflare.com 当成了隧道地址** —— 于是启动器欢天喜地地
    #   把 `https://api.trycloudflare.com/?k=dev123` 写进文件、还复制到剪贴板。
    #
    #   判据（必须有分辨力）：
    #     ① 域名必须是**随机子域**：`trycloudflare.com` 前面那一段是
    #        cloudflared 生成的，形如 `earrings-gis-slow-blonde`（至少两个连字符）。
    #        `api` / `www` 这类固定子域直接排除。
    #     ② 该行不能带 failed / error / ERR / timeout 这类字样。
    #     ③ 只认 cloudflared 明确打印的那句 Your quick Tunnel has been created!
    #
    # 🔴 2026-11-13 第二个坑：**拿到 URL ≠ 隧道能用**。
    #   cloudflared 会先打印域名，过几秒才 `Registered tunnel connection`。
    #   这中间去访问就是 Cloudflare 的 Error 1033（改了也白改，白等）。
    #   所以除了 URL，还要等 ready —— 见下面的 READY 判据。
    GOOD_HOST = re.compile(
        r"https://([a-z0-9]+(?:-[a-z0-9]+){2,})\.trycloudflare\.com")
    BAD_LINE = re.compile(r"fail|error|err\b|timeout|unable|denied|refused",
                          re.I)
    # 这两句任一条出现，才说明隧道**真的通了**：
    #   Registered tunnel connection          —— 连接已登记
    #   Your quick Tunnel has been created!   —— 域名已分配
    # ⚠️ 但只有前者够 —— 后者早于前者。所以以 Registered 为准。
    READY = re.compile(r"Registered tunnel connection", re.I)

    def grab(text):
        if READY.search(text):
            box["ready"] = True
        if box["url"]:
            return
        m = GOOD_HOST.search(text)
        if not m:
            return
        # 命中失败行 → 记下来当"确实失败了"，绝不当作地址
        if BAD_LINE.search(text):
            box["bad"] = text.strip()[:160]
            return
        # cloudflared 生成的子域不会叫这些
        if m.group(1).split("-")[0] in ("api", "www", "dash", "docs", "blog"):
            return
        box["url"] = m.group(0)

    threading.Thread(target=_pump, args=(proc, "隧道", grab), daemon=True).start()

    t0 = time.time()
    while time.time() - t0 < 75:
        if box["url"] and box["ready"]:
            return proc, box["url"]
        if proc.poll() is not None:
            return proc, None
        time.sleep(0.4)
    # 超时：URL 有但没登记 → 不能拿来用
    if box["url"] and not box["ready"]:
        return proc, None
    return proc, None


# ══════════════════════════════════════════════════════════════════
# 三种模式
# ══════════════════════════════════════════════════════════════════
def preflight(need_tunnel=False):
    """启动前把能预见的坑都查一遍。全通过返回 True。"""
    print()
    info("启动前检查…")
    good = True

    if not os.path.isfile(os.path.join(AGENT_DIR, "server.py")):
        err("找不到 %s —— 启动器不在项目里？"
            % os.path.relpath(os.path.join(AGENT_DIR, "server.py"), ROOT))
        return False
    ok("程序文件在位")

    if not ollama_alive():
        # 🔴 2026-11-18：改成**自动拉起**，不再让用户开第二个窗口。
        #    用户反馈「Ollama 得手动自己启动」—— 那不该是用户的事。
        info("Ollama 没在跑，正在自动启动…")
        _ok_o, _msg_o = start_ollama()
        if _ok_o:
            ok("Ollama %s" % _msg_o)
        else:
            warn("Ollama 启动失败：%s" % _msg_o)
            info("网页能打开、界面能看，但**每问一句都会失败**。")
            info("手动启动：另开一个窗口运行 `ollama serve`")
            info("或双击桌面版 Ollama 图标（托盘出现羊驼即可）")
            good = False
    else:
        ok("Ollama 在线")

    if port_in_use(PORT):
        pid = who_takes_port(PORT)
        warn("端口 %d 已被占用%s" % (PORT, "（PID %s）" % pid if pid else ""))
        ans = input("      先停掉它？[Y/n] ").strip().lower()
        if ans in ("", "y", "yes"):
            pids = load_pids()
            if pid and pid.isdigit():
                kill_pid(pid)
            kill_pid(pids.get("server"))
            time.sleep(1.2)
            if port_in_use(PORT):
                err("还是占着，换个端口吧（设环境变量 PORT=7861 再启动）")
                return False
            ok("已释放端口 %d" % PORT)
        else:
            err("端口被占，启动会失败。")
            return False
    else:
        ok("端口 %d 空闲" % PORT)

    if need_tunnel:
        cf = find_cloudflared()
        if not cf:
            err("没找到 cloudflared，公网模式用不了")
            info("装一个：winget install --id Cloudflare.cloudflared -e --source winget")
            return False
        ok("cloudflared 在位")

    return good


def banner_mode(mode, url, extra_note="", has_code=True):
    title("已启动 · %s" % mode)
    print("  " + C_BOLD + "打开这个链接：" + C_RESET)
    print("  " + C_KEY + url + C_RESET)
    print()
    if extra_note:
        info(extra_note)
    if has_code:
        print("  " + C_DIM + "访问码已拼进链接（?k=…），直接发给别人就行。" + C_RESET)
    print()
    info("按 Ctrl+C 停止服务。")
    line()


def run_local():
    if not preflight(need_tunnel=False):
        if input("\n  检查没全过，还是要启动吗？[y/N] ").strip().lower() not in ("y", "yes"):
            return
    proc = start_server(lan=False)
    save_pids({"server": proc.pid})
    if not wait_server():
        err("服务没起来，看上面的 [服务] 输出")
        stop_all(quiet=True)
        return
    url = "http://127.0.0.1:%d/?k=%s" % (PORT, ACCESS_CODE)
    banner_mode("本机自用", url,
                "只有这台电脑能打开（只绑 127.0.0.1），别人连不上。")
    # 🔴 2026-11-18：**自动打开浏览器**。
    #
    #    用户反馈：「启动服务快捷方式启动了也访问不了」
    #    根因就在这里：run_local 只把 URL 打印出来，然后 hold(proc) 阻塞，
    #    用户双击 bat 看到的是一个黑窗口，得自己复制 URL 去浏览器 ——
    #    不会复制的人就以为"访问不了"。
    #
    #    修法：服务就绪后自动开默认浏览器。
    #    延迟 1 秒是为了让 server 的 accept 循环真正就绪
    #    （wait_server 只测了端口能连，第一帧可能还没准备好）。
    try:
        time.sleep(1.0)
        webbrowser.open(url)
        ok("已自动打开浏览器（没反应的话手动点上面那个地址）")
    except Exception as e:                     # noqa: BLE001
        warn("自动打开浏览器失败：%s" % str(e)[:50])
        info("手动复制上面的地址到浏览器")
    hold(proc)


def run_lan():
    if not preflight(need_tunnel=False):
        if input("\n  检查没全过，还是要启动吗？[y/N] ").strip().lower() not in ("y", "yes"):
            return
    proc = start_server(lan=True)
    save_pids({"server": proc.pid})
    if not wait_server():
        err("服务没起来，看上面的 [服务] 输出")
        stop_all(quiet=True)
        return
    ip = lan_ip() or "本机IP"
    url = "http://%s:%d/?k=%s" % (ip, PORT, ACCESS_CODE)
    banner_mode("局域网共享", url,
                "同一 WiFi / 网线下的设备都能开。手机也行。")
    print()
    info("⚠️ 这台电脑必须一直开着，关掉窗口链接就断。")
    info("⚠️ Windows 防火墙第一次可能弹窗，选「允许」。")
    if input("\n  现在用浏览器打开吗？[Y/n] ").strip().lower() in ("", "y", "yes"):
        open_browser(url)
    hold(proc)


def run_public():
    if not preflight(need_tunnel=True):
        return
    cf = find_cloudflared()

    print()
    info("正在启动服务…")
    proc = start_server(lan=True)
    save_pids({"server": proc.pid})
    if not wait_server():
        err("服务没起来，看上面的 [服务] 输出")
        stop_all(quiet=True)
        return
    ok("服务就绪")

    info("正在建立 Cloudflare 隧道（约 5–15 秒）…")
    tproc, base = start_tunnel(cf)
    if not base:
        print()
        err("隧道没拿到地址。")
        info("常见原因：这台机器出不了外网的 443（代理 / 公司网络 / Cloudflare 侧抖动）")
        info("这不是你的代码问题 —— 换「局域网」模式给同组人，那个不依赖外网。")
        print()
        if input("  再试一次？[Y/n] ").strip().lower() in ("", "y", "yes"):
            tproc, base = start_tunnel(cf)
        if not base:
            err("还是失败。已停掉本次启动的进程，不留半开的服务。")
            stop_all(quiet=True)
            return
    save_pids({"server": proc.pid, "tunnel": tproc.pid})

    url = base + "/?k=" + ACCESS_CODE

    info("检查隧道是否已经能应答（本机回环，最多 16 秒）…")
    reachable = probe_public(url)

    banner_mode("公网测试链接", url, "任何人拿到这个链接都能打开 —— 这就是发给测试同学的地址。")
    print()
    if reachable:
        ok("链路已通（本机 → Cloudflare → 隧道 → 本地服务）")
        info("⚠️ 这只是本机能通。真正能不能开，**第一次让别人打开**才算数。")
    else:
        warn("本机这一圈都没打通 —— 先别发给别人，等十几秒再试")
        info("如果浏览器打开是 Cloudflare Error 1033，就是还没通。")

    # 只在确认可达、或至少拿到了正规域名时才落地
    if reachable is not False:
        try:
            with open(LINK_FILE, "w", encoding="utf-8") as f:
                f.write(url + "\n")
            ok("链接已写入 测试链接.txt")
        except Exception:
            pass
        if put_clipboard(url):
            ok("链接已复制到剪贴板，直接 Ctrl+V 发给别人")

    print()
    info("⚠️ 这个地址**每次重启都会变**，不是固定的。")
    info("⚠️ 你的电脑必须开着、不能睡眠，否则链接失效。")
    info("⚠️ 访问码：%s（已拼进链接；别人手输地址时要带上）" % ACCESS_CODE)

    if input("\n  现在用浏览器打开吗？[Y/n] ").strip().lower() in ("", "y", "yes"):
        open_browser(url)
    hold(proc)


def probe_public(url, tries=8):
    """尽力确认链接能打开 —— 但**不能当成"外网一定通"的证明**。

    🔴 判据的边界（2026-11-13 实测后修正，必须写明）：
      这里是从**本机**访问自己的隧道域名。拿到 200 只能说明
      「本机 → Cloudflare → 隧道 → 本地服务」这条回路通。
      它**不能排除**「外网 → Cloudflare」那一段没问题的反例 ——
      本机可能有本地 DNS 缓存、可能被代理劫持、Cloudflare 边缘路由也可能
      对同网段和对公网表现不同。

      所以本函数的结果只用于两件事：
        ① 明确失败时**拦住**不要发出去（这个方向是可靠的：连本机都不通，
           外网一定不通）
        ② 成功时给个"看起来没问题"的提示，措辞必须留余地

      真正的确认只有一条路：让测试同学打开。产出文案不许写成
      「外网已确认可访问」—— 那是把弱证据说成强结论。

    返回值：True=本机回路通 / False=明确不通 / None=没验成
    """
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    got_any_response = False
    for i in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "pc-advisor-launcher"})
            with opener.open(req, timeout=6) as r:
                if 200 <= r.status < 400:
                    got_any_response = True
                    # 再等一下，让边缘路由稳定，别只靠第一次
                    if i >= 2:
                        return True
        except urllib.error.HTTPError as e:
            if 200 <= e.code < 400:
                got_any_response = True
            # 1033/530 = 隧道还没登记完；502/504 = 边缘到源站还不通
            # 这些都要继续等，不能判死
        except Exception:
            pass
        time.sleep(2)
    if got_any_response:
        return True
    return None


def hold(proc):
    """前台守着，直到 Ctrl+C 或进程自己退出。

    🔴 非交互环境（被脚本 / 重定向 stdin 调用）时 input() 会立刻抛 EOFError，
    如果照搬"等用户按键"的写法，进程会直接退出、把服务带走。
    所以这里检测 stdin 是否可交互：不可交互就纯 sleep 守着。
    """
    interactive = sys.stdin is not None and sys.stdin.isatty()
    try:
        while True:
            if proc.poll() is not None:
                print()
                warn("服务进程已退出（返回码 %s）" % proc.returncode)
                break
            if interactive:
                # 可交互时也别一直占着输入 —— 只在进程活着时轮询
                time.sleep(0.5)
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        print()
        info("正在停止…")
        stop_all()


# ══════════════════════════════════════════════════════════════════
# 菜单
# ══════════════════════════════════════════════════════════════════
def status_brief():
    running = port_in_use(PORT)
    pids = load_pids()
    if running:
        pid = who_takes_port(PORT)
        print("  状态   " + C_OK + "运行中" + C_RESET +
              ("（PID %s）" % pid if pid else ""))
    else:
        print("  状态   " + C_DIM + "未运行" + C_RESET)
    print("  端口   %d" % PORT)
    print("  访问码 %s" % ACCESS_CODE)
    print("  模型   " + ("Ollama 在线" if ollama_alive() else C_WARN + "Ollama 未启动" + C_RESET))
    cf = find_cloudflared()
    print("  隧道   " + ("cloudflared 可用" if cf else C_DIM + "cloudflared 未安装" + C_RESET))
    if os.path.isfile(LINK_FILE):
        try:
            with open(LINK_FILE, encoding="utf-8") as f:
                last = f.read().strip()
            if last:
                print("  上次链接 " + C_DIM + last + C_RESET)
        except Exception:
            pass


def menu():
    while True:
        print()
        line("═")
        print("  " + C_BOLD + "电脑选机助手 · 启动器" + C_RESET)
        line("═")
        status_brief()
        line()
        print("  " + C_BOLD + "1" + C_RESET + "  本机自用      " +
              C_DIM + "http://127.0.0.1:%d" % PORT + C_RESET)
        print("  " + C_BOLD + "2" + C_RESET + "  局域网共享    " +
              C_DIM + "同 WiFi 的设备都能连" + C_RESET)
        print("  " + C_BOLD + "3" + C_RESET + "  公网测试链接  " +
              C_DIM + "生成 https 链接，发给测试同学" + C_RESET)
        line()
        print("  " + C_BOLD + "4" + C_RESET + "  停止服务")
        print("  " + C_BOLD + "5" + C_RESET + "  只做检查，不启动")
        print("  " + C_BOLD + "0" + C_RESET + "  退出")
        line()
        try:
            ch = input("  选一个 [1/2/3] ").strip() or "1"
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if ch == "1":
            run_local()
        elif ch == "2":
            run_lan()
        elif ch == "3":
            run_public()
        elif ch == "4":
            print()
            stop_all()
            input("\n  按回车返回菜单…")
        elif ch == "5":
            print()
            preflight(need_tunnel=False)
            print()
            preflight.__wrapped__ if False else None
            if find_cloudflared():
                ok("cloudflared 在位，公网模式可用")
            else:
                warn("cloudflared 未安装，公网模式不可用")
            input("\n  按回车返回菜单…")
        elif ch == "0":
            return
        else:
            warn("没有这个选项")


def main():
    # 直接带参数调用时跳过菜单，方便做快捷方式 / 计划任务
    #   python launcher.py local | lan | public | stop | check
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if arg == "stop":
        stop_all()
        return
    if arg == "check":
        preflight(need_tunnel=False)
        print()
        if find_cloudflared():
            ok("cloudflared 在位，公网模式可用")
        else:
            warn("cloudflared 未安装，公网模式不可用")
        return
    if arg in ("local", "lan", "public"):
        {"local": run_local, "lan": run_lan, "public": run_public}[arg]()
        return

    try:
        menu()
    except KeyboardInterrupt:
        print()
        info("已退出。服务如果还在跑，用 停止助手.bat 或选 4 停掉。")


if __name__ == "__main__":
    main()