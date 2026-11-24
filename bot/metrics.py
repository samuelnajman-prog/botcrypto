"""Indicateurs de performance, dont la distribution des rendements sur 6 mois glissants
(l'horizon visé par le projet)."""
from __future__ import annotations

import numpy as np
import pandas as pd

DAYS = 365


def drawdown(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1


def summary(equity: pd.Series, exposure: pd.Series | None = None,
            turnover: pd.Series | None = None, n_trades: int | None = None) -> dict:
    r = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / DAYS
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    vol = r.std() * np.sqrt(DAYS)
    sharpe = r.mean() / r.std() * np.sqrt(DAYS) if r.std() > 0 else np.nan
    downside = r[r < 0].std() * np.sqrt(DAYS)
    sortino = r.mean() * DAYS / downside if downside > 0 else np.nan
    mdd = drawdown(equity).min()
    roll = equity.pct_change(182).dropna()
    out = {
        "Rendement total": equity.iloc[-1] / equity.iloc[0] - 1,
        "CAGR": cagr,
        "Volatilité": vol,
        "Sharpe": sharpe,
        "Sortino": sortino,
        "Max drawdown": mdd,
        "Calmar": cagr / abs(mdd) if mdd < 0 else np.nan,
        "6 mois - médiane": roll.median() if len(roll) else np.nan,
        "6 mois - pire 5 %": roll.quantile(0.05) if len(roll) else np.nan,
        "6 mois - meilleur 5 %": roll.quantile(0.95) if len(roll) else np.nan,
        "6 mois - % positifs": (roll > 0).mean() if len(roll) else np.nan,
    }
    if exposure is not None:
        out["Exposition moyenne"] = exposure.mean()
    if turnover is not None:
        out["Turnover annuel"] = turnover.sum() / years if years > 0 else np.nan
    if n_trades is not None:
        out["Nb transactions"] = n_trades
    return out


PCT_KEYS = {"Rendement total", "CAGR", "Volatilité", "Max drawdown", "6 mois - médiane",
            "6 mois - pire 5 %", "6 mois - meilleur 5 %", "6 mois - % positifs", "Exposition moyenne"}


def fmt(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if v is None or (isinstance(v, float) and np.isnan(v)):
            out[k] = "n/a"
        elif k in PCT_KEYS:
            out[k] = f"{v:+.1%}" if k not in {"Volatilité", "6 mois - % positifs", "Exposition moyenne"} else f"{v:.0%}"
        elif k == "Turnover annuel":
            out[k] = f"{v:.1f}x"
        elif k == "Nb transactions":
            out[k] = f"{int(v)}"
        else:
            out[k] = f"{v:.2f}"
    return out


def table(results: dict[str, dict]) -> pd.DataFrame:
    return pd.DataFrame({k: fmt(v) for k, v in results.items()})
