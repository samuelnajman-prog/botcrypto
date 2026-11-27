"""Recherche intraday sur actions US : ORB « stocks in play » et variantes.

Conventions (prudentes) :
- décision possible seulement avec des bougies CLOSES ; ordres stop simulés bougie par bougie ;
- si une bougie franchit l'ordre d'entrée ET le stop, on suppose le stop touché (pire cas) ;
- ouverture au-delà d'un niveau (gap) => exécution au prix d'ouverture, pas au niveau ;
- sortie au plus tard sur la clôture de la dernière bougie de la séance ;
- frais par action + glissement en % à chaque passage d'ordre.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import data as D


@dataclass
class Costs:
    per_share: float = 0.005      # commission + frais bourse/compensation/régulateur, par action et par côté
    slip: float = 0.0003          # glissement par côté (3 pb) sur ordres stop / au marché
    min_order: float = 0.35       # commission minimale par ordre (IBKR Pro)


@dataclass
class Params:
    or_bars: int = 1              # nombre de bougies de la fourchette d'ouverture
    top_n: int = 20
    min_rvol: float = 1.0
    stop_atr: float = 0.10        # stop à x % de l'ATR14 journalier
    target_r: float = 0.0         # objectif en multiples de risque (0 = aucun, sortie clôture)
    sides: str = "both"           # both | long | short
    min_price: float = 5.0
    min_vol14: float = 1e6
    min_atr: float = 0.5
    entry_until: int = 999        # index de bougie max pour l'entrée (999 = toute la séance)
    risk: float = 0.01            # risque par trade (fraction du capital)
    lev_cap: float = 4.0          # levier brut maximal
    ssr: bool = True              # pas de vente à découvert si le titre perd >= 10 % (règle SEC 201)
    catalyst_only: bool = False   # ne garder que les titres avec catalyseur (8-K récent / gap)
    min_gap: float = 0.0          # |gap| minimal à l'ouverture
    trend_filter: str = ""        # "" | "spy" : longs seulement si SPY > ouverture, etc.
    rank: str = "rvol"            # rvol | rvol_gap
    trail_atr: float = 0.0        # stop suiveur (multiple d'ATR) une fois en gain, 0 = aucun
    same_bar: str = "worst"       # worst : stop supposé touché si la bougie d'entrée l'atteint ; best : ignoré


# ------------------------------------------------------------------ préparation

@dataclass
class Panel:
    interval: str
    days: list                    # dates de séance communes
    syms: list
    o: np.ndarray                 # [sym, day, bar]
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    nbar: np.ndarray              # [sym, day] nombre de bougies présentes
    daily: dict = field(default_factory=dict)   # name -> [sym, day]


def build_panel(intraday: dict, daily: dict, interval: str, bars_per_day: int,
                min_days: int = 20) -> Panel:
    sess = {}
    for s, df in intraday.items():
        r = D.regular_session(df)
        if len(r):
            sess[s] = r
    alld = sorted({d for r in sess.values() for d in r["day"].unique()})
    # séances présentes pour SPY (référence du calendrier)
    if "SPY" in sess:
        alld = sorted(sess["SPY"]["day"].unique())
    di = {d: i for i, d in enumerate(alld)}
    syms = [s for s, r in sess.items() if r["day"].nunique() >= min_days and s in daily]
    S, T, B = len(syms), len(alld), bars_per_day
    arr = {k: np.full((S, T, B), np.nan, np.float32) for k in "ohlcv"}
    nbar = np.zeros((S, T), np.int16)
    step = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}[interval]
    dl = {k: np.full((S, T), np.nan) for k in ("atr14", "vol14", "dvol20", "prev_close", "d_open", "d_close")}
    for i, s in enumerate(syms):
        r = sess[s]
        loc = r.index.tz_convert(D.NY)
        b = ((loc.hour * 60 + loc.minute - 570) // step).to_numpy()
        t = np.array([di.get(d, -1) for d in r["day"]])
        ok = (t >= 0) & (b >= 0) & (b < B)
        for k, col in zip("ohlcv", ("open", "high", "low", "close", "volume")):
            arr[k][i, t[ok], b[ok]] = r[col].to_numpy()[ok]
        np.add.at(nbar, (np.full(ok.sum(), i), t[ok]), 1)
        st = D.daily_stats(daily[s])
        st.index = st.index.tz_convert(D.NY).date if st.index.tz is not None else st.index.date
        st = st[~pd.Index(st.index).duplicated(keep="last")]
        st = st.reindex(alld)
        for k, col in (("atr14", "atr14"), ("vol14", "vol14"), ("dvol20", "dvol20"),
                       ("prev_close", "prev_close"), ("d_open", "open"), ("d_close", "close")):
            dl[k][i] = st[col].to_numpy()
    return Panel(interval, alld, syms, arr["o"], arr["h"], arr["l"], arr["c"], arr["v"], nbar, dl)


def first_range(P: Panel, n: int):
    """Fourchette d'ouverture sur n bougies : open, high, low, close, volume, et RVOL du volume
    de la fourchette vs moyenne des 14 séances précédentes (même fenêtre horaire)."""
    o = P.o[:, :, 0]
    h = np.nanmax(P.h[:, :, :n], axis=2)
    l = np.nanmin(P.l[:, :, :n], axis=2)
    c = P.c[:, :, n - 1]
    v = np.nansum(P.v[:, :, :n], axis=2)
    v = np.where(np.isnan(P.c[:, :, n - 1]), np.nan, v)
    vs = pd.DataFrame(v.T)
    base = vs.rolling(14, min_periods=10).mean().shift(1).to_numpy().T
    rvol = v / base
    return o, h, l, c, v, rvol


# ------------------------------------------------------------------ simulation d'un trade

def sim_trade(oo, hh, ll, cc, nb, start, side, level, stopdist, P: Params, slip):
    """Ordre stop d'entrée à `level` posé après la bougie start-1. Retourne
    (prix d'entrée, prix de sortie, bougie d'entrée, motif) ou None si pas déclenché."""
    last = nb - 1
    end_entry = min(last, P.entry_until)
    for k in range(start, end_entry + 1):
        if np.isnan(oo[k]):
            continue
        if side > 0 and hh[k] >= level:
            fill = max(level, oo[k]) * (1 + slip)
        elif side < 0 and ll[k] <= level:
            fill = min(level, oo[k]) * (1 - slip)
        else:
            continue
        stop = fill - side * stopdist
        tgt = fill + side * stopdist * P.target_r if P.target_r > 0 else None
        best = fill
        for j in range(k, last + 1):
            if np.isnan(oo[j]):
                continue
            if j > k:  # ouverture de la bougie : gap au-delà du stop / de l'objectif
                if side * (oo[j] - stop) <= 0:
                    return fill, oo[j] * (1 - side * slip), k, "stop"
                if tgt is not None and side * (oo[j] - tgt) >= 0:
                    return fill, oo[j] * (1 - side * slip), k, "target"
            lo, hi = (ll[j], hh[j]) if side > 0 else (-hh[j], -ll[j])
            sstop = side * stop
            if lo <= sstop and not (j == k and P.same_bar == "best"):   # stop d'abord (prudent)
                return fill, stop * (1 - side * slip), k, "stop"
            if tgt is not None and hi >= side * tgt and j > k:
                return fill, tgt, k, "target"      # objectif = ordre limite, sans glissement
            if P.trail_atr > 0:
                best = max(best, hh[j]) if side > 0 else min(best, ll[j])
                trail = best - side * P.trail_atr * stopdist / max(P.stop_atr, 1e-9)
                if side * (trail - stop) > 0 and side * (best - fill) > stopdist:
                    stop = trail
        return fill, cc[last] * (1 - side * slip), k, "close"
    return None


# ------------------------------------------------------------------ backtest ORB stocks in play

def orb_trades(P: Panel, prm: Params, cost: Costs, catalysts: set | None = None,
               spy_dir: np.ndarray | None = None) -> pd.DataFrame:
    n = prm.or_bars
    o, h, l, c, v, rvol = first_range(P, n)
    dl = P.daily
    gap = o / dl["prev_close"] - 1
    elig = ((o > prm.min_price) & (dl["vol14"] > prm.min_vol14) & (dl["atr14"] > prm.min_atr)
            & (rvol >= prm.min_rvol) & (P.nbar > n) & np.isfinite(rvol) & (np.abs(gap) >= prm.min_gap))
    direction = np.sign(c - o)
    if prm.sides == "long":
        elig &= direction > 0
    elif prm.sides == "short":
        elig &= direction < 0
    elig &= direction != 0
    score = rvol.copy()
    if prm.rank == "rvol_gap":
        score = rvol * (1 + 10 * np.abs(gap))
    rows = []
    for t in range(P.o.shape[1]):
        cand = np.where(elig[:, t])[0]
        if catalysts is not None and prm.catalyst_only:
            cand = np.array([i for i in cand if (P.syms[i], P.days[t]) in catalysts], int)
        if spy_dir is not None and prm.trend_filter == "spy" and np.isfinite(spy_dir[t]):
            cand = np.array([i for i in cand if direction[i, t] == spy_dir[t]], int)
        if len(cand) == 0:
            continue
        cand = cand[np.argsort(-score[cand, t])][: prm.top_n]
        for i in cand:
            side = int(direction[i, t])
            if side < 0 and prm.ssr:
                # SSR : déclenché si le titre a perdu 10 % la veille (on l'ignore) ou s'il
                # cote déjà 10 % sous la clôture de la veille au moment de l'entrée
                if l[i, t] <= 0.9 * dl["prev_close"][i, t]:
                    continue
            atr = dl["atr14"][i, t]
            stopdist = prm.stop_atr * atr
            level = h[i, t] if side > 0 else l[i, t]
            res = sim_trade(P.o[i, t], P.h[i, t], P.l[i, t], P.c[i, t], int(P.nbar[i, t]), n,
                            side, level, stopdist, prm, cost.slip)
            if res is None:
                continue
            fill, exitp, k, why = res
            rows.append((P.days[t], P.syms[i], side, fill, exitp, stopdist, k, why,
                         rvol[i, t], gap[i, t], atr, o[i, t]))
    return pd.DataFrame(rows, columns=["day", "sym", "side", "entry", "exit", "stopdist", "bar",
                                       "why", "rvol", "gap", "atr", "open"])


def portfolio(tr: pd.DataFrame, days: list, prm: Params, cost: Costs, capital: float = 25_000.0,
              compound: bool = True) -> pd.DataFrame:
    """Taille : risque `risk` du capital jusqu'au stop, plafonnée à lev_cap/top_n du capital par
    position. Frais : par action (minimum par ordre) + glissement déjà inclus dans les prix."""
    eq = capital
    out = []
    g = tr.groupby("day") if len(tr) else {}
    for d in days:
        pnl, gross, n = 0.0, 0.0, 0
        if len(tr) and d in g.groups:
            for r in g.get_group(d).itertuples():
                notional_risk = prm.risk * eq / r.stopdist * r.entry
                notional = min(notional_risk, prm.lev_cap / prm.top_n * eq)
                sh = np.floor(notional / r.entry)
                if sh <= 0:
                    continue
                fees = 2 * max(cost.min_order, cost.per_share * sh)
                pnl += r.side * (r.exit - r.entry) * sh - fees
                gross += sh * r.entry
                n += 1
        ret = pnl / eq
        out.append((d, ret, gross / eq, n, eq))
        if compound:
            eq *= 1 + ret
    return pd.DataFrame(out, columns=["day", "ret", "gross", "n", "equity"]).set_index("day")


def stats(daily: pd.DataFrame | pd.Series, per_year: int = 252) -> dict:
    r = daily["ret"] if isinstance(daily, pd.DataFrame) else daily
    r = r.astype(float)
    if len(r) == 0:
        return {}
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    mu, sd = r.mean(), r.std()
    sharpe = mu / sd * np.sqrt(per_year) if sd > 0 else np.nan
    cagr = eq.iloc[-1] ** (per_year / len(r)) - 1
    return {"jours": len(r), "total": eq.iloc[-1] - 1, "cagr": cagr, "sharpe": sharpe,
            "maxdd": dd, "vol": sd * np.sqrt(per_year), "jours_pos": (r > 0).mean()}


def trade_stats(tr: pd.DataFrame) -> dict:
    if len(tr) == 0:
        return {"trades": 0}
    R = tr.side * (tr.exit - tr.entry) / tr.stopdist
    ret = tr.side * (tr.exit / tr.entry - 1)
    return {"trades": len(tr), "R_moy": R.mean(), "gain%": (ret > 0).mean(),
            "ret_moy_pb": ret.mean() * 1e4, "long": (tr.side > 0).mean()}


# ------------------------------------------------------------------ momentum intraday indice

def noise_area(df5: pd.DataFrame, lookback: int = 14, check_every: int = 6, band_mult: float = 1.0,
               cost_bps: float = 1.0, vwap_trail: bool = True, target_vol: float = 0.02,
               lev_cap: float = 4.0, daily_close: pd.Series | None = None, step_min: int = 5):
    """Zarattini, Aziz & Barbon (2024) « Beat the Market » sur un ETF (SPY/QQQ).
    Frontières = open × (1 ± moyenne sur 14 j du |mouvement depuis l'ouverture| à cette heure),
    ancrées sur max(open, clôture de la veille) / min(...). Décisions toutes les `check_every`
    bougies (6 × 5 min = 30 min). Taille = cible de volatilité journalière / vol 14 j, plafonnée."""
    r = D.regular_session(df5)
    days = sorted(r["day"].unique())
    B = -(-390 // step_min)
    arr = {k: np.full((len(days), B), np.nan) for k in ("o", "h", "l", "c", "v")}
    di = {d: i for i, d in enumerate(days)}
    loc = r.index.tz_convert(D.NY)
    b = ((loc.hour * 60 + loc.minute - 570) // step_min).to_numpy()
    t = np.array([di[d] for d in r["day"]])
    for k, col in zip("ohlcv", ("open", "high", "low", "close", "volume")):
        arr[k][t, b] = r[col].to_numpy()
    o0 = arr["o"][:, 0]
    move = np.abs(arr["c"] / o0[:, None] - 1)
    sigma = pd.DataFrame(move).rolling(lookback, min_periods=lookback).mean().shift(1).to_numpy()
    prevc = np.r_[np.nan, np.array([arr["c"][i][np.isfinite(arr["c"][i])][-1] for i in range(len(days))])[:-1]]
    dret = pd.Series([arr["c"][i][np.isfinite(arr["c"][i])][-1] for i in range(len(days))]).pct_change()
    dvol = dret.rolling(14).std().shift(1).to_numpy()
    tp = (arr["h"] + arr["l"] + arr["c"]) / 3
    vwap = np.nancumsum(tp * arr["v"], axis=1) / np.nancumsum(arr["v"], axis=1)
    rets = []
    for i in range(len(days)):
        if not np.isfinite(sigma[i, -1]) or not np.isfinite(dvol[i]) or not np.isfinite(prevc[i]):
            continue
        up_a = max(o0[i], prevc[i])
        dn_a = min(o0[i], prevc[i])
        size = min(lev_cap, target_vol / max(dvol[i], 1e-4))
        pos, pnl, nb = 0, 0.0, int(np.isfinite(arr["c"][i]).sum())
        for k in range(check_every - 1, nb, check_every):
            px = arr["c"][i, k]
            ub = up_a * (1 + band_mult * sigma[i, k])
            lb = dn_a * (1 - band_mult * sigma[i, k])
            if pos == 0:
                new = 1 if px > ub else (-1 if px < lb else 0)
            elif pos > 0:
                stop = max(ub, vwap[i, k]) if vwap_trail else ub
                new = 0 if px < stop else 1
                if new == 0 and px < lb:
                    new = -1
            else:
                stop = min(lb, vwap[i, k]) if vwap_trail else lb
                new = 0 if px > stop else -1
                if new == 0 and px > ub:
                    new = 1
            if new != pos:
                pnl -= abs(new - pos) * size * cost_bps / 1e4
                pos = new
            if pos != 0 and k < nb - 1:
                k2 = min(k + check_every, nb - 1)
                pnl += pos * size * (arr["c"][i, k2] / px - 1)
        if pos != 0:
            pnl -= abs(pos) * size * cost_bps / 1e4
        rets.append((days[i], pnl, size))
    return pd.DataFrame(rets, columns=["day", "ret", "size"]).set_index("day")


# ------------------------------------------------------------------ gap fade (vente des gaps haussiers)

@dataclass
class GapParams:
    min_gap: float = 0.03
    max_gap: float = 0.60
    min_price: float = 5.0
    min_dvol: float = 50e6
    top_n: int = 10
    stop: float = 0.0             # stop en % au-dessus du prix d'entrée (0 = aucun)
    stop_atr: float = 0.0         # ou en multiple d'ATR14 (prioritaire si > 0)
    confirm_bars: int = 0         # 0 = vente à l'ouverture ; k = attendre k bougies et vendre si sous l'ouverture
    confirm_vwap: bool = False
    weight: float = 0.10          # poids par position (fraction du capital)
    side: int = -1                # -1 : vendre les gaps haussiers ; +1 : acheter (tests)
    gap_dir: int = 1              # 1 : gaps haussiers ; -1 : gaps baissiers
    max_gap_rank_pct: float = 1.0
    take_profit: float = 0.0      # objectif en % (0 = aucun)
    max_gap_atr: float = 0.0      # gap max en multiple de l'ATR14 en % (0 = pas de limite) : écarte les gaps « news »
    delay_bars: int = 0           # entrer à l'ouverture de la bougie k sans condition
    exit_bar: int = -1            # sortir à la clôture de la bougie k (-1 = clôture de séance)


def premarket_gap(P: Panel, pre: dict, cutoff_min: int = 565) -> np.ndarray:
    """Gap indiqué avant l'ouverture : dernier cours de pré-séance (bougie close au plus tard à
    `cutoff_min` minutes après minuit, heure de New York) / clôture de la veille - 1."""
    out = np.full(P.o.shape[:2], np.nan)
    di = {d: i for i, d in enumerate(P.days)}
    step = {"5m": 5, "1h": 60}[P.interval]
    for i, s in enumerate(P.syms):
        df = pre.get(s)
        if df is None or df.empty:
            continue
        loc = df.index.tz_convert(D.NY)
        end = loc.hour * 60 + loc.minute + step
        ok = (end <= max(cutoff_min, 570 if step == 60 else cutoff_min)) & (loc.hour >= 4)
        x = df[ok]
        if x.empty:
            continue
        last = x.groupby(loc[ok].date)["close"].last()
        for d, px in last.items():
            t = di.get(d)
            if t is not None:
                out[i, t] = px
    return out / P.daily["prev_close"] - 1


def gap_trades(P: Panel, g: GapParams, slip: float = 0.0003, pre_gap: np.ndarray | None = None) -> pd.DataFrame:
    dl = P.daily
    o0 = P.o[:, :, 0]
    gap = o0 / dl["prev_close"] - 1 if pre_gap is None else pre_gap
    elig = ((dl["prev_close"] > g.min_price) & (dl["dvol20"] > g.min_dvol) & (P.nbar >= 2)
            & (g.gap_dir * gap > g.min_gap) & (g.gap_dir * gap < g.max_gap))
    if g.max_gap_atr > 0:
        elig &= np.abs(gap) <= g.max_gap_atr * dl["atr14"] / dl["prev_close"]
    rows = []
    for t in range(P.o.shape[1]):
        cand = np.where(elig[:, t])[0]
        if not len(cand):
            continue
        cand = cand[np.argsort(-g.gap_dir * gap[cand, t])][: g.top_n]
        for i in cand:
            nb = int(P.nbar[i, t])
            oo, hh, ll, cc, vv = P.o[i, t], P.h[i, t], P.l[i, t], P.c[i, t], P.v[i, t]
            k0 = max(g.confirm_bars, g.delay_bars)
            last = nb - 1 if g.exit_bar < 0 else min(nb - 1, g.exit_bar)
            if k0 > last:
                continue
            if g.confirm_bars > 0:
                ref = cc[k0 - 1]
                if g.confirm_vwap:
                    tp = (hh[:k0] + ll[:k0] + cc[:k0]) / 3
                    vw = np.nansum(tp * vv[:k0]) / max(np.nansum(vv[:k0]), 1)
                    bad = (g.side < 0 and ref >= vw) or (g.side > 0 and ref <= vw)
                else:
                    bad = (g.side < 0 and ref >= oo[0]) or (g.side > 0 and ref <= oo[0])
                if bad:
                    continue
            entry = oo[k0] * (1 + g.side * slip)
            if g.stop_atr > 0:
                sd = g.stop_atr * dl["atr14"][i, t]
            else:
                sd = g.stop * entry if g.stop > 0 else np.inf
            stop = entry - g.side * sd
            tgt = entry * (1 + g.side * g.take_profit) if g.take_profit > 0 else None
            exitp, why = cc[last] * (1 - g.side * slip), "close"
            for j in range(k0, last + 1):
                if np.isnan(oo[j]):
                    continue
                adverse = ll[j] if g.side > 0 else hh[j]
                if np.isfinite(sd) and g.side * (adverse - stop) <= 0:
                    px = oo[j] if g.side * (oo[j] - stop) <= 0 and j > k0 else stop
                    exitp, why = px * (1 - g.side * slip), "stop"
                    break
                if tgt is not None:
                    fav = hh[j] if g.side > 0 else ll[j]
                    if g.side * (fav - tgt) >= 0:
                        exitp, why = tgt, "target"
                        break
            rows.append((P.days[t], P.syms[i], g.side, entry, exitp, why, gap[i, t], dl["atr14"][i, t] / dl["prev_close"][i, t]))
    return pd.DataFrame(rows, columns=["day", "sym", "side", "entry", "exit", "why", "gap", "atrp"])


def gap_portfolio(tr: pd.DataFrame, days: list, weight: float, cost_side: float = 0.0002) -> pd.Series:
    """Rendement journalier : somme des positions au poids fixe, frais par côté en plus du glissement."""
    if len(tr) == 0:
        return pd.Series(0.0, index=days)
    r = tr.side * (tr.exit / tr.entry - 1) - 2 * cost_side
    d = (r * weight).groupby(tr.day).sum()
    return d.reindex(days).fillna(0.0)


def gap_book(P: Panel, tr: pd.DataFrame, weight: float, daily_stop: float = 0.0,
             slip: float = 0.0003, cost_side: float = 0.0002) -> pd.Series:
    """Rendement journalier du portefeuille de shorts avec limite de perte journalière :
    si la perte latente cumulée atteint `daily_stop` à la clôture d'une bougie, tout est
    racheté à cette clôture (+ glissement). Les stops individuels restent ceux de `tr`."""
    si = {s: i for i, s in enumerate(P.syms)}
    ti = {d: t for t, d in enumerate(P.days)}
    out = {}
    for d, grp in tr.groupby("day"):
        t = ti[d]
        nb = int(P.nbar[:, t].max())
        path = np.zeros(nb)
        final = 0.0
        legs = []
        for r in grp.itertuples():
            i = si[r.sym]
            c = P.c[i, t, :nb].astype(float)
            c = pd.Series(c).ffill().to_numpy()
            # bougie de sortie du trade (stop) : première bougie dont le high/low franchit le prix de sortie
            ret_close = r.side * (c / r.entry - 1)
            done = r.side * (r.exit / r.entry - 1)
            if r.why == "stop":
                adverse = P.h[i, t, :nb] if r.side < 0 else P.l[i, t, :nb]
                hit = np.where(r.side * (adverse - r.exit / (1 - r.side * slip)) <= 1e-9)[0]
                kx = int(hit[0]) if len(hit) else nb - 1
                ret_close[kx:] = done
            legs.append(ret_close)
            final += done
        legs = np.array(legs)
        path = weight * legs.sum(axis=0)
        res = weight * final
        if daily_stop > 0:
            br = np.where(path <= -daily_stop)[0]
            if len(br) and br[0] < nb - 1:
                k = br[0]
                res = weight * (legs[:, k] - slip).sum()
        out[d] = res - weight * len(grp) * 2 * cost_side
    return pd.Series(out).reindex(P.days).fillna(0.0)
