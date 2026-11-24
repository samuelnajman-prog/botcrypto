"""Capital coupé en 6 tranches décidant chacune à une heure différente : effet sur le risque de « mauvaise heure »."""
import sys; sys.path.insert(0, ".")
import pandas as pd
from multiprocessing import Pool
import scripts.timing as T
from bot.backtest import prepare, run_backtest
from bot.strategy import Config
from bot.metrics import summary

def eq_for(h):
    m = T.daily_from_4h(h); cfg = Config()
    r = run_backtest(m, cfg, T.START, sig=prepare(m, cfg))
    # date d'étiquette = début de la journée décalée ; on ramène à la date calendaire de clôture
    e = r.equity.copy(); e.index = e.index + pd.Timedelta(hours=24 + h)
    return h, e, len(r.trades)

with Pool(2) as p:
    res = p.map(eq_for, [0, 4, 8, 12, 16, 20])
curves = {h: e.resample("D").last() for h, e, _ in res}
df = pd.DataFrame(curves).ffill().dropna()
norm = df / df.iloc[0]
combo = norm.mean(axis=1)          # 6 tranches de 1/6 du capital, chacune à son heure
rows = {}
for name, s in [("1 tranche à 01h (actuel)", norm[0]), ("Moyenne des 6 heures", None), ("6 tranches (une toutes les 4 h)", combo)]:
    if s is None: continue
    r = {}
    for lab, a, b in (("réglage", T.START, T.IS_END), ("test", T.OOS_START, None)):
        e = s.loc[a:b]; m = summary(e / e.iloc[0])
        r[f"Sharpe {lab}"] = round(m["Sharpe"], 2); r[f"par an {lab}"] = f"{m['CAGR']:+.1%}"; r[f"pire repli {lab}"] = f"{m['Max drawdown']:.1%}"
        r[f"6m p5 {lab}"] = f"{m['6 mois - pire 5 %']:+.1%}"
    rows[name] = r
# dispersion : pire et meilleure heure en test
oos = {h: summary(norm[h].loc[T.OOS_START:] / norm[h].loc[T.OOS_START:].iloc[0]) for h in norm}
print(pd.DataFrame(rows).T.to_string())
print("test, heure unique : Sharpe min/max", round(min(v['Sharpe'] for v in oos.values()), 2), round(max(v['Sharpe'] for v in oos.values()), 2),
      "| par an min/max", f"{min(v['CAGR'] for v in oos.values()):+.1%}", f"{max(v['CAGR'] for v in oos.values()):+.1%}",
      "| moyenne Sharpe", round(sum(v['Sharpe'] for v in oos.values()) / 6, 2))
print("ordres/an total 6 tranches:", round(sum(n for *_, n in res) / ((df.index[-1] - df.index[0]).days / 365)))
