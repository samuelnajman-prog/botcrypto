"""Deux questions de timing, même protocole que le reste (réglage 2019-09 → 2022-12, test 2023 → aujourd'hui).

1) Réévaluer les MÊMES règles toutes les 4 h au lieu d'une fois par jour aide-t-il ?
   (signaux de même durée que la v1, exprimés en bougies 4h)
2) L'heure de la décision quotidienne compte-t-elle ? Bougies journalières reconstruites
   à partir des bougies 4h, clôturant à 00, 04, 08, 12, 16 ou 20 h UTC.
"""
import sys
from dataclasses import replace
from multiprocessing import Pool

sys.path.insert(0, ".")
import pandas as pd  # noqa: E402

from bot import strategy  # noqa: E402
from bot.backtest import prepare, run_backtest  # noqa: E402
from bot.data import CANDIDATES, daily_from_intraday, load_panel, to_matrix  # noqa: E402
from bot.metrics import summary  # noqa: E402
from bot.strategy import Config  # noqa: E402

START, IS_END, OOS_START = "2019-09-01", "2022-12-31", "2023-01-01"
# Mêmes durées que la v1 (8/32, 16/64, 32/128 jours) en bougies 4h ; scalaires ÷ √6
# car la volatilité d'une bougie 4h est ≈ celle d'un jour ÷ √6.
SAME4H = ((48, 192), (96, 384), (192, 768))
strategy.EWMAC_SCALARS.update({(48, 192): 5.3 / 6 ** .5, (96, 384): 3.75 / 6 ** .5, (192, 768): 2.65 / 6 ** .5})

P4 = load_panel(CANDIDATES, verbose=False, interval="4h")
M4 = {f: to_matrix(P4, f) for f in ["open", "high", "low", "close", "quote_volume"]}


def daily_from_4h(hour: int) -> dict:
    """Bougies journalières dont la clôture tombe à `hour` h UTC."""
    return daily_from_intraday(M4, hour)


def metrics(eq, ex):
    eq = eq.resample("D").last().dropna()
    ex = ex.resample("D").mean().dropna()
    row = {}
    for lab, a, b in (("réglage", START, IS_END), ("test", OOS_START, None)):
        e = eq.loc[a:b]
        s = summary(e / e.iloc[0], ex.loc[a:b])
        row.update({f"Sharpe {lab}": s["Sharpe"], f"par an {lab}": s["CAGR"], f"pire repli {lab}": s["Max drawdown"]})
    return row


def run(job):
    kind, arg = job
    if kind == "4h":
        cfg = replace(Config(), bar_hours=4, ewmac_pairs=SAME4H)
        res = run_backtest(M4, cfg, START, sig=prepare(M4, cfg))
        name = "Mêmes règles, réévaluées toutes les 4 h"
    else:
        m = daily_from_4h(arg)
        cfg = Config()
        res = run_backtest(m, cfg, START, sig=prepare(m, cfg))
        name = f"1 décision/jour à {(arg + 1) % 24:02d}h Paris ({arg:02d}h UTC)"
    turnover = res.turnover.sum() / ((res.equity.index[-1] - res.equity.index[0]).days / 365)
    return {"variante": name, **metrics(res.equity, res.exposure), "ordres/an": len(res.trades) / ((res.equity.index[-1] - res.equity.index[0]).days / 365), "rotation": turnover}


if __name__ == "__main__":
    jobs = [("daily", h) for h in (0, 4, 8, 12, 16, 20)] + [("4h", None)]
    with Pool(2) as p:
        rows = p.map(run, jobs, chunksize=1)
    df = pd.DataFrame(rows).set_index("variante")
    pd.set_option("display.width", 250)
    t = df.copy()
    for c in t.columns:
        t[c] = t[c].map((lambda v: f"{v:.2f}") if "Sharpe" in c else (lambda v: f"{v:.0f}") if c in ("ordres/an",) else (lambda v: f"{v:.1f}x") if c == "rotation" else (lambda v: f"{v:+.1%}"))
    print(t.to_string())
    d = df[df.index.str.startswith("1 décision")]
    print(f"\nÉcart selon l'heure (test) : Sharpe {d['Sharpe test'].min():.2f} → {d['Sharpe test'].max():.2f}, "
          f"par an {d['par an test'].min():+.1%} → {d['par an test'].max():+.1%}")
    df.to_json("data/timing.json", orient="index", indent=1)
