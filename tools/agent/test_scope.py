# -*- coding: utf-8 -*-
"""品类红线的独立单测（2026-11-13 新增）。

## 为什么有这个文件

2026-11-13 早上接手时实跑，抓到一条 P0：

    「预算 6000 左右，轻薄本，上课用」
    → 首推 **MacBook Air 13 M4（¥6161.65）**

它同时违反两条写死的事实：
  ① PRD §6：手机 / 平板 / 工作站 / Mac —— 明确拒答
  ② 页面首屏：只限 Windows 笔记本 / 台式整机 / 内存固态

根因不是判据写错，而是**判据管不到的地方**：`machine_pool.json` 里混着 4 台
苹果机，`cat` 写着 `thin`，于是它们和普通轻薄本一起排队、还排到了第一。
出口审计确实报了 `库外机型:['MacBook']`，但两次重写仍命中 →
落到 `template_reply(first)`，而模板里的机名**就是 MacBook** ——
检出了，还是发出去了。

## 这些用例守的是什么

修法是把范围判断放在**取数口子**（`core.pool_offers`），不是出口。
所以这里测三件事，缺一不可：

  1. **结构**：推荐池（merged_offers）里一台苹果机都没有
  2. **分辨力**：故意喂一台假 Mac，判据必须判 True（否则它只是个常量 False）
  3. **不误伤**：正常 Windows 机、brand 缺失的机器，必须判 False

第 2、3 条是宪法第 4 条要的对照实验 —— 只测"现在池子里没有 Mac"是不够的：
把判据写成 `return False` 也能过，那不叫证据。
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core
import agent

OK = 0
BAD = 0
FAILS = []


def ck(name, cond, note=""):
    global OK, BAD
    if cond:
        OK += 1
    else:
        BAD += 1
        FAILS.append((name, note))


# ---------- 1. 结构：推荐池里不许有苹果机 ----------
pool = core.merged_offers()
macs = [o["name"] for o in pool
        if core.is_out_of_scope_offer(o)
        or "mac" in (o.get("name") or "").lower()
        or "apple" in (o.get("chip") or "").lower()]
ck("推荐池无苹果机", not macs, str(macs[:4]))
ck("推荐池总数 = 93", len(pool) == 93, "实际 %d" % len(pool))

# 挡掉的机器必须被记下来（不是"文件里本来就没有"）
ps = core.pool_stats()
ck("采集池挡掉 4 台范围外机型", ps.get("outOfScope") == 4, str(ps))
ck("采集池进池 = 76 台", ps.get("total") == 76, str(ps.get("total")))

# ---------- 2. 分辨力对照：故意喂假 Mac，必须判 True ----------
fake_mac_brand = {"id": "x1", "name": "某机型 14", "brand": "Apple", "chip": "M4"}
fake_mac_name = {"id": "x2", "name": "MacBook Pro 14 M5（16G/1T）", "brand": "", "chip": ""}
fake_mac_chip = {"id": "x3", "name": "某机器", "brand": "", "chip": "Apple M4"}
ck("分辨力：brand=Apple → 挡", core.is_out_of_scope_offer(fake_mac_brand) is True)
ck("分辨力：名字含 MacBook → 挡", core.is_out_of_scope_offer(fake_mac_name) is True)
ck("分辨力：芯片是 Apple M4 → 挡", core.is_out_of_scope_offer(fake_mac_chip) is True)

# ---------- 3. 不误伤：Windows 机 / 缺字段的机器必须判 False ----------
fake_win = {"id": "y1", "name": "联想小新 16", "brand": "联想", "chip": "R7 H255"}
fake_nobrand = {"id": "y2", "name": "战66 八代 14 英寸", "brand": "", "chip": ""}
ck("不误伤：普通 Windows 机", core.is_out_of_scope_offer(fake_win) is False)
ck("不误伤：brand 缺失不能当范围外", core.is_out_of_scope_offer(fake_nobrand) is False)
ck("不误伤：空 dict 也不许抛异常",
   core.is_out_of_scope_offer({}) is False)

# ---------- 4. 句级：用户点名问 Mac → 范围外拒答（纯规则，不依赖模型） ----------
for q in ["MacBook Air 值不值得买", "苹果笔记本和这个比呢", "我想买 mac "]:
    ck("范围外拒答：" + q, agent.check_scope(q) is not None,
       "check_scope 没命中")
# 对照：正常问句不许被范围外拦掉
for q in ["预算 6000 的轻薄本", "联想小新 16 怎么样", "学设计的五千块"]:
    ck("不误拦：" + q, agent.check_scope(q) is None, "被误判成范围外")

# ---------- 5. 端到端（确定性）：同一个问题，首位不许是苹果机 ----------
r = core.build_recommendation({"cat": "thin", "budget": 6000})
names = [o["name"] for o in r.get("ranked") or []]
ck("6000 轻薄本首推不是苹果机",
   bool(names) and not any("mac" in n.lower() for n in names),
   str(names[:4]))
ck("6000 轻薄本排序结果整体无苹果机",
   not any("mac" in n.lower() for n in names), str(names))

# 对照：这条查询在修复前确实会推 Mac —— 所以上面的断言必须能真的失败过。
# 证据不在测试里跑（要回滚代码），记在报告里：
#   logs/chat-2026-11-13.log 08:46:03 → picked=MacBook Air 13 M4（16G/256G）

print("\n" + "=" * 70)
print("通过 %d / %d" % (OK, OK + BAD))
if FAILS:
    print("\n失败明细：")
    for n, note in FAILS:
        print("  - %s   %s" % (n, note))
else:
    print("全绿。")
print("=" * 70)
