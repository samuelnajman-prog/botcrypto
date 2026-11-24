"""Compare la v1 (journalière) à des variantes plus rapides / plus larges.

Une seule simulation par variante de 2019-09 à aujourd'hui, puis indicateurs calculés
séparément sur la période de réglage (2019-09 → 2022-12) et la période de test
(2023-01 → aujourd'hui). Les indicateurs utilisent l'équité journalière.
Usage : python scripts/compare.py [noms de variantes...]
"""
import json
import sys
import time
from dataclasses import replace
from multiprocessing import Pool

sys.path.insert(0, ".")
import pandas as pd  # noqa: E402

from bot.backtest import buy_and_hold_btc, prepare, run_backtest  # noqa: E402
from bot.data import CANDIDATE_SETS, load_panel, to_matrix  # noqa: E402
from bot.metrics import summary  # noqa: E402
from bot.strategy import Config  # noqa: E402

START, IS_END, OOS_START = "2019-09-01", "2022-12-31", "2023-01-01"
F4 = ((4, 16), (8, 32), (16, 64))       # 0,7 → 10,7 jours
M4 = ((8, 32), (16, 64), (32, 128))     # 1,3 → 21 jours
S4 = ((16, 64), (32, 128), (64, 256))   # 2,7 → 43 jours

VARIANTS = {
    "v1 jour core": dict(),
    "jour wide": dict(universe="wide"),
    "jour wide top30": dict(universe="wide", top_n_liquid=30, max_positions=12),
    "jour core régime souple": dict(risk_off_core_only=False),
    "jour wide régime souple": dict(universe="wide", risk_off_core_only=False),
    "4h wide très rapide": dict(bar_hours=4, universe="wide", ewmac_pairs=F4),
    "4h wide rapide": dict(bar_hours=4, universe="wide", ewmac_pairs=M4),
    "4h wide moyen": dict(bar_hours=4, universe="wide", ewmac_pairs=S4),
    "4h core rapide": dict(bar_hours=4, ewmac_pairs=M4),
    "4h core moyen": dict(bar_hours=4, ewmac_pairs=S4),
    "4h core très rapide": dict(bar_hours=4, ewmac_pairs=F4),
    "v1 coûts x2": dict(cost_bps=30),
    "4h core rapide coûts x2": dict(bar_hours=4, ewmac_pairs=M4, cost_bps=30),
}

MATS = {}


def mats_for(cfg):
    key = ("1d" if cfg.bar_hours == 24 else "4h", cfg.universe)
    if key not in MATS:
        panel = load_panel(CANDIDATE_SETS[cfg.universe], verbose=False, interval=key[0], workers=8)
        MATS[key] = {f: to_matrix(panel, f) for f in ["open", "high", "low", "close", "quote_volume"]}
    return MATS[key]


def run(name):
    t0 = time.time()
    cfg = replace(Config(), **VARIANTS[name])
    m = mats_for(cfg)
    res = run_backtest(m, cfg, START, sig=prepare(m, cfg))
    eq = res.equity.resample("D").last().dropna()
    ex = res.exposure.resample("D").mean().dropna()
    to = res.turnover.resample("D").sum()
    out = {"variante": name, "sec": round(time.time() - t0)}
    for lab, a, b in (("IS", START, IS_END), ("OOS", OOS_START, None)):
        e = eq.loc[a:b]
        s = summary(e / e.iloc[0], ex.loc[a:b], to.loc[a:b])
        out.update({f"{lab} CAGR": s["CAGR"], f"{lab} Sharpe": s["Sharpe"], f"{lab} MaxDD": s["Max drawdown"],
                    f"{lab} 6m méd": s["6 mois - médiane"], f"{lab} 6m p5": s["6 mois - pire 5 %"],
                    f"{lab} turnover": s["Turnover annuel"], f"{lab} expo": s["Exposition moyenne"]})
    eq.to_csv(f"data/cmp_{name.replace(' ', '_')}.csv")
    return out


if __name__ == "__main__":
    names = sys.argv[1:] or list(VARIANTS)
    with Pool(2) as p:
        rows = p.map(run, names, chunksize=1)
    m = mats_for(Config())
    for lab, a, b in (("IS", START, IS_END), ("OOS", OOS_START, None)):
        bh = buy_and_hold_btc(m, a, b)
        s = summary(bh)
        print(f"BTC buy&hold {lab}: CAGR {s['CAGR']:+.1%} Sharpe {s['Sharpe']:.2f} MaxDD {s['Max drawdown']:.1%} "
              f"6m méd {s['6 mois - médiane']:+.1%} p5 {s['6 mois - pire 5 %']:+.1%}")
    df = pd.DataFrame(rows).set_index("variante")
    pd.set_option("display.width", 250)
    for lab in ("IS", "OOS"):
        cols = [c for c in df.columns if c.startswith(lab + " ")]
        t = df[cols].copy()
        for c in cols:
            if "Sharpe" in c or "turnover" in c:
                t[c] = t[c].map(lambda v: f"{v:.2f}")
            else:
                t[c] = t[c].map(lambda v: f"{v:+.1%}")
        print(f"\n=== {lab} ===\n" + t.to_string())
    print("\ndurées (s):", df["sec"].to_dict())
    df.to_json("data/compare.json", orient="index", indent=1)
