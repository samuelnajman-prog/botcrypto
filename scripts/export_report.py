"""Exporte les résultats du backtest (config par défaut) en JSON pour le tableau de bord.
Chaque fichier contient {rows, meta} ; meta embarque ce script pour qu'il soit vérifiable."""
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
import numpy as np, pandas as pd
from bot.data import load_panel, to_matrix
from bot.strategy import Config
from bot.backtest import run_backtest, prepare, buy_and_hold_btc, equal_weight_fn
from bot.metrics import summary

OUT = Path("data/report"); OUT.mkdir(parents=True, exist_ok=True)
CODE = Path(__file__).read_text()
START, OOS = "2018-06-01", "2023-01-01"
panel = load_panel(verbose=False)
mats = {f: to_matrix(panel, f) for f in ["open", "high", "low", "close", "quote_volume"]}
cfg = Config(); sig = prepare(mats, cfg)

def write(name, rows, note):
    meta = [{"label": "export_report.py", "value": CODE, "type": "code", "lang": "python"},
            {"label": "Données", "value": "Bougies journalières Binance spot (data-api.binance.vision), " + note}]
    (OUT / f"{name}.json").write_text(json.dumps({"rows": rows, "meta": meta}, ensure_ascii=False))

bot = run_backtest(mats, cfg, START, sig=sig)
ew = run_backtest(mats, cfg, START, sig=sig, weight_fn=equal_weight_fn(10))
btc = buy_and_hold_btc(mats, START)
df = pd.DataFrame({"bot": bot.equity, "btc": btc, "ew": ew.equity, "exposure": bot.exposure}).dropna()
w = df.resample("W").last()
norm = w[["bot", "btc", "ew"]] / df[["bot", "btc", "ew"]].iloc[0] * 100
dd = w[["bot", "btc"]] / df[["bot", "btc"]].cummax().resample("W").last() - 1
rows = [{"date": str(i.date()), "bot": round(norm.loc[i, "bot"], 2), "btc": round(norm.loc[i, "btc"], 2),
         "ew": round(norm.loc[i, "ew"], 2), "bot_dd": round(dd.loc[i, "bot"], 4), "btc_dd": round(dd.loc[i, "btc"], 4),
         "exposure": round(w.loc[i, "exposure"], 3)} for i in w.index]
write("equity", rows, "backtest 2018-06-01 → dernière bougie, base 100, points hebdomadaires.")

KEYS = {"Rendement total": "total", "CAGR": "cagr", "Volatilité": "vol", "Sharpe": "sharpe", "Sortino": "sortino",
        "Max drawdown": "maxdd", "Calmar": "calmar", "6 mois - médiane": "m6_median", "6 mois - pire 5 %": "m6_p5",
        "6 mois - meilleur 5 %": "m6_p95", "6 mois - % positifs": "m6_pos", "Exposition moyenne": "exposure"}
metric_rows = []
for period, (a, b) in {"Complet": (START, None), "Hors échantillon": (OOS, None), "In-sample": (START, "2022-12-31")}.items():
    r_bot = run_backtest(mats, cfg, a, b, sig=sig)
    r_ew = run_backtest(mats, cfg, a, b, sig=sig, weight_fn=equal_weight_fn(10))
    r_btc = buy_and_hold_btc(mats, a, b)
    for name, s in {"Bot": summary(r_bot.equity, r_bot.exposure), "BTC buy&hold": summary(r_btc),
                    "Équipondéré top 10": summary(r_ew.equity, r_ew.exposure)}.items():
        metric_rows.append({"period": period, "strategy": name, **{KEYS[k]: (None if pd.isna(v) else round(float(v), 4)) for k, v in s.items()}})
write("metrics", metric_rows, "indicateurs par période et par stratégie ; frais+slippage 0,15 % par côté.")

y = pd.concat([df[["bot", "btc"]].iloc[[0]], df[["bot", "btc"]].resample("YE").last()]).pct_change().dropna()
write("annual", [{"year": str(i.year), "bot": round(y.loc[i, "bot"], 4), "btc": round(y.loc[i, "btc"], 4)} for i in y.index],
      "rendement par année civile (2018 partiel depuis juin, 2026 jusqu'à la dernière bougie).")

roll = df[["bot", "btc"]].pct_change(182).dropna()
bins = np.arange(-0.8, 3.01, 0.1)
hist = [{"from": round(float(lo), 2), "to": round(float(hi), 2),
         "bot": float(((roll.bot >= lo) & (roll.bot < hi)).mean()), "btc": float(((roll.btc >= lo) & (roll.btc < hi)).mean())}
        for lo, hi in zip(bins[:-1], bins[1:])]
write("rolling6m", hist, "distribution des rendements sur 6 mois glissants (182 jours), part des jours par tranche de 10 points.")
print("exported", [p.name for p in OUT.iterdir()], "last bar", df.index[-1].date())
