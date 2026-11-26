"""Bot n°2 — recherche day trading crypto sur bougies 5 minutes (Binance, 2020 → 2026).

Toutes les positions sont ouvertes et fermées dans la même séance (pas de position de nuit).
Exécution simulée de façon prudente : ordres stop déclenchés au niveau (ou à l'ouverture de la
bougie si elle s'ouvre au-delà), slippage à chaque ordre, frais à chaque côté, et si une même
bougie touche l'entrée ET le stop, on suppose le pire (stop touché).

Stratégies testées (définies AVANT de regarder les résultats, petites grilles) :
  ORB   Opening Range Breakout (Zarattini, Barbon & Aziz 2024), dans le sens de la 1re bougie,
        filtre « actif en mouvement » (volume relatif), stop en fraction d'ATR, sortie en fin de séance.
  NOISE Zone de bruit (Zarattini, Aziz & Barbon 2024, « Beat the Market ») : achat/vente quand le prix
        sort de sa zone de bruit habituelle à cette heure-là, stop suiveur = max(bord, VWAP), fin de séance.
  VB    Cassure de volatilité (L. Williams) : achat si le prix dépasse ouverture + k × amplitude de la veille.
  ITM   Momentum intraday (Shen, Urquhart & Wang 2022) : la dernière heure suit le sens de la journée.

Séances : "ny" = 09:30 → 16:00 heure de New York (pic de volume crypto), "utc" = 00:00 → 24:00 UTC.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
NY = ZoneInfo("America/New_York")
BAR = 5  # minutes


@dataclass
class Costs:
    fee: float = 0.0010    # par côté (taker spot/perp standard)
    slip: float = 0.0003   # par ordre, en plus du prix de déclenchement


def load5m(sym: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / f"{sym}USDT_5m.csv.gz", index_col=0, parse_dates=True)
    return df[~df.index.duplicated()].sort_index()


class Asset:
    """Tableaux numpy + séances précalculées pour un actif."""

    def __init__(self, sym: str, df: pd.DataFrame | None = None):
        self.sym = sym
        df = load5m(sym) if df is None else df
        self.idx = df.index
        self.t = df.index.values.astype("datetime64[ns]").astype(np.int64)
        self.o, self.h, self.l, self.c, self.v = (df[k].values for k in ("open", "high", "low", "close", "quote_volume"))
        d = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(), (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
        # ATR et vol journalière connues AVANT la journée (décalées d'un jour)
        self.atr_pct = (tr.rolling(14).mean() / d.close).shift(1)
        self.dvol = d.close.pct_change().rolling(14).std().shift(1)
        self.daily = d
        self._sess = {}

    def sessions(self, kind: str):
        """Liste de (jour, i0, i1) : indices [i0, i1) des bougies 5 min de la séance complète."""
        if kind in self._sess:
            return self._sess[kind]
        out = []
        days = pd.date_range(self.idx[0].normalize(), self.idx[-1].normalize(), freq="D")
        for d in days:
            if kind == "ny":
                if d.weekday() >= 5:      # séance US : jours ouvrés seulement
                    continue
                s = pd.Timestamp(d.year, d.month, d.day, 9, 30, tz=NY).tz_convert("UTC").tz_localize(None)
                e = pd.Timestamp(d.year, d.month, d.day, 16, 0, tz=NY).tz_convert("UTC").tz_localize(None)
            else:
                s, e = d, d + pd.Timedelta(days=1)
            i0 = int(np.searchsorted(self.t, s.value))
            i1 = int(np.searchsorted(self.t, e.value))
            n_exp = int((e - s).total_seconds() // 60 // BAR)
            if i1 - i0 == n_exp and i0 < len(self.t) and self.t[i0] == s.value:   # séance complète, sans trou
                out.append((d, i0, i1))
        self._sess[kind] = out
        return out


# --------------------------------------------------------------------------- stratégies
# Chaque fonction renvoie une liste de trades (jour, sens, prix entrée, prix sortie, risque % pour le sizing).

def orb(a: Asset, kind="ny", or_min=15, stop="atr", k=0.25, mode="ls", rvol_min=0.0, cost=Costs()):
    n = or_min // BAR
    trades, hist = [], []
    for d, i0, i1 in a.sessions(kind):
        oh, ol = a.h[i0:i0 + n].max(), a.l[i0:i0 + n].min()
        oo, oc = a.o[i0], a.c[i0 + n - 1]
        vol = a.v[i0:i0 + n].sum()
        rv = vol / np.mean(hist[-14:]) if len(hist) >= 14 else np.nan
        hist.append(vol)
        atr = a.atr_pct.get(d, np.nan)
        if np.isnan(rv) or np.isnan(atr) or rv < rvol_min or oc == oo:
            continue
        side = 1 if oc > oo else -1
        if side < 0 and mode == "long":
            continue
        level = oh if side > 0 else ol
        for j in range(i0 + n, i1):
            hit = a.h[j] >= level if side > 0 else a.l[j] <= level
            if not hit:
                continue
            raw = max(a.o[j], level) if side > 0 else min(a.o[j], level)
            entry = raw * (1 + side * cost.slip)
            if stop == "atr":
                sp = entry * (1 - side * k * atr)
            else:  # bord opposé de la fourchette d'ouverture
                sp = ol if side > 0 else oh
            risk = abs(entry - sp) / entry
            exit_px = None
            for m in range(j, i1):
                if (side > 0 and a.l[m] <= sp) or (side < 0 and a.h[m] >= sp):
                    px = min(a.o[m], sp) if side > 0 else max(a.o[m], sp)
                    if m == j:
                        px = sp
                    exit_px = px * (1 - side * cost.slip)
                    break
            if exit_px is None:
                exit_px = a.c[i1 - 1] * (1 - side * cost.slip)
            trades.append((d, side, entry, exit_px, risk))
            break
    return trades


def noise(a: Asset, kind="utc", mult=1.0, lookback=14, check_min=30, mode="ls", cost=Costs()):
    """Zone de bruit : bords = ouverture × (1 ± mult × écart moyen habituel à cette heure)."""
    step = check_min // BAR
    sess = a.sessions(kind)
    if not sess:
        return []
    L = sess[0][2] - sess[0][1]
    moves = []  # |close_t / open - 1| par séance, à chaque point de contrôle
    trades = []
    for d, i0, i1 in sess:
        op = a.o[i0]
        pts = list(range(i0 + step - 1, i1 - 1, step))          # fin de chaque tranche de 30 min
        mv = np.abs(a.c[pts] / op - 1)
        if len(moves) >= lookback and len(pts) == len(moves[-1]):
            sig = np.mean(moves[-lookback:], axis=0)
            pv = a.c[i0:i1] * a.v[i0:i1]
            cum_v = np.cumsum(a.v[i0:i1])
            vwap = np.cumsum(a.c[i0:i1] * a.v[i0:i1]) / np.where(cum_v > 0, cum_v, np.nan)
            pos, entry = 0, 0.0
            for q, p in enumerate(pts):
                px = a.c[p]
                ub, lb = op * (1 + mult * sig[q]), op * (1 - mult * sig[q])
                vw = vwap[p - i0]
                if pos > 0 and px < max(ub, vw):
                    trades.append((d, 1, entry, px * (1 - cost.slip), None)); pos = 0
                    continue                                   # pas de ré-entrée au même point
                if pos < 0 and px > min(lb, vw):
                    trades.append((d, -1, entry, px * (1 + cost.slip), None)); pos = 0
                    continue
                if pos == 0:
                    if px > max(ub, vw):                        # entrée seulement au-delà du stop suiveur
                        pos, entry = 1, px * (1 + cost.slip)
                    elif px < min(lb, vw) and mode == "ls":
                        pos, entry = -1, px * (1 - cost.slip)
            if pos != 0:
                trades.append((d, pos, entry, a.c[i1 - 1] * (1 - pos * cost.slip), None))
        moves.append(mv)
    return trades


def vbreak(a: Asset, k=0.5, ma_filter=0, mode="long", stop_at_open=False, cost=Costs()):
    """Cassure de volatilité : achat si le prix dépasse ouverture + k × amplitude de la veille
    (vente si mode="ls" et le prix casse ouverture − k × amplitude). Filtre de tendance optionnel :
    long seulement si l'ouverture est au-dessus de la moyenne des `ma_filter` dernières clôtures,
    short seulement en dessous. Sortie en fin de journée UTC, ou au retour à l'ouverture (stop)."""
    d = a.daily
    rng = (d.high - d.low).shift(1)
    ma = d.close.rolling(ma_filter).mean().shift(1) if ma_filter else None
    trades = []
    for day, i0, i1 in a.sessions("utc"):
        r = rng.get(day, np.nan)
        if np.isnan(r):
            continue
        op = a.o[i0]
        m = ma.get(day, np.nan) if ma is not None else np.nan
        allow_long = ma is None or op > m
        allow_short = mode == "ls" and (ma is None or op < m)
        up, dn = op + k * r, op - k * r
        for j in range(i0, i1):
            side = 0
            if allow_long and a.h[j] >= up:
                side, raw = 1, max(a.o[j], up)
            elif allow_short and a.l[j] <= dn:
                side, raw = -1, min(a.o[j], dn)
            if not side:
                continue
            entry = raw * (1 + side * cost.slip)
            exit_px = None
            if stop_at_open:
                for q in range(j + 1, i1):
                    if (side > 0 and a.l[q] <= op) or (side < 0 and a.h[q] >= op):
                        px = min(a.o[q], op) if side > 0 else max(a.o[q], op)
                        exit_px = px * (1 - side * cost.slip)
                        break
            if exit_px is None:
                exit_px = a.c[i1 - 1] * (1 - side * cost.slip)
            trades.append((day, side, entry, exit_px, None))
            break
    return trades


