"""Reproduit la recherche du bot n°2 (day trading). Données : bougies 5 min Binance 2020-01 → 2026-10
(script de téléchargement : scripts/download_5m.py). Réglage jugé sur 2020-2022, test sur 2023-2026.

  python scripts/daytrade_research.py
"""
import itertools
import sys
from multiprocessing import Pool

sys.path.insert(0, ".")
import pandas as pd  # noqa: E402

from daytrade.research import Asset, Costs, itm, noise, orb, pooled, portfolio, stats, vbreak  # noqa: E402

SYMS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
PERIODS = {"réglage": ("2020-01-01", "2022-12-31"), "test": ("2023-01-01", None)}
PERP = Costs(0.0006, 0.0003)        # perpétuels : taker 0,05 % + ~0,01 % de funding, slippage 0,03 %
A = [Asset(s) for s in SYMS]
for a in A:
    a.sessions("utc"); a.sessions("ny")
V = dict(sizing="vol", target_vol=0.02, cap=1)
R = dict(sizing="risk", risk=0.01, cap=1)


def line(fn, params, sizing, cost):
    out = []
    for s, e in PERIODS.values():
        r, n = portfolio(A, fn, params, sizing, cost=cost, start=s, end=e)
        out.append(stats(r, n, len(r)))
    return out


def table_strategies():
    rows = [("ORB séance US 15 min, stop 0,25 ATR", orb, dict(kind="ny", or_min=15, k=0.25), R),
            ("ORB 5 min + volume relatif ≥ 1,5", orb, dict(kind="ny", or_min=5, k=0.25, rvol_min=1.5), R),
            ("ORB 30 min achat seul, stop bas de fourchette", orb, dict(kind="ny", or_min=30, stop="range", mode="long", rvol_min=1.0), R),
            ("Zone de bruit (UTC)", noise, dict(kind="utc"), V),
            ("Zone de bruit (séance US)", noise, dict(kind="ny"), V),
            ("Momentum dernière heure", itm, dict(), V),
            ("Cassure de volatilité k=0,8 + tendance 10 j", vbreak, dict(k=0.8, ma_filter=10), V)]
    print("Stratégie | brut réglage | brut test | net 0,10 % réglage | net 0,10 % test | trades/an")
    for name, fn, p, sz in rows:
        g = line(fn, p, sz, Costs(0, 0)); n = line(fn, p, sz, Costs())
        print(f"{name} | {g[0]['Sharpe']:+.2f} | {g[1]['Sharpe']:+.2f} | {n[0]['Sharpe']:+.2f} | {n[1]['Sharpe']:+.2f} | {n[1]['trades/an']:.0f}")


def one(p):
    k, ma = p
    i, o = line(vbreak, dict(k=k, ma_filter=ma), V, PERP)
    return k, ma, round(i["Sharpe"], 2), round(o["Sharpe"], 2), round(o["par an"], 3)


if __name__ == "__main__":
    table_strategies()
    with Pool(2) as pool:
        g = pool.map(one, list(itertools.product([0.4, 0.5, 0.6, 0.7, 0.8, 1.0], [0, 10, 20])))
    print("\nCassure de volatilité, frais de perpétuels :")
    print(pd.DataFrame(g, columns=["k", "moyenne", "Sharpe réglage", "Sharpe test", "par an test"]).to_string(index=False))
    print("\nRègle retenue (k=0,8, moyenne 10 j, vol cible 3 %/j) selon les frais :")
    for name, c in [("perpétuels", PERP), ("0,10 %", Costs()), ("OKX spot 0,20 %", Costs(0.0020, 0.0003)), ("Kraken Pro spot 0,38 %", Costs(0.0038, 0.0003))]:
        i, o = line(vbreak, dict(k=0.8, ma_filter=10), dict(sizing="vol", target_vol=0.03, cap=1), c)
        print(f"  {name:22s} réglage Sharpe {i['Sharpe']:+.2f} ({i['par an']:+.1%}/an) | test Sharpe {o['Sharpe']:+.2f} ({o['par an']:+.1%}/an, pire baisse {o['pire repli']:.1%})")

    print("\nVersion agressive (v2), capital mutualisé :")
    for name, kw in [("v1 ≈ 1/6, sans levier, moyenne 10 j", dict(params=dict(k=0.8, ma_filter=10), lev=1.0, cap=1 / 6, tv=0.03)),
                     ("v2 : 34 %/trade, levier ≤ 1,5, moyenne 20 j", dict(params=dict(k=0.8, ma_filter=20), lev=1.5, cap=0.34, tv=0.06)),
                     ("v2 modérée : 25 %/trade", dict(params=dict(k=0.8, ma_filter=20), lev=1.5, cap=0.25, tv=0.06))]:
        res = []
        for s_, e_ in PERIODS.values():
            r, n = pooled(A, cost=PERP, start=s_, end=e_, **kw); res.append(stats(r, n, len(r)))
        print(f"  {name:44s} réglage {res[0]['par an']:+.1%} DD {res[0]['pire repli']:.1%} | test {res[1]['par an']:+.1%} Sharpe {res[1]['Sharpe']:.2f} DD {res[1]['pire repli']:.1%}")
