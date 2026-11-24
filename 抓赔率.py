# -*- coding: utf-8 -*-
"""抓竞彩官方赔率快照（免费，不限次）

每场存：胜平负 / 让球 / 比分 / 总进球 / 半全场 + 涨跌标记
写进 赔率历史.csv，一天两次（开盘 AM / 收盘 PM）就能算变盘幅度
"""
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
OUT = "赔率历史.csv"
# 用阿里云自己的函数（noodds=1 不消耗任何额度），GitHub Actions 在海外也能访问
API = "https://jcdata-pxigzkgjcb.cn-hangzhou.fcapp.run?noodds=1"
H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.lottery.gov.cn/",
}

COLS = ["抓取时间", "时段", "业务日", "编号", "联赛", "主队", "客队", "开球",
        "状态", "胜", "平", "负", "主涨跌", "平涨跌", "客涨跌",
        "让球线", "让胜", "让平", "让负",
        "比分赔率", "总进球赔率", "半全场赔率"]


def g(d, *keys, default=""):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return default if d is None else d


def grab():
    req = urllib.request.Request(API, headers=H)
    raw = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "ignore")
    d = json.loads(raw)
    if not d.get("success"):
        raise RuntimeError("接口返回失败：" + str(d)[:200])
    out = []
    for grp in d["value"].get("matchInfoList") or []:
        for m in grp.get("subMatchList") or []:
            had = m.get("had") or {}
            hhad = m.get("hhad") or {}

            crs = {}
            for k, v in (m.get("crs") or {}).items():
                if not k.startswith("s") or k.endswith("f"):
                    continue
                try:
                    o = float(v)
                except (TypeError, ValueError):
                    continue
                if o > 1 and len(k) == 6:
                    crs[f"{int(k[1:3])}:{int(k[4:6])}"] = o

            ttg = {}
            for k, v in (m.get("ttg") or {}).items():
                if not k.startswith("s") or k.endswith("f"):
                    continue
                try:
                    o = float(v)
                except (TypeError, ValueError):
                    continue
                if o > 1:
                    ttg[k[1:]] = o

            hafu = {}
            for k, v in (m.get("hafu") or {}).items():
                if k in ("goalLine", "goalLineValue", "id",
                         "updateDate", "updateTime") or k.endswith("f"):
                    continue
                try:
                    o = float(v)
                except (TypeError, ValueError):
                    continue
                if o > 1:
                    hafu[k] = o

            out.append({
                "业务日": g(m, "businessDate"),
                "编号": g(m, "matchNumStr"),
                "联赛": g(m, "leagueAbbName"),
                "主队": g(m, "homeTeamAbbName"),
                "客队": g(m, "awayTeamAbbName"),
                "开球": (g(m, "matchDate") + " " + g(m, "matchTime")).strip(),
                "状态": g(m, "matchStatus"),
                "胜": g(had, "h"), "平": g(had, "d"), "负": g(had, "a"),
                "主涨跌": g(had, "hf"), "平涨跌": g(had, "df"), "客涨跌": g(had, "af"),
                "让球线": g(hhad, "goalLine"),
                "让胜": g(hhad, "h"), "让平": g(hhad, "d"), "让负": g(hhad, "a"),
                "比分赔率": json.dumps(crs, ensure_ascii=False, separators=(",", ":")),
                "总进球赔率": json.dumps(ttg, ensure_ascii=False, separators=(",", ":")),
                "半全场赔率": json.dumps(hafu, ensure_ascii=False, separators=(",", ":")),
            })
    return out


def main():
    now = datetime.now(CST)
    slot = "AM" if now.hour < 14 else "PM"
    stamp = now.strftime("%Y-%m-%d %H:%M")
    day = now.strftime("%Y-%m-%d")

    try:
        rows = grab()
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}：{e.read().decode('utf-8','ignore')[:300]}")
        sys.exit(1)
    except Exception as e:
        print("失败：", type(e).__name__, str(e)[:300])
        sys.exit(1)

    for r in rows:
        r["抓取时间"] = stamp
        r["时段"] = slot

    old = []
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8-sig", newline="") as f:
            old = list(csv.DictReader(f))

    key = lambda x: (str(x.get("抓取时间"))[:10], x.get("时段"), x.get("编号"))
    seen = {key(r) for r in rows}
    kept = [r for r in old if key(r) not in seen]
    allrows = kept + rows

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for r in allrows:
            w.writerow({c: r.get(c, "") for c in COLS})

    print(f"{stamp}（{slot}）：抓到 {len(rows)} 场，历史累计 {len(allrows)} 行")
    for r in rows[:5]:
        print(f"  {r['编号']:<8}{r['联赛']:<6}{r['主队']} vs {r['客队']:<10}"
              f"胜平负 {r['胜']}/{r['平']}/{r['负']}　让球 {r['让球线']} "
              f"{r['让胜']}/{r['让平']}/{r['让负']}")


if __name__ == "__main__":
    main()
