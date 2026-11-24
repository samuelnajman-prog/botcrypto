"""Stratégie : trend-following multi-horizons, long/cash, vol-targeting, filtre de régime.

Principe (étayé par la littérature : time-series momentum robuste en crypto,
cross-sectional momentum faible, volatility management utile contre les krachs) :

1. Univers point-in-time : les N cryptos les plus liquides à la date t
   (volume médian 30 j), avec un historique minimal. Pas de choix a posteriori.
2. Signal : moyenne de 3 croisements d'EMA normalisés par la volatilité
   (8/32, 16/64, 32/128 jours) → "forecast" continu, plafonné.
3. Long uniquement si forecast > 0 (sinon cash/stablecoin). Pas de short, pas de levier.
4. Sélection : les K forecasts les plus forts.
5. Sizing : poids ∝ forecast / volatilité, puis mise à l'échelle pour viser une
   volatilité annuelle de portefeuille cible (covariance glissante), plafonds par actif.
6. Régime : si BTC < MM200, exposition plafonnée et réservée au "core" (BTC/ETH).
7. Frein de drawdown optionnel et stop suiveur ATR optionnel (testés, voir rapport).

Tout est causal : chaque valeur à la date t n'utilise que des données ≤ t.
Les ordres décidés à la clôture t sont exécutés à l'ouverture t+1.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

# Scalaires de forecast d'EWMAC (R. Carver, "Systematic Trading") ramenés à |f|≈1
EWMAC_SCALARS = {(8, 32): 5.3, (16, 64): 3.75, (32, 128): 2.65, (64, 256): 1.87}


@dataclass
class Config:
    # Univers
    top_n_liquid: int = 20
    min_history_days: int = 180
    min_median_qvol: float = 10e6        # USDT/jour
    liq_window: int = 30
    # Signaux
    ewmac_pairs: tuple = ((8, 32), (16, 64), (32, 128))
    forecast_cap: float = 2.0
    vol_span: int = 30
    # Sélection
    max_positions: int = 10
    # Sizing
    target_vol: float = 0.50             # vol annuelle visée (ex ante, avant filtres)
    max_gross: float = 1.0               # pas de levier
    max_weight: float = 0.30
    max_weight_core: float = 0.60
    core_assets: tuple = ("BTC", "ETH")
    cov_window: int = 90
    # Régime de marché
    regime_filter: bool = True
    regime_ma: int = 200
    risk_off_gross: float = 0.35
    risk_off_core_only: bool = True
    # Gestion du risque
    trailing_atr_mult: float = 0.0       # 0 = désactivé
    atr_window: int = 20
    stop_cooldown_days: int = 10
    dd_brake_level: float = 0.0          # 0 = désactivé ; ex. 0.25
    dd_brake_scale: float = 0.5
    # Exécution
    rebalance_threshold: float = 0.05    # n'ajuste que si l'écart de poids > 5 pts
    cost_bps: float = 15.0               # par côté : 10 frais + 5 slippage

    def to_dict(self):
        return asdict(self)


@dataclass
class Signals:
    close: pd.DataFrame
    forecast: pd.DataFrame       # forecast combiné (peut être < 0)
    vol: pd.DataFrame            # vol annualisée
    eligible: pd.DataFrame       # bool, univers point-in-time
    risk_on: pd.Series           # bool
    atr: pd.DataFrame
    returns: pd.DataFrame


def compute_signals(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame,
                    qvol: pd.DataFrame, cfg: Config) -> Signals:
    rets = close.pct_change(fill_method=None)
    sigma_d = rets.ewm(span=cfg.vol_span, min_periods=cfg.vol_span).std()
    vol_ann = sigma_d * np.sqrt(365)

    fcs = []
    for f, s in cfg.ewmac_pairs:
        raw = (close.ewm(span=f, adjust=False).mean() - close.ewm(span=s, adjust=False).mean())
        raw = raw / (close * sigma_d)
        fc = (raw * EWMAC_SCALARS[(f, s)] / 10).clip(-cfg.forecast_cap, cfg.forecast_cap)
        # pas de signal tant que l'EMA lente n'a pas assez d'historique
        age = close.notna().cumsum()
        fcs.append(fc.where(age >= s))
    forecast = sum(fcs) / len(fcs)
    forecast = forecast.clip(-cfg.forecast_cap, cfg.forecast_cap)

    # Univers point-in-time par liquidité
    age = close.notna().cumsum()
    med_qv = qvol.rolling(cfg.liq_window, min_periods=cfg.liq_window).median()
    base_ok = (age >= cfg.min_history_days) & (med_qv >= cfg.min_median_qvol) & close.notna()
    rank = med_qv.where(base_ok).rank(axis=1, ascending=False)
    eligible = base_ok & (rank <= cfg.top_n_liquid)

    btc = close["BTC"]
    risk_on = (btc > btc.rolling(cfg.regime_ma, min_periods=cfg.regime_ma).mean())
    if not cfg.regime_filter:
        risk_on[:] = True

    prev_close = close.shift(1)
    tr = pd.concat({"a": high - low, "b": (high - prev_close).abs(), "c": (low - prev_close).abs()})
    tr = tr.groupby(level=1).max().reindex(close.index)[close.columns]
    atr = tr.rolling(cfg.atr_window, min_periods=cfg.atr_window).mean()

    return Signals(close, forecast, vol_ann, eligible, risk_on, atr, rets)


def _cap_weights(w: pd.Series, caps: pd.Series, max_gross: float) -> pd.Series:
    """Applique plafonds individuels + plafond brut sans redistribuer au-delà (le reste en cash)."""
    w = w.clip(upper=caps)
    g = w.sum()
    if g > max_gross:
        w = w * (max_gross / g)
    return w


def target_weights(sig: Signals, t: pd.Timestamp, cfg: Config,
                   blocked: set | None = None, equity_dd: float = 0.0) -> pd.Series:
    """Poids cibles à la clôture de t (fraction de l'équité). Somme ≤ 1, reste = cash."""
    fc = sig.forecast.loc[t]
    el = sig.eligible.loc[t]
    risk_on = bool(sig.risk_on.loc[t]) if not pd.isna(sig.risk_on.loc[t]) else False

    cand = fc[el & (fc > 0)].dropna()
    if blocked:
        cand = cand.drop([a for a in blocked if a in cand.index])
    max_gross = cfg.max_gross
    if not risk_on:
        max_gross = cfg.risk_off_gross
        if cfg.risk_off_core_only:
            cand = cand[[a for a in cand.index if a in cfg.core_assets]]
    if cand.empty:
        return pd.Series(dtype=float)

    cand = cand.sort_values(ascending=False).head(cfg.max_positions)
    vol = sig.vol.loc[t, cand.index].replace(0, np.nan).dropna()
    cand = cand[vol.index]
    if cand.empty:
        return pd.Series(dtype=float)

    raw = cand / vol
    w = raw / raw.sum()

    # Vol de portefeuille estimée sur la covariance glissante
    hist = sig.returns.loc[:t, w.index].tail(cfg.cov_window)
    cov = hist.cov(min_periods=30) * 365
    cov = cov.fillna(0.0)
    for a in w.index:  # diagonale de secours si historique court
        if cov.loc[a, a] == 0:
            cov.loc[a, a] = vol[a] ** 2
    port_vol = float(np.sqrt(max(w.values @ cov.values @ w.values, 1e-12)))

    strength = float(min(1.0, (cand * w).sum()))   # tendances faibles → exposition réduite
    gross = min(max_gross, cfg.target_vol / port_vol * strength)
    if cfg.dd_brake_level > 0 and equity_dd >= cfg.dd_brake_level:
        gross *= cfg.dd_brake_scale
    w = w * gross

    caps = pd.Series(cfg.max_weight, index=w.index)
    for a in cfg.core_assets:
        if a in caps.index:
            caps[a] = cfg.max_weight_core
    return _cap_weights(w, caps, max_gross)
