# -*- coding: utf-8 -*-
"""GitHub Actions 里跑：读 今日预测.json，我 + Fable 各算一遍，写 台账_Fable.csv"""
import importlib.util
import json
import os
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location("m", "预测.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

SER = ["胜胜", "胜平", "胜负", "平胜", "平平", "平负", "负胜", "负平", "负负"]
RES = ["主胜", "平", "客胜"]
KEY = os.environ.get("FABLE_KEY", "").strip()
MODEL = os.environ.get("FABLE_MODEL", "anthropic/claude-fable-5.2")
URL = "https://openrouter.ai/api/v1/chat/completions"
OUT = "台账_Fable.csv"

PROMPT = """下面是今天中国竞彩的足球比赛。请对每一场给出你的独立预测。

{rows}

严格按这个 JSON 数组输出，不要任何解释文字，不要 markdown：
[{{"num":"001","pick":"主胜","p":[0.52,0.26,0.22],"hf":"胜胜","score":"2:1","hc":"让负"}}]

字段：num 照抄；pick 只能是 主胜/平/客胜；p 是三个概率和为一；
hf 只能是 胜胜/胜平/胜负/平胜/平平/平负/负胜/负平/负负；
score 格式 主:客；hc 只能是 让胜/让平/让负。"""


def my_predict(eu, line):
    eu = np.array(eu, float)
    inv = 1 / eu
    p0 = inv / inv.sum()
    ps = p0 ** (1 / M.SHARPEN_T)
    ps = ps / ps.sum()
    lh, la = M.fit_lambda(p0)
    G = M.dc_grid(lh, la)
    hf = M.htft(lh, la)
    n = G.shape[0]
    top = sorted(((G[i, j], i, j) for i in range(n) for j in range(n)), reverse=True)
    rp = [0, 0, 0]
    for i in range(n):
        for j in range(n):
            t = i - j + line
            rp[0 if t > 0 else (1 if t == 0 else 2)] += G[i, j]
    k = int(np.argmax(hf))
    return {"pick": RES[int(np.argmax(ps))], "pmax": float(ps.max()),
            "hf": SER[k], "score": f"{top[0][1]}:{top[0][2]}",
            "scoreP": float(top[0][0]),
            "hc": ["让胜", "让平", "让负"][int(np.argmax(rp))]}


def extract_json(txt):
    if not txt:
        return None
    s = txt.strip()
    i, j = s.find("["), s.rfind("]")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            pass
    return None


def ask_fable(matches):
    rows = "\n".join(
        f'{m["num"]} {m["lg"]} {m["h"]} vs {m["a"]}　'
        f'欧赔 {m["eu"][0]}/{m["eu"][1]}/{m["eu"][2]}　让球 {m["line"]:+d}'
        for m in matches)
    body = json.dumps({"model": MODEL, "max_tokens": 4000, "temperature": 0.2,
                       "messages": [{"role": "user",
                                     "content": PROMPT.format(rows=rows)}]}).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + KEY,
        "HTTP-Referer": "https://github.com/wg-rj/lanshuy",
        "X-Title": "lanshu-dual"})
    try:
        r = urllib.request.urlopen(req, timeout=180)
        d = json.loads(r.read().decode("utf-8", "ignore"))
        txt = d["choices"][0]["message"]["content"]
        u = d.get("usage") or {}
        print(f"Fable 用量 in={u.get('prompt_tokens')} out={u.get('completion_tokens')}")
        return extract_json(txt), txt
    except urllib.error.HTTPError as e:
        print("Fable HTTP", e.code, e.read().decode("utf-8", "ignore")[:400])
    except Exception as e:
        print("Fable 失败", type(e).__name__, str(e)[:200])
    return None, None


def main():
    raw = json.load(open("今日预测.json", encoding="utf-8"))
    ms = []
    for x in raw.get("matches") or []:
        eu = x.get("eu") or []
        if len(eu) != 3:
            continue
        try:
            line = int(float(x.get("line") or -1))
        except Exception:
            line = -1
        ms.append({"num": x.get("num", ""), "lg": x.get("lg", ""),
                   "h": x.get("h", ""), "a": x.get("a", ""),
                   "eu": [float(v) for v in eu], "line": line})
    print(f"今日 {len(ms)} 场（云端更新 {raw.get('updated')}）")
    if not ms:
        return

    fmap = {}
    if KEY:
        data, txt = ask_fable(ms)
        if data:
            for x in data:
                fmap[str(x.get("num", "")).strip()] = x
            print(f"Fable 回了 {len(fmap)} 场")
        elif txt:
            print("原始回复前 500 字：", txt[:500])
    else:
        print("没有 FABLE_KEY，只跑我这边")

    rows = []
    for m in ms:
        mine = my_predict(m["eu"], m["line"])
        f = fmap.get(m["num"], {})
        rows.append({
            "日期": raw.get("updated", "")[:10], "竞彩编号": m["num"],
            "联赛": m["lg"], "对阵": f'{m["h"]} vs {m["a"]}',
            "欧赔主": m["eu"][0], "欧赔平": m["eu"][1], "欧赔客": m["eu"][2],
            "让球线": m["line"],
            "我_方向": mine["pick"], "我_概率": round(mine["pmax"], 4),
            "我_半全场": mine["hf"], "我_比分": mine["score"],
            "我_比分率": round(mine["scoreP"], 4), "我_让球": mine["hc"],
            "F_方向": f.get("pick", ""), "F_半全场": f.get("hf", ""),
            "F_比分": str(f.get("score", "")), "F_让球": f.get("hc", ""),
            "实际比分": "", "实际半全场": "",
            "我_方向中": "", "我_半全中": "", "我_比分中": "", "我_让球中": "",
            "F_方向中": "", "F_半全中": "", "F_比分中": "", "F_让球中": "",
        })
        pp = f.get("p")
        rows[-1]["F_概率"] = round(max(float(v) for v in pp), 4) if (
            isinstance(pp, list) and len(pp) == 3) else ""

    df = pd.DataFrame(rows)
    import os.path
    if os.path.exists(OUT):
        old = pd.read_csv(OUT, encoding="utf-8-sig")
        old = old[~old.set_index(["日期", "竞彩编号", "对阵"]).index.isin(
            df.set_index(["日期", "竞彩编号", "对阵"]).index)]
        df = pd.concat([old, df], ignore_index=True)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")

    print()
    print(f"{'编号':<8}{'对阵':<22}{'我的':<6}{'概率':>7}　{'Fable':<6}{'F概率':>7}　{'F比分':<6}")
    print("-" * 76)
    for r in rows:
        fp = r["F_概率"]
        print(f'{r["竞彩编号"]:<8}{r["对阵"]:<22}{r["我_方向"]:<6}'
              f'{r["我_概率"]*100:>6.1f}%　{(r["F_方向"] or "—"):<6}'
              f'{(fp*100 if isinstance(fp, float) else 0):>6.1f}%　'
              f'{r["F_比分"] or "—":<6}')
    print()
    print(f"写入 {OUT}（{len(rows)} 场）")


if __name__ == "__main__":
    main()
