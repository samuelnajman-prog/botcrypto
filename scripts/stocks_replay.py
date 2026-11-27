"""Test « en avant » du bot actions : rejoue les dernières séances avec EXACTEMENT le code du bot
(stocks/live.py : scan de 9:25, filtres, ordres, stops, frais), séance après séance, en ne lui
donnant à chaque fois que les données disponibles ce jour-là. Compare au backtest de recherche.

Limite : l'historique des titres d'actualité n'est pas disponible, le filtre « rachat » n'est
donc pas appliqué dans ce rejeu (il ne peut qu'écarter des titres).
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

from stocks import data as D  # noqa: E402
from stocks import live as L  # noqa: E402


def frames(syms):
    out = {}
    for s in syms:
        reg, pre = D.load(s, "5m"), D.load(s, "5mpre")
        if reg is None:
            continue
        df = pd.concat([x for x in (reg, pre) if x is not None]).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        loc = df.index.tz_convert(L.NYZ)
        out[s] = df.assign(day=loc.date, mins=loc.hour * 60 + loc.minute)
    return out


def main(n_days: int = 40) -> None:
    # univers « point dans le temps » : toutes les actions ordinaires (hors ETF) dont on a les
    # bougies ; le filtre de liquidité à 50 M$/j est appliqué chaque jour avec les données de la veille
    # (prendre la liste liquide d'aujourd'hui écarterait les titres qui ont perdu leur liquidité
    # après avoir chuté — un biais défavorable à la stratégie).
    common = set(D.symbol_directory(D.DATA / "raw")["symbol"])
    syms = sorted(p.stem for p in (D.DATA / "5m").glob("*.parquet") if p.stem in common)
    data = frames(syms)
    daily = {}
    for s in syms:
        d = D.load(s, "1d")
        if d is not None:
            d.index = d.index.tz_convert(L.NYZ).date
            daily[s] = d[~pd.Index(d.index).duplicated(keep="last")]

    def check_daily(s, day):
        d = daily[s]
        d = d[d.index < day]
        return float(d["close"].iloc[-1]), float((d["close"] * d["volume"]).tail(20).mean())

    spy = frames(["SPY"])["SPY"]
    days = sorted({d for d, m in zip(spy["day"], spy["mins"]) if 570 <= m < 960})[-n_days:]
    eq = L.CONFIG["capital"]
    rows, trades = [], []
    for day in days:
        upto = {s: df[df["day"] <= day] for s, df in data.items()}
        sel = L.select(day, upto, pd.Timestamp.now(tz="UTC").to_pydatetime(), check_daily, lambda *a: [])
        sess = {"selection": sel, "positions": {}, "skipped": []}
        L.open_positions(sess, day, upto, eq)
        L.update_positions(sess, day, upto, done=True)
        pnl = sum(L.pnl(p) for p in sess["positions"].values())
        rows.append({"day": str(day), "trades": len(sess["positions"]), "pnl": round(pnl, 2), "ret": pnl / eq,
                     "stops": sum(p["status"] == "stop" for p in sess["positions"].values())})
        for s, p in sess["positions"].items():
            trades.append({"day": str(day), "sym": s, "gap": p["gap"], "ret": (p["entry"] - p["exit"]) / p["entry"],
                           "status": p["status"]})
        eq += pnl
    df = pd.DataFrame(rows)
    r = df["ret"]
    curve = (1 + r).cumprod()
    res = {"sessions": len(df), "first": df.day.iloc[0], "last": df.day.iloc[-1], "total": float(curve.iloc[-1] - 1),
           "sharpe": float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else None,
           "maxdd": float((curve / curve.cummax() - 1).min()), "trades": int(df.trades.sum()),
           "stops": int(df.stops.sum()), "ret_per_trade_bp": float(pd.DataFrame(trades)["ret"].mean() * 1e4),
           "worst_day": float(r.min()), "best_day": float(r.max()), "pos_days": float((r > 0).mean()),
           "equity_end": round(eq, 2), "daily": rows}
    (ROOT / "docs" / "stocks_replay.json").write_text(json.dumps(res, indent=0))
    print(json.dumps({k: v for k, v in res.items() if k != "daily"}, indent=1))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
