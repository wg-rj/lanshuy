# -*- coding: utf-8 -*-
"""模型 vs 市场：比分概率谁更准

读 赔率历史.csv（每场取最新快照）+ 官方赛果，逐场比较：
① 两边各自的首选比分命中
② 对数损失（越低越准）
③ 模型认为比市场高的比分，实际出现几次

用法：
    python 比分对比.py                只看汇总
    python 比分对比.py --detail       逐场列出
    python 比分对比.py --save         把逐场结果追加进 比分战绩.csv
"""
import argparse
import csv
import json
import math
import os
import urllib.request
from collections import defaultdict

import numpy as np
import importlib.util

spec = importlib.util.spec_from_file_location("m", "预测.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

ODDS_CSV = "赔率历史.csv"
OUT = "比分战绩.csv"
API = ("https://webapi.sporttery.cn/gateway/uniform/football/"
       "getUniformMatchResultV1.qry?matchBeginDate={a}&matchEndDate={b}"
       "&leagueId=&pageSize=100&pageNo=1&isFix=0&matchPage=1&pcOrWap=1")
H = {"User-Agent": "Mozilla/5.0 Chrome/120", "Accept": "application/json",
     "Referer": "https://www.lottery.gov.cn/jc/zqsgkj/"}

COLS = ["日期", "编号", "主队", "客队", "实际比分",
        "市场首选", "市场概率", "模型首选", "模型概率",
        "市场命中", "模型命中", "市场对数损失", "模型对数损失", "模型加分"]


def fnum(x):
    try:
        v = float(x)
        return v if v > 1 else None
    except (TypeError, ValueError):
        return None


def load_snapshots():
    if not os.path.exists(ODDS_CSV):
        return []
    rows = list(csv.DictReader(open(ODDS_CSV, encoding="utf-8-sig")))
    best = {}
    for r in rows:
        k = (r.get("业务日", ""), r.get("编号", ""))
        cur = best.get(k)
        if cur is None or (r.get("时段") == "PM" and cur.get("时段") != "PM"):
            best[k] = r
    return list(best.values())


def market_probs(crs_json):
    d = json.loads(crs_json) if crs_json else {}
    inv = {}
    for k, v in d.items():
        o = fnum(v)
        if o:
            inv[k] = 1.0 / o
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s > 0 else {}


def model_probs(row):
    eu = [fnum(row.get("胜")), fnum(row.get("平")), fnum(row.get("负"))]
    if any(x is None for x in eu):
        return {}, None
    eu = np.array(eu, float)
    inv = 1 / eu
    p0 = inv / inv.sum()
    lh, la = M.fit_lambda(p0)
    G = M.dc_grid(lh, la)
    out = {}
    for i in range(G.shape[0]):
        for j in range(G.shape[1]):
            out[f"{i}:{j}"] = float(G[i, j])
    return out, (lh, la)


def fetch_results(dates):
    got = {}
    for d in sorted(dates):
        try:
            u = API.format(a=d, b=d)
            j = json.loads(urllib.request.urlopen(
                urllib.request.Request(u, headers=H), timeout=30)
                .read().decode("utf-8", "ignore"))
        except Exception as e:
            print(f"  {d} 拉赛果失败 {str(e)[:60]}")
            continue
        for m in j["value"].get("matchResult") or []:
            full = str(m.get("sectionsNo999") or "").replace(":", "-")
            if "-" not in full:
                continue
            got[(m.get("homeTeam", ""), m.get("awayTeam", ""))] = full
    return got


def norm(s):
    s = str(s or "")
    for w in ["FC", "队", " ", "城", "联", "竞技"]:
        s = s.replace(w, "")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    rows = load_snapshots()
    if not rows:
        print("还没有 赔率历史.csv 数据")
        return
    dates = {r.get("业务日", "")[:10] for r in rows}
    print(f"快照 {len(rows)} 场，日期 {sorted(dates)}")
    res = fetch_results(dates)
    idx = {norm(h) + "|" + norm(w): v for (h, w), v in res.items()}

    recs = []
    for r in rows:
        full = idx.get(norm(r.get("主队")) + "|" + norm(r.get("客队")))
        if not full:
            continue
        act = full.replace("-", ":")
        mp = market_probs(r.get("比分赔率"))
        mo, lam = model_probs(r)
        if not mp or not mo:
            continue
        keys = [k for k in mp if k in mo]
        if len(keys) < 8 or act not in keys:
            continue
        sm = sum(mp[k] for k in keys)
        so = sum(mo[k] for k in keys)
        mp = {k: mp[k] / sm for k in keys}
        mo = {k: mo[k] / so for k in keys}
        bm = max(mp, key=lambda k: mp[k])
        bk = max(mo, key=lambda k: mo[k])
        recs.append({
            "日期": r.get("业务日", "")[:10], "编号": r.get("编号", ""),
            "主队": r.get("主队", ""), "客队": r.get("客队", ""),
            "实际比分": act,
            "市场首选": bm, "市场概率": round(mp[bm], 4),
            "模型首选": bk, "模型概率": round(mo[bk], 4),
            "市场命中": int(bm == act), "模型命中": int(bk == act),
            "市场对数损失": round(-math.log(max(mp.get(act, 1e-9), 1e-9)), 4),
            "模型对数损失": round(-math.log(max(mo.get(act, 1e-9), 1e-9)), 4),
            "模型加分": round(mo.get(act, 0) - mp.get(act, 0), 4),
        })

    print(f"有赛果的 {len(recs)} 场")
    if not recs:
        print("还没有已完赛的场次，等比赛结束再来。")
        return

    n = len(recs)
    hm = sum(x["市场命中"] for x in recs)
    hk = sum(x["模型命中"] for x in recs)
    lm = sum(x["市场对数损失"] for x in recs) / n
    lk = sum(x["模型对数损失"] for x in recs) / n
    print()
    print("=" * 52)
    print(f"{'':<12}{'市场':>15}{'模型':>15}")
    print("-" * 42)
    print(f"{'首选命中':<12}{f'{hm}/{n} = {hm/n:.1%}':>17}{f'{hk}/{n} = {hk/n:.1%}':>17}")
    print(f"{'对数损失':<12}{lm:>17.4f}{lk:>17.4f}")
    print()
    print("（对数损失越小越准；差 0.01 以上算有区别）")

    if a.detail:
        print()
        for x in recs:
            print(f"  {x['编号']:<8}{x['主队']} vs {x['客队']:<10} 实际 {x['实际比分']:<5}"
                  f" 市场 {x['市场首选']}{'✓' if x['市场命中'] else '✗'}"
                  f"　模型 {x['模型首选']}{'✓' if x['模型命中'] else '✗'}")

    if a.save:
        old = []
        if os.path.exists(OUT):
            old = list(csv.DictReader(open(OUT, encoding="utf-8-sig")))
        key = lambda x: (x.get("日期"), x.get("编号"))
        newkeys = {key(x) for x in recs}
        old = [x for x in old if key(x) not in newkeys]
        allrows = old + recs
        with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
            w.writeheader()
            for x in allrows:
                w.writerow({c: x.get(c, "") for c in COLS})
        print()
        print(f"写入 {OUT}（新增 {len(recs)}，累计 {len(allrows)}）")

        if len(allrows) >= 30:
            hm2 = sum(int(x["市场命中"]) for x in allrows)
            hk2 = sum(int(x["模型命中"]) for x in allrows)
            lm2 = sum(float(x["市场对数损失"]) for x in allrows) / len(allrows)
            lk2 = sum(float(x["模型对数损失"]) for x in allrows) / len(allrows)
            print(f"累计 {len(allrows)} 场：市场 {hm2} 中（{hm2/len(allrows):.1%}）"
                  f" 模型 {hk2} 中（{hk2/len(allrows):.1%}）")
            print(f"对数损失 市场 {lm2:.4f}　模型 {lk2:.4f}")


if __name__ == "__main__":
    main()
