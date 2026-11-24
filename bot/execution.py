"""Logique d'ordres partagée par le backtest et le paper trading (même code = mêmes décisions)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def generate_orders(units: pd.Series, px: pd.Series, cash: float, target: pd.Series,
                    threshold: float) -> dict[str, float]:
    """Retourne {actif: variation d'unités}. Négatif = vente.

    - Sortie complète si le poids cible est 0 et qu'on détient l'actif.
    - Sinon on ne trade que si l'écart de poids dépasse `threshold` (bande de non-transaction),
      ou pour une entrée suffisamment grande (> threshold/2).
    """
    equity = cash + float((units * px.fillna(0)).sum())
    if equity <= 0:
        return {}
    tgt = target.reindex(units.index).fillna(0.0)
    cur_w = (units * px.fillna(0)) / equity
    orders: dict[str, float] = {}
    for a in units.index:
        p = px.get(a, np.nan)
        if p is None or np.isnan(p) or p <= 0:
            continue
        wt, wc = float(tgt[a]), float(cur_w[a])
        if wt == 0 and units[a] != 0:
            orders[a] = -float(units[a])
        elif abs(wt - wc) > threshold or (wt != 0 and wc == 0 and abs(wt) > threshold / 2):
            orders[a] = (wt * equity - units[a] * p) / p
    return orders


def scale_buys(orders: dict[str, float], buy_px: pd.Series, cash_after_sells: float,
               cost: float) -> float:
    """Facteur ≤ 1 appliqué aux achats pour ne jamais avoir de cash négatif."""
    need = sum(du * buy_px[a] * (1 + cost) for a, du in orders.items() if du > 0)
    return min(1.0, cash_after_sells / need) if need > 0 else 1.0
