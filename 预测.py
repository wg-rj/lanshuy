# -*- coding: utf-8 -*-
"""预测.py —— 填 待预测.xlsx，出 预测结果.xlsx

必填：日期 / 联赛 / 主队 / 客队 / 欧赔主 / 欧赔平 / 欧赔客
选填：让球（如 -1、+1）、让球主/平/客、竞彩主/平/客（填了就多算 EV）

模型：欧赔去水 → Dixon-Coles 泊松反解 λ → 比分矩阵
      → 胜平负 / 让球 / 比分 / 总进球 / 半全场（上半场占比 0.4214，官方 785 场校准）
"""
import io, sys, os, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import numpy as np, pandas as pd
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

IN = "待预测.xlsx"
OUT = "预测结果.xlsx"
RHO = -0.05
S_HT = 0.4424
HTFT_BOOST_FS = 1.4   # 负胜（半场客领先→全场主胜）加权
HTFT_BOOST_SF = 1.2   # 胜负（半场主领先→全场客胜）加权          # 上半场进球占比
STRETCH = 0.06          # 主客进球差的拉伸系数          # 上半场进球占比（官方 785 场实测 0.4214）
KMAX = 11
SERIES = ["胜胜", "胜平", "胜负", "平胜", "平平", "平负", "负胜", "负平", "负负"]

K = np.arange(KMAX)
FACT = np.array([math.factorial(i) for i in range(KMAX)], float)
GRID = np.round(np.arange(0.20, 3.61, 0.05), 2)
LH, LA = np.meshgrid(GRID, GRID, indexing="ij")
II = K[:, None] * np.ones((1, KMAX), int)
JJ = np.ones((KMAX, 1), int) * K[None, :]
UP, EQ = II > JJ, II == JJ


def pois(lam):
    lam = np.asarray(lam, float)
    return np.exp(-lam)[..., None] * (lam[..., None] ** K) / FACT


def dc_grid(lh, la):
    """给标量 λ，返回 K×K 比分矩阵"""
    m = np.outer(pois(lh), pois(la))
    m[0, 0] *= 1 - lh * la * RHO
    m[0, 1] *= 1 + lh * RHO
    m[1, 0] *= 1 + la * RHO
    m[1, 1] *= 1 - RHO
    return m / m.sum()


# 预建网格上的 1X2，用于反解 λ
_m0 = pois(LH)[..., :, None] * pois(LA)[..., None, :]
_m0 = _m0 * np.where((II == 0) & (JJ == 0), 1 - LH[..., None, None] * LA[..., None, None] * RHO, 1.0)
_m0 = _m0 * np.where((II == 0) & (JJ == 1), 1 + LH[..., None, None] * RHO, 1.0)
_m0 = _m0 * np.where((II == 1) & (JJ == 0), 1 + LA[..., None, None] * RHO, 1.0)
_m0 = _m0 * np.where((II == 1) & (JJ == 1), 1 - RHO, 1.0)
_m0 = _m0 / _m0.sum(axis=(-1, -2), keepdims=True)
TB = np.stack([_m0[..., UP].sum(-1), _m0[..., EQ].sum(-1), _m0[..., ~(UP | EQ)].sum(-1)], -1)


def target_total(p_home, p_draw):
    """总进球经验值（三次+交互，5,861 场拟合）。
    旧版是二次，测试段（2,024 场）三项全面更差；三次版：
      比分 LL 2.9224→2.9215、总进球 1.8612→1.8609、半全场 1.8193→1.8189。"""
    x = (4.860176 + 49.726868 * p_home - 33.017954 * p_draw
         - 55.647062 * p_home ** 2 + 45.720000 * p_draw ** 2 - 146.291582 * p_home * p_draw
         + 6.616203 * p_home ** 3 + 35.885996 * p_draw ** 3
         + 103.161029 * p_home ** 2 * p_draw + 97.962465 * p_home * p_draw ** 2)
    return min(max(x, 1.7), 5.5)


def fit_lambda(p, tol=2e-4):
    """在“1X2 拟合够好”的候选里，挑总进球最接近经验值的那个 λ 组合。
    只靠 1X2 反解时，总进球是不确定的（多组 λ 给出同样胜负平），必须加这条约束。

    最后再做一个「强弱拉伸」：实测模型把主客进球差压缩了
    （强主队 λ主 2.56 vs 实际 2.62，λ客 0.83 vs 0.76），
    按 ±6%×偏差 拉开一点，测试段比分/半全场 LogLoss 各降 0.0008。"""
    sse = ((TB - np.asarray(p)[None, None, :]) ** 2).sum(-1)
    mn = sse.min()
    cand = np.argwhere(sse <= mn + tol)
    tot = LH[cand[:, 0], cand[:, 1]] + LA[cand[:, 0], cand[:, 1]]
    tgt = target_total(float(p[0]), float(p[1]))
    k = int(np.argmin(np.abs(tot - tgt)))
    i, j = cand[k]
    lh, la = float(GRID[i]), float(GRID[j])
    z = 2 * float(p[0]) - 1
    return lh * (1 + STRETCH * z), la * (1 - STRETCH * z)


