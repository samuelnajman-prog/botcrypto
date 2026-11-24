#!/usr/bin/env python3
"""Bot crypto — point d'entrée.

  python main.py fetch                      télécharge / met à jour l'historique
  python main.py backtest [--start 2018-06-01] [--end AAAA-MM-JJ]
  python main.py signals                    état actuel du marché vu par le bot
  python main.py paper run [--capital 10000] [--force]
  python main.py paper status
  python main.py paper reset [--capital 10000]
  python main.py paper loop                 tourne en continu, 1 exécution/jour à 00:10 UTC
  python main.py dashboard                  régénère le tableau de bord du README
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone

from bot.backtest import buy_and_hold_btc, prepare, run_backtest
from bot.data import load_panel, to_matrix
from bot.metrics import summary, table
from bot.strategy import Config, target_weights


def _mats():
    panel = load_panel(verbose=False)
    return {f: to_matrix(panel, f) for f in ["open", "high", "low", "close", "quote_volume"]}


def cmd_fetch(_):
    load_panel(verbose=True)


def cmd_backtest(a):
    mats, cfg = _mats(), Config()
    res = run_backtest(mats, cfg, a.start, a.end)
    bh = buy_and_hold_btc(mats, a.start, a.end)
    print(table({"Bot": summary(res.equity, res.exposure, res.turnover, len(res.trades)),
                 "BTC buy&hold": summary(bh)}).to_string())


def cmd_signals(_):
    mats, cfg = _mats(), Config()
    sig = prepare(mats, cfg)
    t = mats["close"].index[-1]
    ma = mats["close"]["BTC"].rolling(cfg.regime_ma).mean().iloc[-1]
    print(f"Bougie clôturée : {t.date()}  |  BTC {mats['close']['BTC'].iloc[-1]:,.0f}  MM200 {ma:,.0f}  "
          f"→ {'RISK-ON' if sig.risk_on.iloc[-1] else 'RISK-OFF'}")
    fc = sig.forecast.loc[t][sig.eligible.loc[t]].dropna().sort_values(ascending=False)
    print("Forecasts (univers éligible) :")
    for k, v in fc.items():
        print(f"  {k:8s} {v:+.2f}")
    w = target_weights(sig, t, cfg)
    print("Poids cibles :", {k: round(v, 3) for k, v in w.items()} or "100 % cash")


def cmd_paper(a):
    from bot import paper
    if a.action == "run":
        print(paper.run_once(capital=a.capital, force=a.force))
    elif a.action == "status":
        print(paper.status())
    elif a.action == "reset":
        print(paper.reset(a.capital))
    elif a.action == "loop":
        while True:
            try:
                print(paper.run_once(capital=a.capital))
            except Exception as e:  # on log et on retente au prochain cycle
                print("Erreur :", repr(e))
            now = datetime.now(timezone.utc)
            nxt = (now + timedelta(days=1)).replace(hour=0, minute=10, second=0, microsecond=0)
            if now.hour == 0 and now.minute < 10:
                nxt = now.replace(minute=10, second=0, microsecond=0)
            print(f"Prochaine exécution : {nxt.isoformat()}")
            time.sleep(max(60, (nxt - now).total_seconds()))


def cmd_dashboard(_):
    from bot.dashboard import build
    print(build())


def main():
    p = argparse.ArgumentParser(description="Bot crypto trend-following (paper trading)")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("fetch").set_defaults(fn=cmd_fetch)
    b = sp.add_parser("backtest")
    b.add_argument("--start", default="2018-06-01"); b.add_argument("--end", default=None)
    b.set_defaults(fn=cmd_backtest)
    sp.add_parser("signals").set_defaults(fn=cmd_signals)
    sp.add_parser("dashboard").set_defaults(fn=cmd_dashboard)
    pp = sp.add_parser("paper")
    pp.add_argument("action", choices=["run", "status", "reset", "loop"])
    pp.add_argument("--capital", type=float, default=10_000.0)
    pp.add_argument("--force", action="store_true")
    pp.set_defaults(fn=cmd_paper)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
