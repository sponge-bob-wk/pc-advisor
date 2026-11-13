import json, urllib.request, sys

sys.stdout.reconfigure(encoding='utf-8')
API = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.5-4b"
BASE = r"E:\电脑涨价分析\tools\eval"

ASK_SYS = """你在帮准大学生选电脑，已收集到部分信息。判断下一步最该问哪一个问题。

必问五问：
- Q1 专业定了吗？要跑什么软件（用途不明时问）
- Q2 宿舍限电吗？大概多少瓦（涉及游戏本时必问）
- Q3 要带去教室或图书馆吗（便携需求不明时问）
- Q4 寒暑假带回家吗（异地生源时问）
- Q5 四年内打算换吗（长期使用要考虑）

规则：信息已足够给出推荐时 next_question 填 null。每次只问一个。
只输出 JSON：{"next_question":"Q1|Q2|Q3|Q4|Q5|null","reason":"一句话理由"}"""


def chat(messages, fmt=None, num_predict=140, temperature=0):
    payload = {"model": MODEL, "stream": False, "think": False, "messages": messages,
               "options": {"num_predict": num_predict, "temperature": temperature}}
    if fmt:
        payload["format"] = fmt
    req = urllib.request.Request(API, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8")).get("message", {}).get("content", "")


cases = {c["id"]: c for c in json.load(open(BASE + r"\eval_set.json", encoding="utf-8"))["cases"]}

print("温度=0，每例 3 次，检验失败是否可复现")
print("-" * 62)
for cid in ["B10", "B12", "B01", "B04", "B13"]:
    case = cases[cid]
    msgs = [{"role": "system", "content": ASK_SYS},
            {"role": "user", "content": "已收集：" + json.dumps(case["known"], ensure_ascii=False)}]
    outs = []
    for _ in range(3):
        o = chat(msgs, fmt="json")
        try:
            outs.append(json.loads(o).get("next_question"))
        except Exception:
            outs.append("PARSE_ERR")
    stable = "稳定" if len(set(outs)) == 1 else "不稳定"
    verdict = "PASS" if outs[0] in case["expect_q"] else "FAIL"
    print("%s 期望%-12s 三次=%-22s %s %s" % (cid, str(case["expect_q"]), str(outs), stable, verdict))