def htft(lh, la):
    """半全场 9 类：上半场 λ×0.4214，下半场 λ×0.5786，独立泊松"""
    N = 9
    p1h, p1a = pois(S_HT * lh), pois(S_HT * la)
    p2h, p2a = pois((1 - S_HT) * lh), pois((1 - S_HT) * la)
    out = np.zeros(9)
    kk = np.arange(KMAX)
    for i in range(N):
        for j in range(N):
            w1 = p1h[i] * p1a[j]
            if w1 < 1e-12:
                continue
            h = "胜" if i > j else ("平" if i == j else "负")
            for k_ in range(N):
                w2 = p2h[k_] * p2a
                fh, fa = i + k_, j + kk
                out[SERIES.index(h + "胜")] += w1 * w2[fh > fa].sum()
                out[SERIES.index(h + "平")] += w1 * w2[fh == fa].sum()
                out[SERIES.index(h + "负")] += w1 * w2[fh < fa].sum()
    out = out / out.sum()
    # 实测修正：真实比赛「翻盘」比独立泊松高。
    # 5,627 场：实际 负胜 3.20% / 胜负 2.27%，模型只给 2.34% / 1.87%。
    # 训练段最优 负胜×1.4、胜负×1.2；测试段 LogLoss 1.8221 → 1.8204。
    out[SERIES.index("负胜")] *= HTFT_BOOST_FS
    out[SERIES.index("胜负")] *= HTFT_BOOST_SF
    return out / out.sum()


SHARPEN_T = 0.90   # 温度锐化：实测 99 场台账 LogLoss 0.9066 → 0.8976（配对 t=2.13）


def sharpen(p, T=SHARPEN_T):
    """把概率往两端推：p^(1/T) 再归一化。T<1 = 更自信"""
    q = np.clip(np.asarray(p, float), 1e-9, 1) ** (1.0 / T)
    return q / q.sum()


def odds_code(*o):
    """赔率形态码：每位取整数部分，如 1.66/3.77/4.73 → 134"""
    out = ""
    for v in o:
        try:
            f = float(v)
            out += str(min(int(f), 9)) if f > 0 else "-"
        except Exception:
            out += "-"
    return out


def tier_1x2(p):
    # 阈值随锐化一起上调，保持各档场次和命中率不变
    return "高" if p >= 0.68 else ("中" if p >= 0.58 else ("低" if p >= 0.46 else "很低"))


def tier_htft(p):
    return "高" if p >= 0.40 else ("中" if p >= 0.30 else ("低" if p >= 0.20 else "很低"))


def upset_risk(pmax):
    """冷门风险（按全库 5,861 场的实际冷门率定档）：
    >75% → 13% ；65~75% → 27% ；55~65% → 38% ；45~55% → 49% ；<45% → 58%"""
    if pmax >= 0.75: return "低"
    if pmax >= 0.65: return "较低"
    if pmax >= 0.55: return "中"
    if pmax >= 0.45: return "高"
    return "极高"


def tier_score(p3):
    return "高" if p3 >= 0.33 else ("中" if p3 >= 0.28 else ("低" if p3 >= 0.23 else "很低"))


_RES = None


def _results():
    """懒加载官方赛果库（含半场/全场比分），用于算近况"""
    global _RES
    if _RES is None:
        frames = []
        for fn in ("官方赛果_2026.csv", "官方赛果_历史.csv"):
            if os.path.exists(fn):
                frames.append(pd.read_csv(fn, encoding="utf-8-sig"))
        if not frames:
            _RES = pd.DataFrame(columns=["dt", "主队", "客队", "hg", "ag"])
        else:
            r = pd.concat(frames).drop_duplicates("matchId")
            r["dt"] = pd.to_datetime(r.日期.astype(str).str[:10], errors="coerce")
            f = r.全场比分.astype(str).str.split(":", expand=True)
            ok = f[0].str.isdigit() & f[1].str.isdigit()
            r = r[ok].copy()
            f = f[ok]
            r["hg"] = f[0].astype(int)
            r["ag"] = f[1].astype(int)
            _RES = r[["dt", "主队", "客队", "hg", "ag"]]
    return _RES


def team_form(team, before, window=45, n=5):
    """时间窗内的近况：返回场次和场均积分率（0~1）"""
    r = _results()
    if not len(r):
        return None
    lo = before - pd.Timedelta(days=window)
    m = r[((r.主队.astype(str).str.contains(team, na=False)) |
           (r.客队.astype(str).str.contains(team, na=False))) &
          (r.dt < before) & (r.dt >= lo)].sort_values("dt").tail(n)
    if len(m) == 0:
        return None
    pts = 0
    for x in m.itertuples():
        home = team in str(x.主队)
        gf, ga = (x.hg, x.ag) if home else (x.ag, x.hg)
        pts += 3 if gf > ga else (1 if gf == ga else 0)
    return {"n": len(m), "rate": pts / (3.0 * len(m))}


