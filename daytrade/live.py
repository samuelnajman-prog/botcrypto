"""Bot n°2 — day trading crypto en paper trading : cassure de volatilité intraday.

Règle v2 « agressive » (validée dans daytrade/research.py, voir STRATEGIE_DAYTRADING.md §9) :
  Chaque jour à 00:00 UTC, pour BTC, ETH, SOL, BNB, XRP, DOGE :
    - niveau d'achat = ouverture du jour + 0,8 × (plus haut − plus bas de la veille)
    - seulement si l'ouverture est au-dessus de la moyenne des 20 dernières clôtures (tendance)
  Si le prix touche le niveau pendant la journée : achat via contrat perpétuel (ordre stop virtuel),
  revente à la clôture du jour (00:00 UTC). Une seule entrée par crypto et par jour.
  Taille d'une position = min(34 %, 6 % / volatilité journalière 14 j) du capital d'ouverture,
  attribuée dans l'ordre des déclenchements, exposition totale ≤ 1,5 × le capital (levier
  maximal 1,5, sous le plafond légal de 2 pour les particuliers dans l'UE).
  Comptabilité de type marge : le capital reste en stablecoin, seuls frais et P&L le modifient.

Exécution simulée comme un ordre stop posé à l'avance : remplissage au niveau (ou à l'ouverture
de la bougie 5 min si elle s'ouvre au-dessus), + slippage et frais. Les remplissages dépendent
des bougies, pas de l'heure à laquelle le script tourne : le lancer toutes les heures suffit.

État : state/day/state.json, trades.csv, equity.csv ; page : docs/day.json.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from bot.data import API, _get

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("DAY_STATE_DIR", ROOT / "state" / "day"))
SITE = ROOT / "docs"

ASSETS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
K = 0.8                 # fraction de l'amplitude de la veille
MA = 20                 # filtre de tendance (jours)
TARGET_VOL = 0.06       # volatilité journalière visée par position
CAP = 0.34              # taille maximale d'une position (fraction du capital d'ouverture)
LEV = 1.5               # exposition totale maximale (× capital)
VOL_WINDOW = 14
FEE = 0.0006            # par côté : taker perpétuel 0,05 % + ~0,01 % de financement moyen
SLIP = 0.0003           # par ordre


# ----------------------------------------------------------------------------- données

def _klines(symbol: str, interval: str, start_ms: int | None = None, limit: int = 1000) -> pd.DataFrame:
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    r = _get(f"{API}/klines", params)
    if r.status_code != 200:
        raise RuntimeError(f"klines {symbol} {interval}: {r.status_code}")
    rows = r.json()
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "v", "ct", "qv", "n", "a", "b", "i"])
    df.index = pd.to_datetime(df.t, unit="ms")
    return df[["open", "high", "low", "close"]].astype(float)


def market(now: pd.Timestamp, since: pd.Timestamp | None = None) -> dict:
    """Pour chaque actif : bougies journalières clôturées (60 j) et bougies 5 min depuis `since`
    (par défaut la veille 00:00 UTC), paginées si le bot a manqué plusieurs jours."""
    out = {}
    since = min(since or now.normalize(), now.normalize()) - pd.Timedelta(days=1)
    for a in ASSETS:
        sym = a + "USDT"
        d = _klines(sym, "1d", limit=60)
        d = d[d.index + pd.Timedelta(days=1) <= now]                 # journées clôturées
        parts, start = [], int(since.timestamp() * 1000)
        while True:
            m = _klines(sym, "5m", start_ms=start)
            if m.empty:
                break
            parts.append(m)
            if len(m) < 1000:
                break
            start = int(m.index[-1].timestamp() * 1000) + 300_000
        m = pd.concat(parts) if parts else pd.DataFrame(columns=["open", "high", "low", "close"])
        m = m[~m.index.duplicated()]
        m = m[m.index + pd.Timedelta(minutes=5) <= now]              # bougies 5 min clôturées
        out[a] = {"daily": d, "m5": m}
    return out


def day_plan(daily: pd.DataFrame, day: pd.Timestamp, day_open: float) -> dict:
    """Ordre du jour pour un actif, à partir des seules journées clôturées AVANT `day`."""
    hist = daily[daily.index < day]
    if len(hist) < max(MA, VOL_WINDOW) + 1:
        return {"active": False, "reason": "historique insuffisant"}
    prev = hist.iloc[-1]
    level = day_open + K * (prev.high - prev.low)
    ma = hist.close.tail(MA).mean()
    vol = hist.close.pct_change().tail(VOL_WINDOW).std()
    frac = float(min(CAP, TARGET_VOL / vol)) if vol > 0 else 0.0
    plan = {"open": day_open, "level": float(level), "ma10": float(ma), "vol14": float(vol), "size_frac": frac,
            "prev_range": float(prev.high - prev.low)}
    if day_open <= ma:
        plan.update(active=False, reason="tendance baissière (ouverture sous la moyenne 10 jours)")
    else:
        plan.update(active=True, reason="ordre d'achat en attente")
    return plan


def find_fill(m5: pd.DataFrame, day: pd.Timestamp, level: float, not_before: pd.Timestamp | None = None):
    """Première bougie 5 min du jour dont le plus haut touche le niveau → (heure, prix brut).
    `not_before` : aucun remplissage avant le démarrage du bot (pas de trade rétroactif)."""
    bars = m5[(m5.index >= day) & (m5.index < day + pd.Timedelta(days=1))]
    hit = bars[bars.high >= level]
    if hit.empty:
        return None
    if not_before is not None and hit.index[0] < not_before:
        return None   # cassure avant le démarrage : la stratégie aurait acheté plus tôt, on passe la journée
    t = hit.index[0]
    return t, float(max(hit.open.iloc[0], level))


# ----------------------------------------------------------------------------- état

def new_state(capital: float) -> dict:
    return {"version": 1, "initial": capital, "cash": capital, "peak": capital, "day": None, "orders": {},
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "btc_at_start": None,
            "stats": {"trades": 0, "wins": 0, "days_traded": 0}}


def load_state() -> dict | None:
    p = STATE_DIR / "state.json"
    return json.loads(p.read_text()) if p.exists() else None


def save_state(st: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "state.json").write_text(json.dumps(st, indent=1))


def _append(name: str, header: list, rows: list) -> None:
    p = STATE_DIR / name
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


# ----------------------------------------------------------------------------- moteur

def _open_day(st: dict, mk: dict, day: pd.Timestamp, only: list | None = None) -> None:
    """Prépare les ordres du jour (taille fixée sur le capital à l'ouverture, toutes positions soldées)."""
    if only is None:
        st["day"] = str(day.date())
        st["equity_open"] = st["cash"]
        st["orders"] = {}
    equity_open = st.get("equity_open", st["cash"])
    for a in (only or ASSETS):
        m5 = mk[a]["m5"]
        first = m5[m5.index >= day]
        if first.empty or first.index[0] != day:
            st["orders"][a] = {"active": False, "reason": "pas encore de prix pour ce jour"}
            continue
        plan = day_plan(mk[a]["daily"], day, float(first.open.iloc[0]))
        plan["notional"] = round(equity_open * plan.get("size_frac", 0.0), 2)   # taille si capacité dispo
        plan["filled"] = None
        st["orders"][a] = plan


def _update_fills(st: dict, mk: dict, day: pd.Timestamp, now_iso: str, rows: list) -> None:
    """Nouveaux déclenchements, traités dans l'ordre chronologique ; chacun reçoit sa taille
    tant que l'exposition totale du jour reste ≤ LEV × capital d'ouverture."""
    start = pd.Timestamp(st["started_at"]).tz_convert("UTC").tz_localize(None).floor("5min")
    eq0 = st.get("equity_open", st["cash"])
    used = sum(o["filled"]["notional"] for o in st["orders"].values() if o.get("filled"))
    hits = []
    for a, o in st["orders"].items():
        if not o.get("active") or o.get("filled") or o.get("skipped"):
            continue
        f = find_fill(mk[a]["m5"], day, o["level"], not_before=start)
        if f is not None:
            hits.append((f[0], a, f[1]))
    for t, a, raw in sorted(hits):
        o = st["orders"][a]
        notional = min(o["notional"], max(0.0, LEV * eq0 - used))
        if notional < 1.0:
            o["skipped"] = True
            o["reason"] = "déclenché, mais levier maximal déjà atteint"
            continue
        price = raw * (1 + SLIP)
        units = notional / price
        fee = notional * FEE
        st["cash"] -= fee
        used += notional
        o["filled"] = {"time": str(t), "price": price, "units": units, "fee": fee, "notional": round(notional, 2)}
        o["reason"] = "position ouverte, revente à 00:00 UTC"
        rows.append([now_iso, str(day.date()), a, "BUY", units, price, notional, fee, str(t)])


def _close_day(st: dict, mk: dict, day: pd.Timestamp, now_iso: str, rows: list) -> list:
    """Revend les positions du jour au cours de clôture (00:00 UTC)."""
    closed = []
    traded = False
    for a, o in st["orders"].items():
        f = o.get("filled") if o.get("active") else None
        if not f:
            continue
        bars = mk[a]["m5"]
        bars = bars[(bars.index >= day) & (bars.index < day + pd.Timedelta(days=1))]
        px = float(bars.close.iloc[-1]) * (1 - SLIP)
        val = f["units"] * px
        fee = val * FEE
        gross = f["units"] * (px - f["price"])
        st["cash"] += gross - fee                      # marge : seul le P&L touche le capital
        pnl = gross - fee - f["fee"]
        st["stats"]["trades"] += 1
        st["stats"]["wins"] += int(pnl > 0)
        traded = True
        rows.append([now_iso, str(day.date()), a, "SELL", -f["units"], px, val, fee, f"clôture, P&L {pnl:+.2f}"])
        closed.append((a, pnl))
    st["stats"]["days_traded"] += int(traded)
    return closed


def mark_to_market(st: dict, mk: dict) -> tuple[float, dict]:
    """Équité = capital + P&L latent des positions ouvertes ; pos = exposition (notionnel) par crypto."""
    pos, upnl = {}, 0.0
    for a, o in st["orders"].items():
        f = o.get("filled") if o.get("active") else None
        if f:
            last = float(mk[a]["m5"].close.iloc[-1])
            pos[a] = f["units"] * last
            upnl += f["units"] * (last - f["price"])
    return st["cash"] + upnl, pos


def run_once(capital: float = 10_000.0, now: pd.Timestamp | None = None, mk: dict | None = None) -> str:
    now = now or pd.Timestamp.now("UTC").tz_localize(None)
    st = load_state() or new_state(capital)
    mk = mk or market(now, pd.Timestamp(st["day"]) if st["day"] else None)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = now.normalize()
    rows, closed_all = [], []

    if st["day"] is None:
        _open_day(st, mk, today)
    # Rattrape les journées terminées depuis le dernier passage
    while pd.Timestamp(st["day"]) < today:
        d = pd.Timestamp(st["day"])
        _update_fills(st, mk, d, now_iso, rows)
        closed_all += _close_day(st, mk, d, now_iso, rows)
        _open_day(st, mk, d + pd.Timedelta(days=1))
    # Journée en cours : ordres déclenchés depuis le dernier passage ?
    missing = [a for a, o in st["orders"].items() if o.get("reason") == "pas encore de prix pour ce jour"]
    if missing:
        _open_day(st, mk, today, only=missing)
    _update_fills(st, mk, today, now_iso, rows)

    equity, pos = mark_to_market(st, mk)
    st["peak"] = max(st["peak"], equity)
    btc = float(mk["BTC"]["m5"].close.iloc[-1])
    if st["btc_at_start"] is None:
        st["btc_at_start"] = btc
    save_state(st)
    _append("trades.csv", ["ts", "day", "asset", "side", "units", "price", "value", "fee", "info"], rows)
    _append("equity.csv", ["ts", "equity", "cash", "open_positions", "btc"],
            [[now_iso, round(equity, 2), round(st["cash"], 2), len(pos), btc]])
    export_site(st, mk, equity, pos)

    lines = [f"⚡ Bot n°2 day trading — {now:%d/%m %H:%M} UTC",
             f"Équité : {equity:,.0f} USDT ({equity / st['initial'] - 1:+.2%})"]
    for a, pnl in closed_all:
        lines.append(f"Clôturé {a} : {pnl:+.2f} $")
    for a, o in st["orders"].items():
        if o.get("filled"):
            lines.append(f"{a} : acheté à {o['filled']['price']:,.4f}, position ouverte")
        elif o.get("active"):
            last = float(mk[a]["m5"].close.iloc[-1])
            lines.append(f"{a} : achat si ≥ {o['level']:,.4f} (à {o['level'] / last - 1:+.1%})")
        else:
            lines.append(f"{a} : pas d'ordre ({o.get('reason')})")
    report = "\n".join(lines)
    if rows:                       # notification seulement quand il se passe quelque chose
        from bot.paper import notify
        notify(report)
    return report


def export_site(st: dict, mk: dict, equity: float, pos: dict) -> None:
    eq_path = STATE_DIR / "equity.csv"
    eq = pd.read_csv(eq_path) if eq_path.exists() else pd.DataFrame()
    tr_path = STATE_DIR / "trades.csv"
    tr = pd.read_csv(tr_path) if tr_path.exists() else pd.DataFrame()
    orders = []
    for a in ASSETS:
        o = dict(st["orders"].get(a, {}))
        o["asset"], o["symbol"] = a, a + "USDT"
        o["last_close_5m"] = float(mk[a]["m5"].close.iloc[-1])
        orders.append(o)
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "day": st["day"], "initial": st["initial"], "cash": round(st["cash"], 2), "peak": round(st["peak"], 2),
        "started_at": st["started_at"], "btc_at_start": st["btc_at_start"], "stats": st["stats"],
        "equity_open": round(st.get("equity_open", st["cash"]), 2),
        "rules": {"k": K, "ma": MA, "target_vol": TARGET_VOL, "cap": CAP, "lev": LEV, "fee": FEE, "slip": SLIP,
                  "assets": ASSETS, "accounting": "margin"},
        "orders": orders,
        "equity_history": [] if eq.empty else [{"ts": r.ts, "equity": float(r.equity), "btc": float(r.btc)} for r in eq.itertuples()],
        "trades": [] if tr.empty else [{"day": r.day, "asset": r.asset, "side": r.side, "price": float(r.price),
                                        "value": round(abs(float(r.value)), 2), "info": str(r.info)}
                                       for r in tr.tail(30).iloc[::-1].itertuples()],
        "backtest": {"oos_cagr": 0.216, "oos_maxdd": -0.275, "oos_sharpe": 0.94, "trades_per_year": 269},
    }
    SITE.mkdir(exist_ok=True)
    (SITE / "day.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))


def status() -> str:
    st = load_state()
    if not st:
        return "Bot n°2 pas encore démarré : python main.py day run"
    return json.dumps({k: st[k] for k in ("day", "cash", "peak", "stats")}, ensure_ascii=False, indent=1)
