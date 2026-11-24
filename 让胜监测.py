# -*- coding: utf-8 -*-
"""监测：让球 +1 盘、让胜、赔率 1.8-2.2

历史上 677 场，49.8% 命中，期望 0.974（比整体 0.877 好，但仍 <1）
攒到 300 场再判断这是不是真偏差

读 赔率历史.csv，写 让胜监测.csv
"""
import csv
import json
import os
import urllib.request

OUT = "让胜监测.csv"
SRC = "赔率历史.csv"
API = ("https://webapi.sporttery.cn/gateway/uniform/football/"
       "getUniformMatchResultV1.qry?matchBeginDate={a}&matchEndDate={b}"
       "&leagueId=&pageSize=100&pageNo=1&isFix=0&matchPage=1&pcOrWap=1")
H = {"User-Agent": "Mozilla/5.0 Chrome/120", "Accept": "application/json",
     "Referer": "https://www.lottery.gov.cn/jc/zqsgkj/"}
COLS = ["日期", "编号", "主队", "客队", "让球线", "让胜赔率", "让胜概率",
        "标准赔率", "边缘", "实际比分", "差", "命中", "回报"]


def norm(s):
    s = str(s or "")
    for w in ["FC", "队", " ", "城", "联", "竞技"]:
        s = s.replace(w, "")
    return s


def fnum(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def main():
    if not os.path.exists(SRC):
        print("还没有 赔率历史.csv")
        return
    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
    # 每场取最新快照
    best = {}
    for r in rows:
        k = (r.get("业务日", ""), r.get("编号", ""))
        if k not in best or (r.get("时段") == "PM" and best[k].get("时段") != "PM"):
            best[k] = r

    picks = []
    for r in best.values():
        line = str(r.get("让球线", "")).strip()
        if line not in ("+1", "1"):
            continue
        o = fnum(r.get("让胜"))
        if o is None or not (1.8 <= o < 2.2):
            continue
        picks.append(r)
    print(f"快照 {len(best)} 场　命中条件（+1 盘 / 让胜 1.8-2.2）{len(picks)} 场")
    if not picks:
        print("今天没有符合条件的场次")
        return

    dates = {r.get("业务日", "")[:10] for r in picks}
    pool = {}
    for day in sorted(dates):
        try:
            j = json.loads(urllib.request.urlopen(urllib.request.Request(
                API.format(a=day, b=day), headers=H), timeout=30)
                .read().decode("utf-8", "ignore"))
        except Exception as e:
            print(f"  {day} 拉赛果失败 {str(e)[:60]}")
            continue
        for m in j["value"].get("matchResult") or []:
            full = str(m.get("sectionsNo999") or "").replace(":", "-")
            if "-" in full:
                pool[norm(m.get("homeTeam")) + "|" + norm(m.get("awayTeam"))] = full

    old = []
    if os.path.exists(OUT):
        old = list(csv.DictReader(open(OUT, encoding="utf-8-sig")))
    key = lambda x: (x.get("日期"), x.get("编号"))
    have = {key(x) for x in old}

    added = 0
    recs = []
    for r in picks:
        k = (r.get("业务日", "")[:10], r.get("编号", ""))
        if k in have:
            continue
        full = pool.get(norm(r.get("主队")) + "|" + norm(r.get("客队")))
        o = fnum(r.get("让胜"))
        rec = {"日期": k[0], "编号": k[1], "主队": r.get("主队", ""),
               "客队": r.get("客队", ""), "让球线": "+1", "让胜赔率": r.get("让胜", ""),
               "让胜概率": "", "标准赔率": "", "边缘": "",
               "实际比分": "", "差": "", "命中": "", "回报": ""}
        if isinstance(r.get("比分赔率"), str) and r.get("比分赔率"):
            try:
                crs = json.loads(r["比分赔率"])
                inv = {kk: 1 / float(vv) for kk, vv in crs.items() if float(vv) > 1}
                s = sum(inv.values())
                if s > 0:
                    rec["让胜概率"] = ""  # 比分盘不直接给让胜概率，留空
            except Exception:
                pass
        rec["标准赔率"] = round(1 / 0.498, 3)
        rec["边缘"] = round(o - 1 / 0.498, 3)
        if full:
            hg, ag = map(int, full.split("-"))
            rec["实际比分"] = full
            rec["差"] = hg - ag
            hit = 1 if (hg - ag + 1) > 0 else 0
            rec["命中"] = hit
            rec["回报"] = round(hit * o, 3)
        recs.append(rec)
        added += 1

    allrows = old + recs
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for x in allrows:
            w.writerow({c: x.get(c, "") for c in COLS})

    print(f"新增 {added} 条，累计 {len(allrows)} 条 → {OUT}")
    print()
    for x in recs:
        mark = "✓" if x["命中"] == 1 else ("✗" if x["命中"] == 0 else "待赛")
        print(f"  {x['日期']} {x['编号']} {x['主队']} vs {x['客队']}　"
              f"让胜 {x['让胜赔率']}　{x['实际比分'] or '未完'} {mark}")

    done = [x for x in allrows if str(x.get("命中")) in ("0", "1")]
    if done:
        hits = sum(int(x["命中"]) for x in done)
        ret = sum(float(x["回报"] or 0) for x in done) / len(done)
        print()
        print(f"累计已完赛 {len(done)} 场：命中 {hits}（{hits/len(done):.1%}）"
              f"　期望 {ret:.3f}")
        print(f"  基线：历史 677 场 49.8%，期望 0.974")
        if len(done) < 300:
            print(f"  还差 {300-len(done)} 场才够下结论")


if __name__ == "__main__":
    main()
