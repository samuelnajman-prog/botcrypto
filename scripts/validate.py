"""Validation hors échantillon (2023-01 → aujourd'hui) de la config retenue, + benchmarks,
+ sensibilité aux coûts et au voisinage des paramètres. Produit data/validation.json."""
import sys, json
from dataclasses import replace
sys.path.insert(0, ".")
import pandas as pd
from bot.data import load_panel, to_matrix
from bot.strategy import Config
from bot.backtest import run_backtest, prepare, buy_and_hold_btc, equal_weight_fn
from bot.metrics import summary, table

panel = load_panel(verbose=False)
mats = {f: to_matrix(panel, f) for f in ["open", "high", "low", "close", "quote_volume"]}
cfg = Config()
sig = prepare(mats, cfg)
periods = {"In-sample 2018-06→2022-12": ("2018-06-01", "2022-12-31"),
           "Hors échantillon 2023-01→2026-11": ("2023-01-01", None),
           "Complet 2018-06→2026-11": ("2018-06-01", None)}
out = {}
for name, (a, b) in periods.items():
    bot = run_backtest(mats, cfg, a, b, sig=sig)
    ew = run_backtest(mats, cfg, a, b, sig=sig, weight_fn=equal_weight_fn(10))
    bh = buy_and_hold_btc(mats, a, b)
    res = {"Bot": summary(bot.equity, bot.exposure, bot.turnover, len(bot.trades)),
           "BTC buy&hold": summary(bh), "Équipondéré top10": summary(ew.equity, ew.exposure, ew.turnover, len(ew.trades))}
    out[name] = res
    print(f"\n=== {name} ===\n", table(res).to_string())
    if name.startswith("Complet"):
        full = bot; bh_full = bh; ew_full = ew

print("\nRendements annuels (bot vs BTC):")
yr = pd.DataFrame({"Bot": full.equity.resample("YE").last(), "BTC": bh_full.resample("YE").last()})
yr = pd.concat([pd.DataFrame({"Bot":[full.equity.iloc[0]],"BTC":[bh_full.iloc[0]]}, index=[full.equity.index[0]]), yr]).pct_change().dropna()
print(yr.round(3))

print("\nSensibilité OOS (aucune sélection faite ici) :")
var = {"coûts x2 (30 bps)": dict(cost_bps=30), "target_vol 0.4": dict(target_vol=0.4), "target_vol 0.8": dict(target_vol=0.8),
       "max_positions 6": dict(max_positions=6), "top_n 15": dict(top_n_liquid=15), "signaux lents": dict(ewmac_pairs=((16,64),(32,128),(64,256))),
       "sans filtre régime": dict(regime_filter=False, risk_off_gross=1.0)}
sens = {}
for k, v in var.items():
    c2 = replace(cfg, **v)
    s2 = prepare(mats, c2) if any(x in v for x in ("top_n_liquid","ewmac_pairs","regime_filter")) else sig
    r = run_backtest(mats, c2, "2023-01-01", sig=s2)
    sens[k] = summary(r.equity, r.exposure, r.turnover, len(r.trades))
print(table(sens).loc[["CAGR","Sharpe","Max drawdown","6 mois - médiane","6 mois - pire 5 %"]].to_string())
out["Sensibilité OOS"] = sens
full.equity.to_csv("data/bt_equity.csv"); full.exposure.to_csv("data/bt_exposure.csv")
bh_full.to_csv("data/bt_btc.csv"); ew_full.equity.to_csv("data/bt_ew.csv")
full.trades.to_csv("data/bt_trades.csv", index=False); full.weights.to_csv("data/bt_weights.csv")
json.dump(out, open("data/validation.json", "w"), indent=1, default=float)
