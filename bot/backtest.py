"""Moteur de backtest journalier, réaliste et sans biais d'anticipation.

- Décision à la clôture t (données ≤ t), exécution à l'ouverture t+1.
- Frais + slippage à chaque transaction, seuil de rééquilibrage.
- Actif délisté / série interrompue → liquidé au dernier prix connu.
- Stop suiveur ATR optionnel (évalué à la clôture, exécuté à l'ouverture suivante).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from .execution import generate_orders, scale_buys
from .strategy import Config, Signals, compute_signals, target_weights


@dataclass
class BacktestResult:
    equity: pd.Series
    exposure: pd.Series
    weights: pd.DataFrame
    trades: pd.DataFrame
    turnover: pd.Series
    cfg: Config


def prepare(mats: dict[str, pd.DataFrame], cfg: Config) -> Signals:
    return compute_signals(mats["close"], mats["high"], mats["low"], mats["quote_volume"], cfg,
                           funding=mats.get("funding") if cfg.allow_short else None)


def run_backtest(mats: dict[str, pd.DataFrame], cfg: Config, start: str, end: str | None = None,
                 initial: float = 10_000.0, sig: Signals | None = None,
                 weight_fn: Callable | None = None) -> BacktestResult:
    sig = sig or prepare(mats, cfg)
    close, open_ = mats["close"], mats["open"]
    cols = close.columns
    dates = close.index[(close.index >= pd.Timestamp(start)) & (close.index <= pd.Timestamp(end or close.index[-1]))]

    units = pd.Series(0.0, index=cols)
    last_px = close.loc[:dates[0]].ffill().iloc[-1].reindex(cols)
    cash = initial
    peak = initial
    pending: pd.Series | None = None
    high_since_entry: dict[str, float] = {}
    blocked_until: dict[str, pd.Timestamp] = {}
    cost = cfg.cost_bps / 1e4

    eq_rec, exp_rec, w_rec, to_rec, trades = [], [], [], [], []

    def trade(t, a, d_units, px, reason):
        nonlocal cash
        val = d_units * px
        fee = abs(val) * cost
        cash -= val + fee
        units[a] += d_units
        if abs(units[a]) < 1e-12:
            units[a] = 0.0
        trades.append((t, a, "BUY" if d_units > 0 else "SELL", d_units, px, val, fee, reason))
        return abs(val)

    for t in dates:
        o, c = open_.loc[t], close.loc[t]
        traded = 0.0

        # 1) Séries interrompues : liquidation au dernier prix connu
        for a in units.index[units != 0]:
            if np.isnan(o[a]) and np.isnan(c[a]):
                traded += trade(t, a, -units[a], last_px[a], "delist")
                high_since_entry.pop(a, None)

        # 2) Exécution des ordres décidés la veille, à l'ouverture
        if pending is not None:
            # prix d'exécution = ouverture ; actifs non cotés aujourd'hui exclus
            px = o.where(o.notna() & c.notna(), np.nan)
            orders = generate_orders(units, px, cash, pending, cfg.rebalance_threshold)
            # ventes d'abord
            for a, du in orders.items():
                if du < 0:
                    traded += trade(t, a, du, px[a], "rebal")
                    if units[a] == 0:
                        high_since_entry.pop(a, None)
            buys = {a: du for a, du in orders.items() if du > 0}
            scale = scale_buys(buys, px, cash, cost)
            for a, du in buys.items():
                traded += trade(t, a, du * scale, px[a], "rebal")
                high_since_entry[a] = max(high_since_entry.get(a, 0.0), px[a])

        # 3) Valorisation à la clôture (+ funding des shorts : encaissé si taux > 0)
        last_px = c.where(c.notna(), last_px)
        pos_val = units * last_px.fillna(0)
        if sig.funding is not None and (units < 0).any():
            f = sig.funding.loc[t].fillna(0.0)
            cash += float((-pos_val.clip(upper=0) * f).sum())
        equity = cash + float(pos_val.sum())
        peak = max(peak, equity)
        dd = 1 - equity / peak

        # 4) Stops suiveurs (évalués à la clôture)
        if cfg.trailing_atr_mult > 0:
            for a in list(high_since_entry):
                if units[a] <= 0 or np.isnan(c[a]):
                    continue
                high_since_entry[a] = max(high_since_entry[a], c[a])
                atr = sig.atr.loc[t, a]
                if not np.isnan(atr) and c[a] < high_since_entry[a] - cfg.trailing_atr_mult * atr:
                    blocked_until[a] = t + pd.Timedelta(days=cfg.stop_cooldown_days)
        blocked = {a for a, until in blocked_until.items() if until > t}

        # 5) Décision pour demain
        if weight_fn is not None:
            pending = weight_fn(sig, t, cfg, units, last_px, equity)
        else:
            pending = target_weights(sig, t, cfg, blocked=blocked, equity_dd=dd)

        eq_rec.append(equity)
        exp_rec.append(float(pos_val.abs().sum()) / equity if equity > 0 else 0.0)
        w_rec.append((pos_val / equity).rename(t))
        to_rec.append(traded / equity if equity > 0 else 0.0)

    tr = pd.DataFrame(trades, columns=["date", "asset", "side", "units", "price", "value", "fee", "reason"])
    W = pd.DataFrame(w_rec)
    W = W.loc[:, (W.abs() > 1e-9).any()]
    return BacktestResult(pd.Series(eq_rec, index=dates, name="equity"),
                          pd.Series(exp_rec, index=dates, name="exposure"),
                          W, tr, pd.Series(to_rec, index=dates), cfg)


# --------- Benchmarks -------------------------------------------------------

def buy_and_hold_btc(mats, start, end=None, initial=10_000.0, cost_bps=15.0) -> pd.Series:
    c = mats["close"]["BTC"]
    c = c[(c.index >= pd.Timestamp(start)) & (c.index <= pd.Timestamp(end or c.index[-1]))]
    return initial * (1 - cost_bps / 1e4) * c / c.iloc[0]


def equal_weight_fn(top: int = 10):
    """Benchmark : équipondéré sur le top-N liquide (point-in-time), rééquilibré chaque mois."""
    def fn(sig, t, cfg, units, last_px, equity):
        nxt = t + pd.Timedelta(days=1)
        if nxt.day != 1 and units.sum() > 0:
            return None
        el = sig.eligible.loc[t]
        names = list(el[el].index)[: top]
        if not names:
            return None
        return pd.Series(1.0 / len(names), index=names)
    return fn
