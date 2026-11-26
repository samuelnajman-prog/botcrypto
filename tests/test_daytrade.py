"""Tests du bot n°2 (day trading) sur données synthétiques : déclenchement, filtre, clôture, idempotence."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from daytrade import live  # noqa: E402


def fake_market(day, trigger_assets=("BTC",), uptrend=True, upto=None):
    """60 jours de bougies journalières + bougies 5 min de la veille et du jour."""
    mk = {}
    days = pd.date_range(day - pd.Timedelta(days=60), day - pd.Timedelta(days=1), freq="D")
    for i, a in enumerate(live.ASSETS):
        base = 100.0 * (1 + i)
        trend = np.linspace(0.8, 1.0, len(days)) if uptrend else np.linspace(1.2, 1.0, len(days))
        close = base * trend * (1 + 0.01 * np.sin(np.arange(len(days))))
        daily = pd.DataFrame({"open": close * 0.995, "high": close * 1.02, "low": close * 0.98, "close": close}, index=days)
        idx = pd.date_range(day - pd.Timedelta(days=1), day + pd.Timedelta(days=1) - pd.Timedelta(minutes=5), freq="5min")
        op = close[-1]
        px = np.full(len(idx), op)
        rng = daily.high.iloc[-1] - daily.low.iloc[-1]
        today = idx >= day
        if a in trigger_assets:                                   # monte au-delà du niveau à 10:00 UTC
            px[today & (idx >= day + pd.Timedelta(hours=10))] = op + 1.2 * rng
        m5 = pd.DataFrame({"open": px, "high": px * 1.0001, "low": px * 0.9999, "close": px}, index=idx)
        if upto is not None:
            m5 = m5[m5.index + pd.Timedelta(minutes=5) <= upto]
        mk[a] = {"daily": daily, "m5": m5}
    return mk


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "STATE_DIR", tmp_path)
    monkeypatch.setattr(live, "SITE", tmp_path / "site")
    st = live.new_state(10_000.0)
    st["started_at"] = "2026-11-20T00:00:00+00:00"   # démarrage avant les données synthétiques
    live.save_state(st)
    return tmp_path


def test_no_retroactive_fill(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "STATE_DIR", tmp_path)
    monkeypatch.setattr(live, "SITE", tmp_path / "site")
    st = live.new_state(10_000.0)
    st["started_at"] = "2026-11-20T12:00:00+00:00"   # démarré après la cassure de 10:00
    live.save_state(st)
    day = pd.Timestamp("2026-11-20")
    live.run_once(now=day + pd.Timedelta(hours=13), mk=fake_market(day, upto=day + pd.Timedelta(hours=13)))
    assert live.load_state()["orders"]["BTC"]["filled"] is None


def test_trigger_then_close_next_day(state_dir):
    day = pd.Timestamp("2026-11-20")
    now = day + pd.Timedelta(hours=12)
    live.run_once(now=now, mk=fake_market(day, upto=now))
    st = live.load_state()
    assert st["orders"]["BTC"]["filled"] is not None           # BTC a cassé son niveau
    assert st["orders"]["ETH"]["active"] and st["orders"]["ETH"]["filled"] is None
    assert st["cash"] < 10_000
    # passage le lendemain : la position est revendue à la clôture
    nxt = day + pd.Timedelta(days=1, minutes=10)
    mk = fake_market(day + pd.Timedelta(days=1), trigger_assets=())
    for a in mk:   # la veille (jour de l'achat) doit contenir la vraie journée
        mk[a]["m5"] = pd.concat([fake_market(day)[a]["m5"], mk[a]["m5"]]).sort_index()
        mk[a]["m5"] = mk[a]["m5"][~mk[a]["m5"].index.duplicated(keep="first")]
    live.run_once(now=nxt, mk=mk)
    st = live.load_state()
    assert st["day"] == "2026-11-21" and st["stats"]["trades"] == 1
    tr = pd.read_csv(state_dir / "trades.csv")
    assert list(tr.side) == ["BUY", "SELL"]


def test_downtrend_blocks_orders(state_dir):
    day = pd.Timestamp("2026-11-20")
    now = day + pd.Timedelta(hours=12)
    live.run_once(now=now, mk=fake_market(day, trigger_assets=live.ASSETS, uptrend=False, upto=now))
    st = live.load_state()
    assert all(not o["active"] for o in st["orders"].values())
    assert st["cash"] == pytest.approx(10_000)


def test_idempotent_same_hour(state_dir):
    day = pd.Timestamp("2026-11-20")
    now = day + pd.Timedelta(hours=12)
    mk = fake_market(day, upto=now)
    live.run_once(now=now, mk=mk)
    cash1 = live.load_state()["cash"]
    live.run_once(now=now, mk=mk)
    assert live.load_state()["cash"] == pytest.approx(cash1)
    assert len(pd.read_csv(state_dir / "trades.csv")) == 1