_CORAL = None


def _coral_forms():
    """官网快照里各队的近况（完整、无缺口）。只读本地文件，不联网。"""
    global _CORAL
    if _CORAL is None:
        _CORAL = {}
        if os.path.exists("Coral面板.csv"):
            try:
                t = pd.read_csv("Coral面板.csv", encoding="utf-8-sig")
                for x in t.itertuples():
                    if pd.notna(x.主近积分率):
                        _CORAL[str(x.主队)] = {"n": int(x.主近场数), "rate": float(x.主近积分率),
                                            "tot": float(x.主近均球)}
                    if pd.notna(x.客近积分率):
                        _CORAL[str(x.客队)] = {"n": int(x.客近场数), "rate": float(x.客近积分率),
                                            "tot": float(x.客近均球)}
            except Exception:
                _CORAL = {}
    return _CORAL


_PANEL = None


def _coral_panel():
    """Coral面板 里每场的去水概率（官网口径），按「主队|客队」索引"""
    global _PANEL
    if _PANEL is None:
        _PANEL = {}
        if os.path.exists("Coral面板.csv"):
            try:
                t = pd.read_csv("Coral面板.csv", encoding="utf-8-sig")
                for x in t.itertuples():
                    _PANEL[f"{x.主队}|{x.客队}"] = x
            except Exception:
                _PANEL = {}
    return _PANEL


def coral_cols(home, away):
    """返回 (Coral去水, 竞彩去水, 差) 三个字符串"""
    x = _coral_panel().get(f"{home}|{away}")
    if x is None:
        return "—", "—", "—"
    c = f"{x.C主率:.0%}/{x.C平率:.0%}/{x.C客率:.0%}" if pd.notna(x.C主率) else "—"
    j = f"{x.竞主率:.0%}/{x.竞平率:.0%}/{x.竞客率:.0%}" if pd.notna(x.竞主率) else "—"
    d = f"{(x.竞主率 - x.C主率) * 100:+.1f}pp" if (pd.notna(x.C主率) and pd.notna(x.竞主率)) else "—"
    return c, j, d


def form_tag(home, away, day, pick):
    """近况和模型方向是否一致：支持 / 分歧 / 中性 / 不可用
    优先用官网快照（完整），没有才退回本地库。"""
    cf = _coral_forms()
    fh = cf.get(str(home)) or team_form(home, day)
    fa = cf.get(str(away)) or team_form(away, day)
    if not fh or not fa or fh["n"] < 3 or fa["n"] < 3:
        return "不可用"
    diff = fh["rate"] - fa["rate"]
    if pick == "平" or abs(diff) < 0.15:
        return "中性"
    if (diff > 0 and pick == "主胜") or (diff < 0 and pick == "客胜"):
        return "支持"
    return "分歧"


