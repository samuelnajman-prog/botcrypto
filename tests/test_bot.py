"""Tests de non-régression et d'intégrité (pas de biais d'anticipation, coûts, contraintes)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.backtest import prepare, run_backtest  # noqa: E402
from bot.execution import generate_orders, scale_buys  # noqa: E402
from bot.strategy import Config, target_weights  # noqa: E402


def synthetic(n_days=700, assets=("BTC", "ETH", "AAA", "BBB"), seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n_days, freq="D")
    close = pd.DataFrame({a: 100 * np.exp(np.cumsum(rng.normal(0.001 * (i - 1), 0.04, n_days)))
                          for i, a in enumerate(assets)}, index=idx)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    qv = pd.DataFrame(50e6, index=idx, columns=close.columns)
    return {"open": open_, "high": high, "low": low, "close": close, "quote_volume": qv}


def test_no_lookahead():
    """Les poids à la date t doivent être identiques avec ou sans les données futures."""
    mats = synthetic()
    cfg = Config(min_history_days=60)
    full = prepare(mats, cfg)
    for t in mats["close"].index[[300, 450, 650]]:
        cut = {k: v.loc[:t] for k, v in mats.items()}
        part = prepare(cut, cfg)
        w_full = target_weights(full, t, cfg)
        w_part = target_weights(part, t, cfg)
        pd.testing.assert_series_equal(w_full.sort_index(), w_part.sort_index(), check_names=False)


def test_weight_constraints():
    mats = synthetic()
    cfg = Config(min_history_days=60)
    sig = prepare(mats, cfg)
    for t in mats["close"].index[300::25]:
        w = target_weights(sig, t, cfg)
        assert (w >= 0).all()
        assert w.sum() <= cfg.max_gross + 1e-9
        for a, v in w.items():
            cap = cfg.max_weight_core if a in cfg.core_assets else cfg.max_weight
            assert v <= cap + 1e-9
        if not sig.risk_on.loc[t]:
            assert w.sum() <= cfg.risk_off_gross + 1e-9
            assert set(w.index) <= set(cfg.core_assets)


def test_costs_round_trip():
    units = pd.Series({"BTC": 0.0}); px = pd.Series({"BTC": 100.0})
    o = generate_orders(units, px, 1000.0, pd.Series({"BTC": 1.0}), 0.05)
    k = scale_buys(o, px, 1000.0, 0.0015)
    assert k < 1.0 and o["BTC"] * k * 100 * 1.0015 == pytest.approx(1000.0)


def test_backtest_runs_and_cash_never_negative():
    mats = synthetic()
    cfg = Config(min_history_days=60)
    res = run_backtest(mats, cfg, "2020-08-01")
    assert len(res.equity) > 100
    assert (res.equity > 0).all()
    assert (res.exposure <= 1.0 + 1e-6).all()


def test_delisted_asset_is_liquidated():
    mats = synthetic()
    for k in mats:
        mats[k].loc["2021-03-01":, "AAA"] = np.nan   # AAA disparaît
    cfg = Config(min_history_days=60, regime_filter=False)
    res = run_backtest(mats, cfg, "2020-08-01")
    assert res.weights.loc["2021-03-02":].get("AAA", pd.Series(0.0)).abs().max() < 1e-9


@pytest.mark.network
def test_paper_buy_then_sell_path(tmp_path, monkeypatch):
    """Exerce achat → vente sur les 6 tranches avec de vrais prix Binance."""
    from bot import paper
    monkeypatch.setattr(paper, "STATE", tmp_path / "s.json")
    monkeypatch.setattr(paper, "TRADES", tmp_path / "t.csv")
    monkeypatch.setattr(paper, "EQUITY", tmp_path / "e.csv")
    monkeypatch.setattr(paper, "STATE_DIR", tmp_path)
    monkeypatch.setattr(paper, "target_weights", lambda *a, **k: pd.Series({"BTC": 0.30, "ETH": 0.20}))
    paper.run_once(capital=10_000)
    st = paper.load_state()
    assert set(st["units"]) == {"BTC", "ETH"} and len(st["tranches"]) == 6
    assert 4_800 < st["cash"] < 5_100            # ~50 % investi, frais déduits
    assert "Rien de nouveau" in paper.run_once()  # idempotent
    monkeypatch.setattr(paper, "target_weights", lambda *a, **k: pd.Series({"BTC": 0.10}))
    paper.run_once(force=True)
    st = paper.load_state()
    assert set(st["units"]) == {"BTC"} and st["cash"] > 8_000
    trades = pd.read_csv(tmp_path / "t.csv")
    assert (trades.side == "BUY").sum() == 12 and (trades.side == "SELL").sum() == 12
    eq = pd.read_csv(tmp_path / "e.csv")
    assert 9_900 < eq.equity.iloc[-1] < 10_100   # seuls frais + spread ont coûté


def test_migration_v1_to_tranches():
    from bot import paper
    v1 = {"initial": 10_000.0, "cash": 7_000.0, "units": {"BTC": 0.06}, "peak": 10_000.0,
          "high_since_entry": {"BTC": 50_000.0}, "blocked_until": {}, "last_run_date": "2026-11-23",
          "started_at": "2026-11-23T22:36:38+00:00", "btc_at_start": 61_000.0}
    st = paper.migrate(v1)
    paper._aggregate(st)
    assert len(st["tranches"]) == 6
    assert st["cash"] == pytest.approx(7_000.0) and st["units"]["BTC"] == pytest.approx(0.06)


def test_cache_never_keeps_open_candle(tmp_path, monkeypatch):
    """Une bougie non clôturée ne doit jamais être mise en cache ni utilisée."""
    from bot import data
    idx = pd.date_range(end=pd.Timestamp.now("UTC").tz_localize(None).normalize(), periods=60, freq="D")
    fake = pd.DataFrame({c: 1.0 for c in ["open", "high", "low", "close", "volume", "quote_volume"]}, index=idx)
    fake.index.name = "date"
    monkeypatch.setattr(data, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data, "fetch_api", lambda s, start=None, interval="1d": fake.copy())
    segs = data.load_symbol("TEST")
    today = idx[-1]
    assert segs["TEST"].index.max() < today                       # pas de bougie du jour
    cached = pd.read_csv(tmp_path / "TESTUSDT.csv", index_col="date", parse_dates=True)
    assert cached.index.max() < today
