# ask_fit 修复：模型判断终于被采信（2026-11-17 晚）

> 用户质疑：「为什么要丢弃模型，不是说模型为主吗」
>
> 这个质疑是对的。我第一版只改了一处，**还有三处在丢弃模型**。

---

## 1. 问题

`ask_fit`（"用户在问匹配吗"）是**纯词表**：

```python
ask_fit = any(k in user_text for k in FIT_KW)     # agent.py:5123
```

而模型明明判了：

```python
intent_layer.route('限电 500W', focus=...)
# → {'intent': 'ask_current', 'topic': 'battery', 'fit': True}
```

`fit=True` 被丢弃 → 走 `answer_followup` → `topic=battery` 没数据
→ 退化报整机参数。用户看到「问限电答屏幕参数」。

---

## 2. 我第一版改错了：只改一处，还有三层拦截

第一版把 `ask_fit` 改成读模型，但实测**行为没变**。逐层排查后发现：

### 拦截点 ①：兜底分支没看 `ask_fit`

```python
if not (_give_cond or _model_other):
    reply = answer_followup(...)
    return {...}          # ← 这里直接 return 了
```

`限电500W` 的 `_give_cond=False`（没给条件动词）、`_model_other=False`
（intent 是 ask_current）→ 掉进这里，**后面的 `if o and ask_fit` 永远走不到**。

修法：兜底条件加 `and not ask_fit`。

### 拦截点 ②（真正的元凶）：detail 分支优先于 fit

改完 ① 还是没变。用 DEBUG 打出真值：

```
GATE detail=True fit=True fitq=False
```

**`ask_detail` 和 `ask_fit` 同时为 True**，而代码是：

```python
if o and ask_detail and not _is_fit_question(user_text):
    → 走 detail，答细节
```

detail 无条件优先。修法：加 `and not ask_fit`，**fit 优先**。

> 为什么这不会复活 2026-11-11 的旧事故（「它保修几年」被 `_answer_current`
> 接走编出"保修一年"）？因为那种句子模型给 `fit=False`，不受新条件影响。

### 拦截点 ③：`asks_about_current` 有状态依赖，导致我误判

排查中我在独立环境测 `asks_about_current('限电 500W')` 得到 `False`，
以为 detail 分支不会进。但 `_run` 里它返回 `True` ——
**这个函数读 `_LAST_ROUTE`，独立调用时那个是 None**。

教训：**测这类函数必须先跑一次 `route()` 填充状态，否则结论是假的。**

---

## 3. 修完发现新问题：模型开始编功耗

修好后回答变成：

> 「这台 16 寸的机器大概率跑不满，因为它的**功耗设计通常远超这个限制**」
> 「要么就得接受性能被**严重阉割**」「否则这台机器在限电环境下**根本没法正常用**」

查数据：

```python
o['adapterW']    # None
o['batteryWh']   # None
o['cpu']['maxW'] # None
o['gpu']['tgpMaxW']  # None
```

**这台机器没有任何功耗字段。** 上面每一句都是推测。

这比"编数字"更危险 —— 它读起来像内行经验，用户不会怀疑，
但它直接决定了"这台能不能买"。

### 修法：两层

**① 提示词加 8c 条**（禁推测，只报事实 + 指路）

**② 代码层加 `_power_guess()` 审计判据**（提示词没管住，实测模型照编）

```python
def _power_guess(text):
    # ① 功耗推测：出现"功耗"且带推测/断言语气
    if re.search(r"(功耗|瓦数|功率)\s*(设计|通常|一般|大概|大约|约为|超过|远超)", t):
        return "功耗推测"
    # ② 对供电够不够下结论
    if re.search(r"(限电|供电|插座|跳闸)", t) and \
            re.search(r"(跑不满|带不动|没法正常用|根本没法|严重阉割)", t):
        return "对限电下结论"
```

判据自测 6 条全对：

| 文本 | 期望 | 实际 |
|---|---|---|
| 这台大概率跑不满，功耗设计通常远超这个限制 | 拦 | ✓ |
| 整机满载功耗大约 200W | 拦 | ✓ |
| 限电 500W 的话根本没法正常用 | 拦 | ✓ |
| 标配 280W 电源适配器 | 放行 | ✓ |
| 库里没有整机功耗数据，我判断不了 | 放行 | ✓ |
| 宿舍限电通常还要给别的电器留余量 | 放行 | ✓ |