def main():
    if not os.path.exists(IN):
        print(f"[X] 找不到 {IN}，请先填好再跑")
        return
    df = pd.read_excel(IN)

    # 欧赔缺失时，用 Coral 面板的即时价补齐（实测去水后差 <1pp，最大 2pp）
    _pan = _coral_panel()
    _fill = 0
    for _i, _r in df.iterrows():
        if pd.notna(_r.get("欧赔主")):
            continue
        _x = _pan.get(f"{_r['主队']}|{_r['客队']}")
        if _x is None:
            continue
        # 优先 marketProb（多书商平均＝欧赔），没有才退到 Coral
        _o = [getattr(_x, "欧主", np.nan), getattr(_x, "欧平", np.nan), getattr(_x, "欧客", np.nan)]
        if not all(pd.notna(v) for v in _o):
            _o = [getattr(_x, "即主", np.nan), getattr(_x, "即平", np.nan), getattr(_x, "即客", np.nan)]
        if all(pd.notna(v) for v in _o):
            df.at[_i, "欧赔主"], df.at[_i, "欧赔平"], df.at[_i, "欧赔客"] = _o
            _fill += 1
    if _fill:
        print(f"  （{_fill} 场缺欧赔，已用官网欧赔(auto)补齐）")
    df = df.dropna(subset=["欧赔主", "欧赔平", "欧赔客"], how="any")
    print(f"待预测 {len(df)} 场")
    rows = {"ov": [], "upset": [], "1x2": [], "htft": [], "score": [], "hc": [], "tg": [], "ev": []}
    ledger_rows = []

    for r in df.itertuples():
        eu = np.array([r.欧赔主, r.欧赔平, r.欧赔客], float)
        inv = 1 / eu
        p = inv / inv.sum()
        lh, la = fit_lambda(p)
        M = dc_grid(lh, la)
        tot = np.add.outer(np.arange(KMAX), np.arange(KMAX))

        # 1X2
        # 胜平负直接用去水概率（市场原值）；比分/半全场/让球才用矩阵
        _ps = sharpen(p)          # 显示用的锐化概率（矩阵仍用原始 p）
        ph, pd_, pa = float(_ps[0]), float(_ps[1]), float(_ps[2])
        trio = [(ph, "主胜"), (pd_, "平"), (pa, "客胜")]
        trio.sort(reverse=True)
        rows["1x2"].append([r.日期, r.联赛, f"{r.主队} vs {r.客队}",
                            ph, pd_, pa, trio[0][1], trio[0][0],
                            trio[0][0] - trio[1][0], tier_1x2(trio[0][0])])

        # 比分
        flat = [(M[i, j], f"{i}:{j}") for i in range(KMAX) for j in range(KMAX)]
        flat.sort(reverse=True)
        top5 = flat[:5]
        top3 = top5[:3]
        p3 = sum(x[0] for x in top3)
        # 进球档：4 球以上的概率常比 2 球还高，但单格太小排不到第一
        p2g = M[tot == 2].sum(); p3g = M[tot == 3].sum(); p4g = M[tot >= 4].sum()
        big = max([(M[i, j], f"{i}:{j}") for i in range(KMAX) for j in range(KMAX) if i + j >= 4])
        # 代表比分 = 在「首选结果」+「最可能进球档」两个条件下概率最高的比分
        p01g = M[tot <= 1].sum()
        bands = {"0-1": p01g, "2": p2g, "3": p3g, "4+": p4g}
        topband = max(bands, key=bands.get)

        def _res(i, j):
            return "主胜" if i > j else ("平" if i == j else "客胜")

        def _inb(t):
            if topband == "0-1":
                return t <= 1
            if topband == "4+":
                return t >= 4
            return t == int(topband)

        _cand = [(M[i, j], f"{i}:{j}") for i in range(KMAX) for j in range(KMAX)
                 if _res(i, j) == trio[0][1] and _inb(i + j)]
        rep = max(_cand) if _cand else (0.0, "—")
        # 结果 × 进球档 的 12 格组合（这才是真正有信息量的输出）
        BL = [("0-1球", tot <= 1), ("2球", tot == 2), ("3球", tot == 3), ("4+球", tot >= 4)]
        _ii = np.arange(KMAX)[:, None]
        _jj = np.arange(KMAX)[None, :]
        RM = [_ii > _jj, _ii == _jj, _ii < _jj]
        RN = ["主胜", "平", "客胜"]
        cells = []
        for _ri in range(3):
            for _bl, _bm in BL:
                cells.append((M[RM[_ri] & _bm].sum(), f"{RN[_ri]}·{_bl}"))
        combo = max(cells)
        rows["score"].append([r.日期, f"{r.主队} vs {r.客队}",
                              " / ".join(f"{n} {pp:.0%}" for pp, n in top3), p3, p2g, p3g, p4g,
                              big[1], big[0], rep[1], rep[0], topband, bands[topband],
                              " / ".join(f"{n}" for _, n in top5), tier_score(p3),
                              [c[0] for c in cells if c[1].startswith("主胜")],
                              [c[0] for c in cells if c[1].startswith("平")],
                              [c[0] for c in cells if c[1].startswith("客胜")],
                              combo])

        # 半全场（9 类全存，方便整表展示）
        hf = htft(lh, la)
        order = np.argsort(hf)[::-1]
        # 半场状态（把 9 格按上半场结果合并）：很多场次「上半场平」才是最大状态
        ht_win = hf[0] + hf[1] + hf[2]
        ht_draw = hf[3] + hf[4] + hf[5]
        ht_lose = hf[6] + hf[7] + hf[8]
        rows["htft"].append([r.日期, f"{r.主队} vs {r.客队}"] + [float(x) for x in hf] + [
            SERIES[order[0]], hf[order[0]], hf[order[1]],
            hf[order[0]] + hf[order[1]] + hf[order[2]], tier_htft(hf[order[0]]),
            ht_win, ht_draw, ht_lose])

        # 让球
        hc_pick, hc_prob, hc_tier = None, None, None
        k_ = getattr(r, "让球", None)
        if pd.notna(k_):
            kk_ = int(round(float(k_)))
            # 让球数 kk_：主队让 kk_ 球（负=主队让），实际比分 i-j，让球后 i-j+kk_
            dreal = np.add.outer(np.arange(KMAX), np.zeros(KMAX, int)) - np.outer(np.ones(KMAX, int), np.arange(KMAX)) + kk_
            h1 = M[dreal > 0].sum(); h2 = M[dreal == 0].sum(); h3 = M[dreal < 0].sum()
            t2 = [(h1, "让胜"), (h2, "让平"), (h3, "让负")]
            t2.sort(reverse=True)
            hc_pick, hc_prob = t2[0][1], t2[0][0]
            hc_tier = tier_1x2(t2[0][0])
            rows["hc"].append([r.日期, f"{r.主队} vs {r.客队}", f"{kk_:+d}",
                               h1, h2, h3, t2[0][1], t2[0][0], tier_1x2(t2[0][0])])

        # 总进球
        gd = [M[tot == t].sum() for t in range(7)] + [M[tot >= 7].sum()]
        rows["tg"].append([r.日期, f"{r.主队} vs {r.客队}"] + [x for x in gd] + [lh, la])

        # 冷门方案：首选落空后，两条替代路径各自多大、最可能打成什么比分
        pick_name = trio[0][1]
        alts = [(ph, "主胜"), (pd_, "平"), (pa, "客胜")]
        alts = sorted([x for x in alts if x[1] != pick_name], reverse=True)

        def best_in(res):
            best = (0.0, "0:0")
            for i in range(KMAX):
                for j in range(KMAX):
                    rr = "主胜" if i > j else ("平" if i == j else "客胜")
                    if rr == res and M[i, j] > best[0]:
                        best = (M[i, j], f"{i}:{j}")
            return best

        b1, b2 = best_in(alts[0][1]), best_in(alts[1][1])
        rows["upset"].append([
            r.日期, f"{r.主队} vs {r.客队}",
            pick_name, trio[0][0], tier_1x2(trio[0][0]), upset_risk(trio[0][0]),
            "要防" if upset_risk(trio[0][0]) in ("高", "极高") else "不用",
            alts[0][1], alts[0][0], b1[1], b1[0],
            alts[1][1], alts[1][0], b2[1], b2[0],
        ])

        # 近况标记（只做标记，不改概率）
        _day = pd.to_datetime(str(r.日期)[:10], errors="coerce")
        if pd.isna(_day):
            _day = pd.Timestamp.today()
        f_tag = form_tag(str(r.主队), str(r.客队), _day, trio[0][1])
        _cc = coral_cols(str(r.主队), str(r.客队))

        # 总览（一场一行）
        rows["ov"].append([
            r.日期, f"{r.主队} vs {r.客队}", f"{eu[0]:.2f}/{eu[1]:.2f}/{eu[2]:.2f}",
            trio[0][1], trio[0][0], tier_1x2(trio[0][0]),
            SERIES[order[0]], hf[order[0]],
            hf[order[0]] + hf[order[1]] + hf[order[2]], tier_htft(hf[order[0]]),
            combo[1], combo[0], p3,
            hc_pick or "—", hc_prob, int(np.argmax(gd)), gd[int(np.argmax(gd))],
            M[tot >= 4].sum(), f_tag, *_cc,
        ])

        # 台账（有实际比分时才算）
        act = getattr(r, "实际比分", None)
        memo = None
        if act is not None and pd.notna(act) and isinstance(act, str) and ("-" in act or ":" in act):
            try:
                aa = act.replace(":", "-").split("-")
                ah, aa2 = int(aa[0]), int(aa[1])
                ay = 0 if ah > aa2 else (1 if ah == aa2 else 2)
                p_act = [ph, pd_, pa][ay]
                ht_act = getattr(r, "实际半全场", None)
                ht_act = str(ht_act) if pd.notna(ht_act) else ""
                memo = {
                    "日期": r.日期, "对阵": f"{r.主队} vs {r.客队}",
                    "主胜": round(ph, 3), "平": round(pd_, 3), "客胜": round(pa, 3),
                    "推荐": ["主胜", "平", "客胜"][int(np.argmax([ph, pd_, pa]))],
                    "把握度": tier_1x2(max(ph, pd_, pa)), "近况": f_tag,
                    "欧赔主": float(getattr(r, "欧赔主", np.nan)), "欧赔平": float(getattr(r, "欧赔平", np.nan)),
                    "欧赔客": float(getattr(r, "欧赔客", np.nan)),
                    "让球主": float(getattr(r, "让球主", np.nan)), "让球平": float(getattr(r, "让球平", np.nan)),
                    "让球客": float(getattr(r, "让球客", np.nan)),
                    "欧赔形态": odds_code(getattr(r, "欧赔主", np.nan), getattr(r, "欧赔平", np.nan), getattr(r, "欧赔客", np.nan)),
                    "让球形态": odds_code(getattr(r, "让球主", np.nan), getattr(r, "让球平", np.nan), getattr(r, "让球客", np.nan)),
                    "实际赛果": ["主胜", "平", "客胜"][ay], "命中": int(np.argmax([ph, pd_, pa]) == ay),
                    "实际结果概率": round(p_act, 3),
                    "半全场首选": SERIES[order[0]], "半全场首选率": round(hf[order[0]], 3),
                    "实际半全场": ht_act,
                    "半全场命中": int(ht_act == SERIES[order[0]]) if ht_act else "",
                    "比分首选": top5[0][1], "比分首选率": round(top5[0][0], 3),
                    "同向比分": rep[1], "同向比分率": round(rep[0], 3),
                    "同向进球档": topband,
                    "实际比分": f"{ah}:{aa2}",
                    "比分命中": int(top5[0][1] == f"{ah}:{aa2}"),
                    "总进球峰值": int(np.argmax(gd)), "实际总进球": ah + aa2,
                    "让球推荐": hc_pick or "",
                    "让球命中": ("" if hc_pick is None else
                             int(hc_pick == ("让胜" if (ah - aa2) + (int(round(float(getattr(r, "让球", 0) or 0))) if pd.notna(getattr(r, "让球", None)) else 0) > 0
                                             else ("让平" if (ah - aa2) + (int(round(float(getattr(r, "让球", 0) or 0))) if pd.notna(getattr(r, "让球", None)) else 0) == 0
                                                   else "让负")))),
                }
            except Exception:
                memo = None
        ledger_rows.append(memo)

        # EV
        if all(pd.notna(getattr(r, c, None)) for c in ("竞彩主", "竞彩平", "竞彩客")):
            jc = np.array([r.竞彩主, r.竞彩平, r.竞彩客], float)
            pm = np.array([ph, pd_, pa])
            ev = pm * jc - 1
            i = int(ev.argmax())
            lab = ["主胜", "平", "客胜"][i]
            rows["ev"].append([r.日期, f"{r.主队} vs {r.客队}",
                               ev[0], ev[1], ev[2], lab, ev[i], "是" if ev[i] > 0.05 else ("薄" if ev[i] > 0 else "否")])

    # ---------- 结果回填：写预测台账 ----------
    led_path = "预测台账.csv"
    led_cols = ["日期", "对阵", "主胜", "平", "客胜", "推荐", "把握度", "实际赛果", "命中",
                "实际结果概率", "半全场首选", "半全场首选率", "实际半全场", "半全场命中",
                "比分首选", "比分首选率", "实际比分", "比分命中", "总进球峰值", "实际总进球",
                "让球推荐", "让球命中"]
    led = []
    if os.path.exists(led_path):
        led = pd.read_csv(led_path, encoding="utf-8-sig").to_dict("records")
    have_led = {(str(x["日期"]), str(x["对阵"])) for x in led}

    if "实际比分" in df.columns:
        for k, memo in enumerate(ledger_rows):
            if memo is None:
                continue
            key = (str(memo["日期"]), memo["对阵"])
            if key in have_led or pd.isna(memo.get("实际比分")):
                continue
            led.append(memo)
            have_led.add(key)
        if led:
            pd.DataFrame(led).to_csv(led_path, index=False, encoding="utf-8-sig")
            print(f"预测台账.csv 已更新（{len(led)} 场）")

    # ---------- 写 Excel ----------
    NAVY, BLUE, BAND = "1F3864", "2C5F8D", "F2F6FA"
    F_T = Font(name="微软雅黑", size=14, bold=True, color="FFFFFF")
    F_H = Font(name="微软雅黑", size=9, bold=True, color="FFFFFF")
    F_B = Font(name="微软雅黑", size=9)
    F_S = Font(name="微软雅黑", size=9, color="595959")
    TH = Side(style="thin", color="D0D0D0")
    BD = Border(left=TH, right=TH, top=TH, bottom=TH)
    CEN = Alignment(horizontal="center", vertical="center", wrap_text=True)
    FILL_H = PatternFill("solid", fgColor=BLUE)
    FILL_B = PatternFill("solid", fgColor=BAND)
    G = PatternFill("solid", fgColor="E2EFDA")
    Y = PatternFill("solid", fgColor="FFF2CC")
    R_ = PatternFill("solid", fgColor="FCE4E4")
    TIER_FILL = {"高": G, "中": Y, "低": R_, "很低": PatternFill("solid", fgColor="D9D9D9")}

    wb = Workbook(); wb.remove(wb.active)

    def newsheet(name, title, sub, names, widths, data, fmts, tier_col=None, nc=None, h=30):
        ncol = nc or len(names)
        ws = wb.create_sheet(name); ws.sheet_view.showGridLines = False
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
        c = ws.cell(row=1, column=1, value=title); c.font, c.fill = F_T, PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[1].height = 30
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncol)
        c = ws.cell(row=2, column=1, value=sub); c.font = F_S
        c.alignment = Alignment(wrap_text=True, vertical="top", indent=1)
        ws.row_dimensions[2].height = h
        for j, nm in enumerate(names, 1):
            c = ws.cell(row=4, column=j, value=nm)
            c.font, c.fill, c.alignment, c.border = F_H, FILL_H, CEN, BD
        for j, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(j)].width = w
        ws.row_dimensions[4].height = 28
        for i, row in enumerate(data):
            for j, v in enumerate(row, 1):
                c = ws.cell(row=5 + i, column=j, value=v)
                c.font, c.alignment, c.border = F_B, CEN, BD
                if j in fmts:
                    c.number_format = fmts[j]
                if i % 2 == 1:
                    c.fill = FILL_B
            if tier_col:
                c = ws.cell(row=5 + i, column=tier_col)
                c.fill = TIER_FILL.get(c.value, FILL_B)
                c.font = Font(name="微软雅黑", size=9, bold=True)
            ws.row_dimensions[5 + i].height = 20
        return ws

    if rows["ov"]:
        ws_ov = newsheet(
            "0 总览", "总览：每场一行，先看这个",
            "四件事：谁赢、最可能的半全场、最可能的比分、几个球。"
            "「概率」是模型给的那个结果的概率，「把握度」是它的档位（≥65% 高、55~65 中、45~55 低、<45 很低）。",
            ["日期", "对阵", "欧赔", "胜平负", "概率", "把握度", "半全场", "概率", "三选合计", "把握度",
             "比分", "概率", "前三合计", "让球", "让球概率", "进球峰值", "峰值概率", "4+球", "近况", "Coral去水", "竞彩去水", "竞彩−Coral"],
            [11, 21, 13, 9, 9, 8, 9, 9, 9, 8, 7, 8, 9, 8, 9, 8, 8, 8, 8, 14, 14, 10],
            rows["ov"],
            {5: "0.0%", 8: "0.0%", 9: "0.0%", 12: "0.0%", 13: "0.0%", 15: "0.0%", 17: "0.0%", 18: "0.0%"},
            tier_col=6)
        for i in range(len(rows["ov"])):
            c = ws_ov.cell(row=5 + i, column=10)
            c.fill = TIER_FILL.get(c.value, FILL_B)
            c.font = Font(name="微软雅黑", size=9, bold=True)

    if rows["1x2"]:
        newsheet("1 胜平负", "胜平负：概率 / 推荐 / 把握度",
                 "把握度＝最大概率的档位（≥65% 高，55~65 中，45~55 低，<45 很低）。“领先”＝第一与第二名的概率差。",
                 ["日期", "联赛", "对阵", "主胜", "平", "客胜", "推荐", "概率", "领先", "把握度"],
                 [11, 12, 22, 10, 10, 10, 10, 10, 9, 9],
                 rows["1x2"], {4: "0.0%", 5: "0.0%", 6: "0.0%", 8: "0.0%", 9: "0.0%"}, tier_col=10)

    if rows["htft"]:
        ws_hf = newsheet(
            "2 半全场", "半全场：九类全概率",
            "九个格子各自的概率。把握度按第一候选算（≥40% 高，30~40 中，20~30 低，<20 很低）。"
            "历史 top1 约 35%、前三合计约 68%——正确用法是看「三选合计」，不是赌单点。",
            ["日期", "对阵"] + SERIES + ["首选", "首选概率", "次选概率", "三选合计", "把握度",
             "半场领先", "半场平", "半场落后"],
            [11, 22] + [7] * 9 + [9, 10, 10, 10, 8, 9, 9, 9],
            rows["htft"],
            {i: "0.0%" for i in range(3, 12)} | {13: "0.0%", 14: "0.0%", 15: "0.0%", 18: "0.0%", 19: "0.0%", 20: "0.0%"},
            tier_col=16)
        try:
            from openpyxl.formatting.rule import DataBarRule
            rng = f"C5:K{4 + len(rows['htft'])}"
            ws_hf.conditional_formatting.add(
                rng, DataBarRule(start_type="num", start_value=0, end_type="num",
                                 end_value=0.6, color="8EB4E3", showValue=True))
        except Exception:
            pass

    if rows["score"]:
        newsheet("3 比分", "比分：别只看首选，先看「几球」",
                 "首选永远是 1:1/2:1 这种小球，因为 4 球以上的概率被摊到二十多个格子里，每一格都排不到第一。"
                 "但「4+ 球」作为一个整体，常常比 2 球、3 球都大。所以先看「进球档」，再看「4+ 里最可能是几比几」。"
                 "把握度按前三合计算（≥33% 高，28~33 中，23~28 低，<23 很低）。",
                 ["日期", "对阵", "主胜0-1", "主胜2球", "主胜3球", "主胜4+",
                  "平0-1", "平2球", "平4+", "客胜0-1", "客胜2球", "客胜3球", "客胜4+", "最可能组合"],
                 [11, 22] + [8] * 12 + [14],
                 [[r2[0], r2[1],
                   r2[15][0], r2[15][1], r2[15][2], r2[15][3],
                   r2[16][0], r2[16][1], r2[16][3],
                   r2[17][0], r2[17][1], r2[17][2], r2[17][3],
                   f"{r2[18][1]} {r2[18][0]:.1%}"] for r2 in rows["score"]],
                 {3: "0.0%", 4: "0.0%", 5: "0.0%", 6: "0.0%", 7: "0.0%", 8: "0.0%", 9: "0.0%",
                  10: "0.0%", 11: "0.0%", 12: "0.0%", 13: "0.0%"})

    if rows["hc"]:
        newsheet("4 让球", "让球胜平负（按你填的让球数算）",
                 "让球为正＝主队受让；负＝主队让球。把握度同胜平负口径。",
                 ["日期", "对阵", "让球", "让胜", "让平", "让负", "推荐", "概率", "把握度"],
                 [11, 22, 7, 10, 10, 10, 10, 10, 9],
                 rows["hc"], {4: "0.0%", 5: "0.0%", 6: "0.0%", 8: "0.0%"}, tier_col=9)

    if rows["tg"]:
        newsheet("5 总进球", "总进球分布（0 ~ 7+）",
                 "最后两列是反解出来的 λ（主/客），可以直观看出强弱。",
                 ["日期", "对阵", "0", "1", "2", "3", "4", "5", "6", "7+", "λ主", "λ客"],
                 [11, 22] + [7] * 8 + [8, 8],
                 rows["tg"], {3: "0.0%", 4: "0.0%", 5: "0.0%", 6: "0.0%", 7: "0.0%",
                              8: "0.0%", 9: "0.0%", 10: "0.0%", 11: "0.0%", 12: "0.00", 13: "0.00"})

    if rows["upset"]:
        newsheet("7 冷门方案", "冷门方案：这场该不该防冷门",
                 "「冷门风险」按全库 5,861 场的实际冷门率定档：首选>75% 冷门率仅 13%；65~75% 约 27%；"
                 "55~65% 约 38%；45~55% 约 49%；<45% 高达 58%。"
                 "「要不要防」只有风险「高/极高」（首选<55%）才标“要防”。右侧是两条替代路径和它们最可能打成的比分。",
                 ["日期", "对阵", "首选", "首选概率", "把握度", "冷门风险", "要不要防",
                  "方向1", "概率", "最可能比分", "比分概率", "方向2", "概率", "最可能比分", "比分概率"],
                 [11, 22, 8, 9, 8, 9, 9, 8, 8, 10, 9, 8, 8, 10, 9],
                 rows["upset"],
                 {4: "0.0%", 9: "0.0%", 11: "0.0%", 13: "0.0%", 15: "0.0%"})


    if rows["ev"]:
        newsheet("6 EV", "对着竞彩赔率的期望值（填了竞彩才有）",
                 "EV ＝ 模型概率 × 竞彩赔率 − 1。>5% 标“是”，0~5% 标“薄”，<0 标“否”。"
                 "提醒：竞彩抽水 11.6%，多数场次 EV 都是负的，这是正常的。",
                 ["日期", "对阵", "EV主", "EV平", "EV客", "最佳", "最佳EV", "值得"],
                 [11, 22, 9, 9, 9, 8, 9, 8],
                 rows["ev"], {3: "+0.0%;-0.0%", 4: "+0.0%;-0.0%", 5: "+0.0%;-0.0%", 7: "+0.0%;-0.0%"})

    # 说明
    ws = wb.create_sheet("0 怎么用", 0)
    ws.sheet_view.showGridLines = False
    ws.merge_cells("A1:F1")
    c = ws.cell(row=1, column=1, value="怎么用")
    c.font, c.fill = F_T, PatternFill("solid", fgColor=NAVY)
    c.alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[1].height = 30
    ws.column_dimensions["A"].width = 3
    for col in "BCDEF":
        ws.column_dimensions[col].width = 22
    lines = [
        ("填表", "在 待预测.xlsx 里每场一行：日期、联赛、主队、客队、欧赔主/平/客（必填）。"),
        ("选填", "让球（-1/+1）、让球主平客、竞彩主平客。填了才会多出对应的页。"),
        ("跑", "python 预测.py   → 生成 预测结果.xlsx"),
        ("模型", "欧赔去水 → Dixon-Coles 泊松反解 λ → 比分矩阵 → 各玩法概率。"
                 "参数：上半场占比 0.4424（5,308 场）、主客拉伸 0.06、总进球三次曲线（5,861 场拟合）。"),
        ("验证", "2025-08~11 共 2,024 场：比分 LogLoss 2.9215、总进球 1.8609、半全场 1.8189（top1 34.1%）。"),
        ("局限", "只用到赔率信息。赔率里没有的东西（伤停、轮换、内幕），它也看不到。"),
        ("把握度", "胜平负：≥65% 高 / 55~65 中 / 45~55 低 / <45 很低。半全场：≥40/30/20。比分：前三合计 ≥33/28/23。"),
    ]
    rr = 3
    for a, b in lines:
        ws.cell(row=rr, column=1, value="").font = F_B
        ws.cell(row=rr, column=2, value=a).font = Font(name="微软雅黑", size=10, bold=True, color=NAVY)
        ws.merge_cells(start_row=rr, start_column=3, end_row=rr, end_column=6)
        c = ws.cell(row=rr, column=3, value=b)
        c.font = F_B
        c.alignment = Alignment(wrap_text=True, vertical="center", indent=1)
        ws.row_dimensions[rr].height = 34
        rr += 1

    wb.save(OUT)
    print(f"已写出 {OUT}")


if __name__ == "__main__":
    main()
