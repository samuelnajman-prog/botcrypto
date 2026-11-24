"""Recherche de paramètres sur la période IN-SAMPLE uniquement (2018-06 → 2022-12).
La période 2023-01 → aujourd'hui reste intouchée pour la validation hors échantillon."""
import itertools, sys, json
from multiprocessing import Pool
from dataclasses import replace
import pandas as pd
sys.path.insert(0, ".")
from bot.data import load_panel, to_matrix
from bot.strategy import Config
from bot.backtest import run_backtest, prepare
from bot.metrics import summary

IS_START, IS_END = "2018-06-01", "2022-12-31"
panel = load_panel(verbose=False)
MATS = {f: to_matrix(panel, f) for f in ["open", "high", "low", "close", "quote_volume"]}
SIGCACHE = {}

def run(params):
    cfg = replace(Config(), **params)
    key = (cfg.ewmac_pairs, cfg.top_n_liquid, cfg.regime_filter, cfg.vol_span)
    if key not in SIGCACHE:
        SIGCACHE[key] = prepare(MATS, cfg)
    res = run_backtest(MATS, cfg, IS_START, IS_END, sig=SIGCACHE[key])
    s = summary(res.equity, res.exposure, res.turnover, len(res.trades))
    return {**{k: str(v) for k, v in params.items()}, **{k: round(float(v), 3) for k, v in s.items()}}

if __name__ == "__main__":
    stage = sys.argv[1]
    if stage == "A":
        grid = [dict(target_vol=tv, max_positions=k, regime_filter=rf, risk_off_gross=rg)
                for tv, k, (rf, rg) in itertools.product([0.4, 0.6, 0.8], [3, 5, 8],
                                                         [(True, 0.35), (True, 0.0), (False, 1.0)])]
    else:
        base = json.loads(sys.argv[2])
        speeds = {"fast": ((8, 32), (16, 64), (32, 128)), "mid": ((16, 64), (32, 128), (64, 256)),
                  "all4": ((8, 32), (16, 64), (32, 128), (64, 256))}
        grid = [dict(base, ewmac_pairs=speeds[sp], top_n_liquid=n, trailing_atr_mult=st, dd_brake_level=dd)
                for sp, n, st, dd in itertools.product(speeds, [10, 20], [0.0, 3.0], [0.0, 0.25])]
    with Pool(2) as p:
        rows = p.map(run, grid)
    df = pd.DataFrame(rows)
    df.to_csv(f"data/grid_{stage}.csv", index=False)
    cols = [c for c in df.columns if c in grid[0]] + ["CAGR", "Sharpe", "Max drawdown", "Calmar", "6 mois - médiane", "6 mois - pire 5 %", "Exposition moyenne", "Turnover annuel"]
    print(df[cols].sort_values("Calmar", ascending=False).to_string(index=False))
