# -*- coding: utf-8 -*-
"""
云端预测：GitHub Actions 每天跑一次，产出 今日预测.json
不需要任何额度：竞彩用 noodds=1，欧赔从捷报抓 Coral
"""
import io, sys, os, re, json, time, difflib, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
import importlib.util


FCAPP = "https://jcdata-pxigzkgjcb.cn-hangzhou.fcapp.run?noodds=1"
JB = "https://m.zhiyunsports.com"
H_APP = {"User-Agent": "Mozilla/5.0"}
H_JB = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Mobile Safari/537.36",
        "Referer": JB + "/"}
OUT = "今日预测.json"

spec = importlib.util.spec_from_file_location("m", "预测.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

SER = ["胜胜", "胜平", "胜负", "平胜", "平平", "平负", "负胜", "负平", "负负"]
RES = ["主胜", "平", "客胜"]

ALIAS = {
    "曼联": "曼彻斯特联", "曼城": "曼彻斯特城", "巴萨": "巴塞罗那",
    "马竞": "马德里竞技", "国米": "国际米兰", "维拉": "阿斯顿维拉",
    "比利亚雷": "比利亚雷亚尔", "巴黎圣曼": "巴黎圣日尔曼",
    "纽喷气机": "纽卡斯尔喷气机", "珀斯": "珀斯光荣",
    "首尔FC": "首尔", "蔚山现代": "蔚山", "国际图尔": "国际图尔库",
    "沙尔克04": "沙尔克", "库奥皮奥": "库普斯", "布拉迪斯": "布拉迪斯拉发",
    "加拉塔萨": "加拉塔萨雷", "斯拉维亚": "布拉格斯拉维亚",
    "莱红牛": "RB莱比锡", "奥因茨": "美因茨", "埃沃斯堡": "埃尔沃斯堡",
}


def get(u, hdr, t=30):
    with urllib.request.urlopen(urllib.request.Request(u, headers=hdr), timeout=t) as r:
        return r.read().decode("utf-8", "ignore")


def norm(s):
    s = str(s or "")
    for w in ["FC", "队", " ", "　", "·", "（", "）", "(", ")", "04", "U21", "U19", "U20"]:
        s = s.replace(w, "")
    return s


def name_score(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 2.0
    long_, short_ = (na, nb) if len(na) >= len(nb) else (nb, na)
    if len(short_) >= 2 and short_ in long_ and len(short_) / len(long_) >= 0.35:
        return 1.8
    aa, bb = ALIAS.get(na, na), ALIAS.get(nb, nb)
    if aa == bb:
        return 1.9
    return difflib.SequenceMatcher(None, aa, bb).ratio()


# ---------- 1) 竞彩 ----------
print("① 拉竞彩")
d = json.loads(get(FCAPP, H_APP))
raw = []
for day in ((d.get("value") or {}).get("matchInfoList") or []):
    raw += (day.get("subMatchList") or [])

rows = []
for m in raw:
    if m.get("matchStatus") != "Selling":
        continue
    hd, hh = m.get("had") or {}, m.get("hhad") or {}
    if not (hd.get("h") and hd.get("d") and hd.get("a")):
        continue
    rows.append({
        "num": m.get("matchNumStr") or "",
        "lg": m.get("leagueAbbName") or "",
        "h": m.get("homeTeamAbbName") or "",
        "a": m.get("awayTeamAbbName") or "",
        "date": str(m.get("businessDate") or "")[:10],
        "mdate": str(m.get("matchDate") or "")[:10],
        "time": str(m.get("matchTime") or "")[:5],
        "line": hh.get("goalLine") or "",
        "ch": hh.get("h"), "cd": hh.get("d"), "ca": hh.get("a"),
        "jh": hd.get("h"), "jd": hd.get("d"), "ja": hd.get("a"),
    })
print(f"   在售 {len(rows)} 场")
if not rows:
    json.dump({"date": "", "matches": [], "note": "今天没有在售场次"}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False)
    sys.exit(0)

# ---------- 2) 捷报赛程 ----------
print("② 拉捷报赛程")
import datetime as dt
dates = set()
for r in rows:
    for k in ("date", "mdate"):
        try:
            x = dt.date.fromisoformat(r[k])
            for off in (-1, 0, 1):
                dates.add((x + dt.timedelta(days=off)).isoformat())
        except Exception:
            pass

idlist = {}
for dstr in sorted(dates):
    try:
        t = get(f"{JB}/results.htm?date={dstr}", H_JB, 25)
    except Exception:
        continue
    m = re.search(r'var\s+jsData\s*=\s*"([^"]{50,})"', t)
    lst = []
    if m:
        body = m.group(1).split("$", 1)[1] if "$" in m.group(1) else ""
        for rec in body.split("!"):
            f = rec.split("^")
            if len(f) >= 7 and re.match(r"^\d{6,8}$", f[0]) and re.match(r"^\d{14}$", f[3]):
                lst.append((f[0], f[5], f[6]))
    idlist[dstr] = lst
    print(f"   {dstr} {len(lst)} 场")

pool = []
for v in idlist.values():
    pool += v
print(f"   合计 {len(pool)} 场可匹配")

# ---------- 3) 匹配 ----------
print("③ 匹配")
for r in rows:
    best = (0, None)
    for mid, jh, ja in pool:
        ra = name_score(r["h"], jh)
        rb = name_score(r["a"], ja)
        if ra >= 0.5 and rb >= 0.5 and ra + rb > best[0]:
            best = (ra + rb, mid)
    r["mid"] = best[1] if best[0] >= 1.2 else None
got = sum(1 for r in rows if r["mid"])
print(f"   匹配 {got} / {len(rows)}")


# ---------- 4) 抓 Coral 欧赔（并发） ----------
def coral(mid):
    if not mid:
        return None
    try:
        h = get(f"{JB}/1x2Detail/{mid}_88.htm", H_JB, 20)
    except Exception:
        return None
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
        nums = re.findall(r">(\d+\.\d+)<", tr)
        if len(nums) >= 4:
            try:
                return [float(nums[0]), float(nums[1]), float(nums[2])]
            except Exception:
                continue
    return None


print("④ 抓欧赔（并发 6）")
with ThreadPoolExecutor(max_workers=6) as ex:
    odds = list(ex.map(lambda r: coral(r["mid"]), rows))
n_ok = 0
for r, o in zip(rows, odds):
    if o:
        r["oh"], r["od"], r["oa"] = o
        r["src"] = "eu"
        n_ok += 1
    else:
        try:
            S = 1 / float(r["jh"]) + 1 / float(r["jd"]) + 1 / float(r["ja"])
            r["oh"], r["od"], r["oa"] = (float(r["jh"]) * S, float(r["jd"]) * S, float(r["ja"]) * S)
            r["src"] = "jc"
        except Exception:
            r["oh"] = r["od"] = r["oa"] = None
            r["src"] = ""
print(f"   真欧赔 {n_ok}　竞彩兜底 {len(rows)-n_ok}")

# ---------- 5) 跑模型 ----------
print("⑤ 算概率")
out = []
for r in rows:
    try:
        eu = np.array([r["oh"], r["od"], r["oa"]], float)
        if not np.all(np.isfinite(eu)) or np.any(eu <= 1):
            continue
    except Exception:
        continue
    inv = 1 / eu
    p0 = inv / inv.sum()
    ps = p0 ** (1 / M.SHARPEN_T); ps = ps / ps.sum()
    lh, la = M.fit_lambda(p0)
    G = M.dc_grid(lh, la)
    hf = M.htft(lh, la)
    pick = int(np.argmax(ps))
    i, j = np.unravel_index(np.argmax(G), G.shape)
    # 与推荐方向一致的最可能比分（避免全是 1:1）
    best = (-1.0, 0, 0)
    for _a in range(G.shape[0]):
        for _b in range(G.shape[1]):
            _yy = 0 if _a > _b else (1 if _a == _b else 2)
            if _yy == pick and G[_a, _b] > best[0]:
                best = (G[_a, _b], _a, _b)
    k = int(np.argmax(hf))
    tg = np.array([G[np.minimum(np.add.outer(np.arange(G.shape[0]), np.arange(G.shape[1])), 7) == t].sum()
                   for t in range(8)])
    try:
        line = int(float(r["line"]))
    except Exception:
        line = 0
    rp = np.zeros(3)
    for a in range(G.shape[0]):
        for b in range(G.shape[1]):
            mm = a - b + line
            rp[0 if mm > 0 else (1 if mm == 0 else 2)] += G[a, b]
    out.append({
        "num": r["num"], "lg": r["lg"], "h": r["h"], "a": r["a"],
        "date": r["mdate"], "time": r["time"],
        "eu": [round(float(x), 2) for x in eu],
        "src": r["src"],
        "p": [round(float(x), 4) for x in ps],
        "pick": RES[pick], "prob": round(float(ps[pick]), 4),
        "tier": M.tier_1x2(float(ps[pick])),
        "hf": SER[k], "hfP": round(float(hf[k]), 4),
        "hf3": [[SER[t], round(float(hf[t]), 4)] for t in np.argsort(-hf)[:3]],
        "score": f"{best[1]}:{best[2]}", "scoreP": round(float(best[0]), 4),
        "scoreTop": f"{i}:{j}",
        "goals": int(np.argmax(tg)), "g4": round(float(tg[4:].sum()), 4),
        "rq": [round(float(x), 4) for x in rp],
        "rqPick": ["让胜", "让平", "让负"][int(np.argmax(rp))],
        "line": line,
        "draw": round(float(ps[1]), 4),
        "ev": round(float(ps[pick]) * float(1 / p0[pick]), 2),
    })

out.sort(key=lambda x: -x["prob"])
payload = {
    "updated": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
    "n": len(out),
    "euCount": sum(1 for x in out if x["src"] == "eu"),
    "matches": out,
}
json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print(f"\n⑥ 写出 {OUT}：{len(out)} 场（真欧赔 {payload['euCount']}）")
print(f"   文件 {os.path.getsize(OUT)/1024:.1f} KB")