def itm(a: Asset, last_min=60, cost=Costs()):
    n = last_min // BAR
    trades = []
    for d, i0, i1 in a.sessions("utc"):
        j = i1 - n
        r = a.c[j - 1] / a.o[i0] - 1
        if r == 0:
            continue
        side = 1 if r > 0 else -1
        trades.append((d, side, a.c[j - 1] * (1 + side * cost.slip), a.c[i1 - 1] * (1 - side * cost.slip), None))
    return trades


# --------------------------------------------------------------------------- portefeuille

def daily_returns(a: Asset, trades, sizing="risk", risk=0.01, target_vol=0.02, cap=1.0, cost=Costs()) -> pd.Series:
    """Rendement journalier de la poche de l'actif (frais inclus)."""
    out = {}
    for d, side, e, x, rk in trades:
        if sizing == "risk" and rk:
            f = min(cap, risk / max(rk, 1e-4))
        else:
            dv = a.dvol.get(d, np.nan)
            f = min(cap, target_vol / dv) if dv and not np.isnan(dv) else 0.0
        r = f * (side * (x / e - 1) - 2 * cost.fee)
        out[d] = out.get(d, 0.0) + r
    return pd.Series(out, dtype=float).sort_index()


def stats(r: pd.Series, n_trades: int, days: int) -> dict:
    if r.empty:
        return {"Sharpe": np.nan, "par an": np.nan, "pire repli": np.nan, "trades/an": 0}
    eq = (1 + r).cumprod()
    years = days / 365
    return {"Sharpe": r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else np.nan,
            "par an": eq.iloc[-1] ** (1 / years) - 1,
            "pire repli": (eq / eq.cummax() - 1).min(),
            "trades/an": n_trades / years}


def portfolio(assets, fn, params, sizing, cost=Costs(), start="2020-01-01", end=None):
    """Capital réparti également entre les actifs ; retourne les rendements journaliers du portefeuille."""
    cols, n = {}, 0
    for a in assets:
        tr = fn(a, cost=cost, **params)
        tr = [t for t in tr if t[0] >= pd.Timestamp(start) and (end is None or t[0] <= pd.Timestamp(end))]
        n += len(tr)
        cols[a.sym] = daily_returns(a, tr, cost=cost, **sizing)
    idx = pd.date_range(start, end or assets[0].daily.index[-1], freq="D")
    df = pd.DataFrame(cols).reindex(idx).fillna(0.0)
    return df.mean(axis=1), n
