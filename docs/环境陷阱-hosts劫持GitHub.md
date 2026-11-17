# 环境陷阱：hosts 文件劫持了 GitHub（2026-11-17）

> **这个坑害我误判了一整晚。** 记下来，免得下次又以为"网络不通"。

---

## 现象

```powershell
Test-NetConnection -ComputerName github.com -Port 443 -InformationLevel Quiet
# → False（永远 False）

git push origin main
# → fatal: unable to access 'https://github.com/...':
#   Failed to connect to github.com:443 after 2077 ms: Could not connect to server
```

**看起来就是网络不通。** 我据此判断"用户加速器没开好"，
让子智能体重试 20 分钟 —— 那 20 分钟纯属浪费。

---

## 真实原因

`C:\Windows\System32\drivers\etc\hosts` 里有 **29 条 GitHub 域名**全部指向 `127.0.0.1`：

```
127.0.0.1 github.dev
127.0.0.1 api.github.com
127.0.0.1 github.githubassets.com
127.0.0.1 support-assets.githubassets.com
127.0.0.1 education.github.com
127.0.0.1 resources.github.com
...（共 29 条）
```

而本机 **443 端口没有任何监听** → 连接被立即拒绝。

`Test-NetConnection` 查的是域名，域名被 hosts 指向 127.0.0.1，
所以永远 False。**跟网络、跟加速器都没关系。**

### 顺带发现

这 118 行的 hosts 文件里还有 Steam、raw.githubusercontent.com 等条目，
像是某个"屏蔽/加速"工具或 hosts 管理软件的残留。

---

## 验证方法（判断是"真不通"还是"被劫持"）

```powershell
# 1. 看 hosts 里有没有这个域名
Select-String -Path 'C:\Windows\System32\drivers\etc\hosts' -Pattern 'github'

# 2. 绕过 DNS，直接测真实 IP
Test-NetConnection -ComputerName 140.82.113.3 -Port 443 -InformationLevel Quiet
# → True 表示真实 GitHub 可达，是 hosts 在捣鬼
```

实测四个 GitHub IP 全部 TCP_OK：
`140.82.113.3`、`140.82.114.3`、`140.82.112.3`、`20.205.243.166`。

---

## 绕过方式（不改任何文件、不改 git config）

**单次命令级参数**，用 `curloptResolve` 强制解析到真实 IP：

```powershell
git -C 'E:\电脑涨价分析' `
    -c http.curloptResolve=github.com:443:140.82.113.3 `
    push origin main
```

原理：`curloptResolve` 让 curl 对 `github.com` 强制用指定 IP 建连，
**SNI 仍然是 `github.com`**，所以 TLS 握手正常，证书校验通过。

验证也用同样参数：

```powershell
git -C 'E:\电脑涨价分析' `
    -c http.curloptResolve=github.com:443:140.82.113.3 `
    ls-remote origin refs/heads/main
```

> ⚠️ `gh api` 走的是 `api.github.com`，同样被劫持。
> 要么也加 resolve 参数（gh 不支持），要么直接用 `git ls-remote` 验证 ——
> 后者更直接，返回的就是远端真实 hash。

---

## 为什么不建议直接改 hosts

改 hosts 需要管理员权限，而且：

1. **不知道是谁写的** —— 可能是用户装的某个工具，删了会影响那个工具
2. **可能有其他用途** —— 比如屏蔽某些站点
3. 单次 `-c` 参数已经够用，没必要动系统文件

**建议用户自己决定要不要清理。** 我这边只记录，不动手。

---

## 教训

1. **`Test-NetConnection` 返回 False 不等于网络不通** ——
   先查 hosts，再怪网络。
2. **"重试"是最差的排查手段** —— 如果是配置问题，重试 100 次也一样。
   应该先问"为什么连不上"，而不是"再试一次"。
3. **报错信息会骗人** —— `Failed to connect after 2077 ms` 看起来像超时，
   实际是**连接被拒绝**（127.0.0.1 上没人监听，立即 RST）。
   "2077 ms" 那个数字是 curl 的重试耗时，不是 TCP 超时。

---

## 附：本次推送记录

用 resolve 参数一次成功：

```
3c7d3ee..21425f6  main -> main
```

4 个提交全部到达远端：

| hash | 提交 |
|---|---|
| `a2a6a3a` | fix: ask_fit 采信模型判断 |
| `a744d32` | feat: 审计判据矩阵工具 |
| `9ce38dd` | fix: 限电问题给出诚实回答 |
| `21425f6` | feat(data): 补 CPU TDP 与显卡 TGP |

远端 HEAD = `21425f628d9d9c1c52b90fccd75f726d929aa3d8`，与本地一致（`0 0`）。
