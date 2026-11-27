"""Recherche bot n°2 (actions US) : reproduit les tableaux de STRATEGIE_DAYTRADING.md et exporte
docs/stocks_backtest.json (courbes et chiffres affichés sur la page).

Pré-requis : données téléchargées (voir stocks/data.py) dans $STOCK_DATA :
  1d/ (10 ans, toutes les actions), 1h/ + 1hpre/ (730 séances), 5m/ + 5mpre/ (60 séances).
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.stocks_panel import get  # noqa: E402
from stocks import data as D  # noqa: E402
from stocks import research as R  # noqa: E402
from stocks.live import CONFIG  # noqa: E402


def stats(x: pd.Series) -> dict:
    eq = (1 + x).cumprod()
    return {"cagr": float(eq.iloc[-1] ** (252 / len(x)) - 1), "sharpe": float(x.mean() / x.std() * np.sqrt(252)),
            "maxdd": float((eq / eq.cummax() - 1).min()), "worst_day": float(x.min()), "days": len(x),
            "total": float(eq.iloc[-1] - 1)}


def params() -> R.GapParams:
    c = CONFIG
    return R.GapParams(min_gap=c["min_gap"], max_gap=c["max_gap"], min_price=c["min_price"],
                       min_dvol=c["min_dvol"], top_n=c["top_n"], stop=c["stop_pct"], weight=c["weight"])


def daily_10y(g: R.GapParams, slip=0.0003, cs=0.0002) -> pd.Series:
    """Version journalière 10 ans (sélection sur l'ouverture officielle : borne haute, biaisée)."""
    O, H, L, C, V = pd.read_pickle(D.DATA / "daily_panel.pkl")
    pc = C.shift(1)
    gap = O / pc - 1
    dv = (C * V).rolling(20).mean().shift(1)
    liq = (pc > g.min_price) & (dv > g.min_dvol) & (gap > g.min_gap) & (gap < g.max_gap)
    sel = gap.where(liq).rank(axis=1, ascending=False) <= g.top_n
    e = O * (1 - slip)
    stp = e * (1 + g.stop)
    ex = pd.DataFrame(np.where(H >= stp, stp, C) * (1 + slip), index=O.index, columns=O.columns)
    r = (-(ex / e - 1) - 2 * cs).where(sel)
    return (r.sum(axis=1) * g.weight).loc["2017":]


def main() -> None:
    g = params()
    out = {"config": CONFIG, "samples": {}}
    # 1) 2,9 ans en bougies 1 h, sélection sur le cours de pré-séance de 9:00 (réaliste)
    P = get("1h")
    days = P.days[20:]
    pg = R.premarket_gap(P, {s: D.load(s, "1hpre") for s in P.syms}, 540)
    tr = R.gap_trades(P, g, pre_gap=pg)
    tr = tr[tr.day.isin(days)]
    pf = R.gap_portfolio(tr, days, g.weight)
    out["samples"]["hourly_premarket"] = {**stats(pf), "trades": len(tr),
                                          "ret_per_trade_bp": float((tr.side * (tr.exit / tr.entry - 1)).mean() * 1e4)}
    eq = (1 + pf).cumprod()
    out["curve"] = [[str(d), round(float(v), 4)] for d, v in eq.items()]
    out["years"] = {int(y): float((1 + x).prod() - 1) for y, x in pf.groupby(pd.to_datetime(pf.index).year)}
    out["costs"] = {}
    for rt in (10, 20, 30):
        slip, cs = 0.0003 * rt / 10, 0.0002 * rt / 10
        t2 = R.gap_trades(P, g, slip=slip, pre_gap=pg)
        t2 = t2[t2.day.isin(days)]
        out["costs"][rt] = stats(R.gap_portfolio(t2, days, g.weight, cost_side=cs))
    # 2) 40 dernières séances en 5 min, sélection 9:25
    P5 = get("5m")
    d5 = P5.days[20:]
    pg5 = R.premarket_gap(P5, {s: D.load(s, "5mpre") for s in P5.syms}, 565)
    t5 = R.gap_trades(P5, g, pre_gap=pg5)
    t5 = t5[t5.day.isin(d5)]
    p5 = R.gap_portfolio(t5, d5, g.weight)
    out["samples"]["recent_5m"] = {**stats(p5), "trades": len(t5),
                                   "ret_per_trade_bp": float((t5.side * (t5.exit / t5.entry - 1)).mean() * 1e4)}
    # 3) 10 ans journaliers (borne haute)
    p10 = daily_10y(g)
    out["samples"]["daily_10y_upper_bound"] = stats(p10)
    out["years_10y"] = {int(y): float((1 + x).prod() - 1) for y, x in p10.groupby(p10.index.year)}
    # 4) bootstrap 6 mois sur l'échantillon 1 h
    rng = np.random.default_rng(7)
    x = pf.to_numpy()
    res = []
    for _ in range(5000):
        idx = np.concatenate([np.arange(s, s + 10) for s in rng.integers(0, len(x) - 10, 13)])[:126]
        path = np.cumprod(1 + x[idx])
        res.append((path[-1] - 1, (path / np.maximum.accumulate(path) - 1).min()))
    res = np.array(res)
    out["bootstrap_6m"] = {"p5": float(np.percentile(res[:, 0], 5)), "median": float(np.median(res[:, 0])),
                           "p95": float(np.percentile(res[:, 0], 95)), "p_loss": float((res[:, 0] < 0).mean()),
                           "dd_median": float(np.median(res[:, 1])), "dd_p5": float(np.percentile(res[:, 1], 5))}
    (ROOT / "docs" / "stocks_backtest.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: v for k, v in out.items() if k not in ("curve",)}, indent=1, default=str)[:3000])


if __name__ == "__main__":
    main()