**关键区分**：「适配器标称 280W」是事实（有数据），
「整机功耗 200W」是推测（没数据）。

---

## 4. 顺带发现的结构性缺口

`_answer_current` **完全没有审计**。

`compose()` 有 `_audit`、`answer_followup` 有 `_f_audit`，
但 `_answer_current` 只有一个 `_bad_answer`（判据少得多，
而且是我这次才把 `_power_guess` 加进去的）。

这意味着「匹配判断」这条路径的出口把关最松 ——
而它恰恰是最容易编造的地方（要判断"够不够用"）。

**下次可以考虑把三条路径的审计统一成一个函数**
（项目里已经因为"判据抄两遍会漂"吃过好几次亏）。

---

## 5. 修复效果对比

**修前**（模型判断被丢弃）
```
U: 限电 500W
A: 联想拯救者，i7 14650HX · 32G + 1T · RTX4050 16 寸 2560×1600 IPS 165Hz
   100% sRGB。这一项的具体表现要看实机或第三方评测。
   ↑ 问限电答屏幕参数
```

**修后**（模型判断被采信 + 编造被拦）
```
U: 限电 500W
A: 联想拯救者，i7 14650HX · 32G + 1T · RTX4050 16 寸 2560×1600 IPS
   165Hz 100% sRGB，到手 ¥8099。就你的用途看，匹配。原因：这台本身就是
   游戏本，按游戏配的机器；库里没有游戏帧数数据，具体能跑什么帧数我说不准。
   ↑ 走了 fit 分支，只报事实，不推测功耗
```

**还差一步**：现在它没直接回答"限电 500W 够不够"。正确做法应该是：

> 标配 280W 适配器（如果有）；库里没有整机实测功耗，所以能不能用我判断不了；
> 建议查厂商规格页的"功耗"或问客服要满载功耗。

这需要 `_answer_current` 在 `dormWatt` 存在时给一条**专门的事实通道**。
见 §6。

---

## 6. 下一步

### P0：`_answer_current` 加限电事实通道

用户给了 `dormWatt`，就该：
1. 报 `adapterW`（如果有，这是唯一有数据支撑的参考）
2. 明说"库里没有整机功耗"
3. 指路（厂商规格页 / 客服）

而不是像现在这样——被审计拦掉推测后，干脆不提限电了。

### P1：三条路径的审计统一

`compose` / `answer_followup` / `_answer_current` 各有一套判据，
项目已经因为"判据抄两遍会漂"吃过 6 次亏。这次 `_power_guess`
只加在 `_answer_current`，另两条路径同样会编功耗。

### P2：补功耗数据

`data/zol/params-*.jsonl` 里有没有功耗相关键？值得扫一遍。
（`adapterW` 已经从「电源适配器」抓到了 99 台，但那是适配器不是整机功耗。）

---

## 7. 回归

```
test_scope       18/18
test_named       124/124
test_multiturn   12/12
test_attack6     87/87
test_weight      28/28
test_source      64/64
test_answer      35/36   ← 存量
test_neg         144/144
test_fuzz        0 违规 / 60
test_intent      73/74   ← 存量
```

---

## 8. 这轮排查的教训

1. **用户说"不是说模型为主吗"时，要顺着这句去查，不要急着解释架构。**
   我第一版改完就以为好了，实测没变才发现还有三层。
2. **DEBUG 打真值，不要靠推理。** 我推理了三轮都错
   （以为 `ask_detail=False`、以为兜底不触发、以为 `ask_fit` 没赋值），
   打出 `GATE detail=True fit=True` 一行就定位了。
3. **测有状态依赖的函数要先填充状态。** `asks_about_current` 读
   `_LAST_ROUTE`，独立调用永远是 False，差点让我走错方向。
4. **修好一个 bug 会暴露下一个。** 模型判断被采信后立刻开始编功耗 ——
   这不是退步，是**之前被"死板"掩盖的问题浮出来了**。
