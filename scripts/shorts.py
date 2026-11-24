"""Test d'une poche short (perpétuels 1x, funding réel) en complément de la v1.

Réglage jugé sur 2020-01 → 2022-12 (début de l'historique de funding), verdict sur
2023-01 → aujourd'hui. Une simulation par variante, indicateurs par sous-période.
"""
import sys
from dataclasses import replace
from multiprocessing import Pool

sys.path.insert(0, ".")
import pandas as pd  # noqa: E402

from bot.backtest import buy_and_hold_btc, prepare, run_backtest  # noqa: E402
from bot.data import CANDIDATES, load_panel, to_matrix  # noqa: E402
from bot.funding import load_funding  # noqa: E402
from bot.metrics import summary  # noqa: E402
from bot.strategy import Config  # noqa: E402

START, IS_END, OOS_START = "2020-01-01", "2022-12-31", "2023-01-01"
VARIANTS = {
    "v1 (long/cash)": dict(),
    "short BTC/ETH 35 %": dict(allow_short=True, short_universe="core", short_gross=0.35),
    "short BTC/ETH 60 %": dict(allow_short=True, short_universe="core", short_gross=0.60),
    "short top liquides 50 %": dict(allow_short=True, short_universe="liquid", short_gross=0.50),
    "short top liquides 100 %": dict(allow_short=True, short_universe="liquid", short_gross=1.0),
    "long/short permanent": dict(allow_short=True, short_universe="liquid", short_gross=0.50, short_only_risk_off=False),
    "short BTC/ETH 35 %, frais x2": dict(allow_short=True, short_universe="core", short_gross=0.35, cost_bps=30),
}

panel = load_panel(CANDIDATES, verbose=False)
MATS = {f: to_matrix(panel, f) for f in ["open", "high", "low", "close", "quote_volume"]}
MATS["funding"] = load_funding(CANDIDATES)


def run(name):
    cfg = replace(Config(), **VARIANTS[name])
    res = run_backtest(MATS, cfg, START, sig=prepare(MATS, cfg))
    eq, ex = res.equity, res.exposure
    short_days = (res.weights.clip(upper=0).sum(axis=1) < -1e-6).mean() if len(res.weights.columns) else 0
    out = {"variante": name, "jours short": short_days}
    for lab, a, b in (("IS", START, IS_END), ("OOS", OOS_START, None)):
        e = eq.loc[a:b]
        s = summary(e / e.iloc[0], ex.loc[a:b], res.turnover.loc[a:b])
        out.update({f"{lab} CAGR": s["CAGR"], f"{lab} Sharpe": s["Sharpe"], f"{lab} MaxDD": s["Max drawdown"],
                    f"{lab} 6m méd": s["6 mois - médiane"], f"{lab} 6m p5": s["6 mois - pire 5 %"]})
    yr = eq.resample("YE").last()
    yr = pd.concat([eq.iloc[[0]], yr]).pct_change().dropna()
    out.update({f"{d.year}": v for d, v in yr.items()})
    eq.to_csv("data/short_" + "".join(ch if ch.isalnum() else "_" for ch in name) + ".csv")
    return out


if __name__ == "__main__":
    names = sys.argv[1:] or list(VARIANTS)
    with Pool(2) as p:
        rows = p.map(run, names, chunksize=1)
    for lab, a, b in (("IS", START, IS_END), ("OOS", OOS_START, None)):
        s = summary(buy_and_hold_btc(MATS, a, b))
        print(f"BTC buy&hold {lab}: CAGR {s['CAGR']:+.1%} Sharpe {s['Sharpe']:.2f} MaxDD {s['Max drawdown']:.1%}")
    df = pd.DataFrame(rows).set_index("variante")
    pd.set_option("display.width", 250)
    fmt = lambda c: (lambda v: f"{v:.2f}") if "Sharpe" in c else (lambda v: f"{v:+.1%}")
    for group in (["jours short"] + [c for c in df.columns if c.startswith("IS ")],
                  [c for c in df.columns if c.startswith("OOS ")],
                  [c for c in df.columns if c.isdigit()]):
        t = df[group].copy()
        for c in group:
            t[c] = t[c].map(fmt(c))
        print("\n" + t.to_string())
    df.to_json("data/shorts.json", orient="index", indent=1)
