"""Tests du bot n°2 actions (données synthétiques, sans réseau)."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from stocks import live, research as R

NY = ZoneInfo("America/New_York")
DAY = date(2026, 11, 24)
PREV = date(2026, 11, 23)


def frame(prev_close, pre_path, reg_path, day=DAY, high_spike=None, last_min=955):
    """Bougies 5 min : veille (clôture prev_close), pré-séance 4:00-9:25 selon pre_path
    {minute: prix}, séance 9:30-last_min selon reg_path (liste de prix d'ouverture)."""
    rows = []

    def add(d, m, o, h, l, c, v=1000):
        ts = datetime.combine(d, datetime.min.time(), NY) + timedelta(minutes=m)
        rows.append((ts.astimezone(timezone.utc), o, h, l, c, v, d, m))

    for m in range(570, 960, 5):
        add(PREV, m, prev_close, prev_close, prev_close, prev_close)
    px = prev_close
    for m in range(240, 570, 5):
        px = pre_path.get(m, px)
        add(day, m, px, px, px, px, 10)
    mins = list(range(570, last_min + 5, 5))
    for k, m in enumerate(mins):
        o = reg_path[min(k, len(reg_path) - 1)]
        c = reg_path[min(k + 1, len(reg_path) - 1)]
        h = max(o, c) if not (high_spike and k == high_spike[0]) else high_spike[1]
        add(day, m, o, h, min(o, c), c)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "day", "mins"]).set_index("ts")
    return df


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(live, "SITE", tmp_path / "stocks.json")
    started = datetime(2026, 11, 23, 22, 0, tzinfo=timezone.utc)  # après la séance du 23
    live.save_state(live.new_state(started))
    return tmp_path


def make_data():
    return {
        "SPY": frame(600, {}, [600, 601, 602]),
        # A : +5 % en pré-séance à 9:20, reflue toute la journée -> gain
        "AAA": frame(100, {480: 104, 560: 105}, list(np.linspace(105, 98, 79))),
        # B : +1 % à 9:20, ne s'envole qu'à 9:25 (après le scan) -> ne doit pas être choisi
        "BBB": frame(50, {480: 50.5, 565: 56}, [56] * 79),
        # C : +8 %, squeeze à +20 % dans la 3e bougie -> stop à +15 %
        "CCC": frame(20, {500: 21.6}, [21.6] * 79, high_spike=(2, 26.0)),
        # D : +10 % sur une annonce de rachat -> exclu par le scan d'actualité
        "DDD": frame(30, {500: 33}, [33] * 79),
    }


def fake_daily(sym, day):
    pc = {"AAA": 100, "BBB": 50, "CCC": 20, "DDD": 30}[sym]
    return pc, 80e6


def fake_news(sym, since, until):
    if sym == "DDD":
        return [{"t": until.isoformat(), "title": "Acme agrees to be acquired for $33 per share in cash",
                 "link": "", "tags": ["rachat"]}]
    return []


def run(now, data=None):
    data = data or make_data()
    fetch = lambda syms: {s: data[s] for s in syms if s in data}  # noqa: E731
    return live.run_once(now=now, fetch=fetch, check_daily=fake_daily, get_news=fake_news,
                         syms=["AAA", "BBB", "CCC", "DDD"])


def test_selection_uses_only_premarket_until_925(env):
    st = run(datetime(2026, 11, 24, 21, 30, tzinfo=timezone.utc))
    sel = st["history"][-1]
    s = live.load_state()["sessions"]["2026-11-24"]
    picked = [r["sym"] for r in s["selection"]["picked"]]
    assert "AAA" in picked and "CCC" in picked
    assert "BBB" not in picked          # le saut de 9:25-9:30 est postérieur au scan
    assert "DDD" not in picked and any(r["sym"] == "DDD" for r in s["selection"]["rejected"])
    assert sel["trades"] == 2


def test_stop_and_close_accounting(env):
    run(datetime(2026, 11, 24, 21, 30, tzinfo=timezone.utc))
    s = live.load_state()["sessions"]["2026-11-24"]
    a, c = s["positions"]["AAA"], s["positions"]["CCC"]
    assert c["status"] == "stop" and c["exit"] == pytest.approx(c["stop"] * (1 + live.CONFIG["slip"]), abs=1e-3)
    assert a["status"] == "clôture" and a["exit"] < a["entry"]
    assert live.pnl(a) > 0 > live.pnl(c)
    # frais : minimum par ordre ou frais par action, deux côtés
    assert a["fees"] == pytest.approx(2 * max(0.35, 0.005 * a["shares"]))
    eq = live.load_state()["equity"]
    assert eq == pytest.approx(25_000 + live.pnl(a) + live.pnl(c), abs=0.02)


def test_idempotent_and_no_double_count(env):
    run(datetime(2026, 11, 24, 21, 30, tzinfo=timezone.utc))
    e1 = live.load_state()["equity"]
    run(datetime(2026, 11, 24, 22, 30, tzinfo=timezone.utc))
    st = live.load_state()
    assert st["equity"] == e1 and len(st["history"]) == 1


def test_no_retroactive_trading(env):
    late = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)      # après 9:25 New York
    live.save_state(live.new_state(late))
    st = run(datetime(2026, 11, 24, 21, 30, tzinfo=timezone.utc))
    assert st["history"] == [] and "2026-11-24" not in st["sessions"]


def test_intraday_then_close(env):
    st = run(datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc))   # 11:00 New York, séance en cours
    s = st["sessions"]["2026-11-24"]
    assert st["history"] == [] and s["positions"]["AAA"]["status"] == "ouverte"
    st = run(datetime(2026, 11, 24, 21, 30, tzinfo=timezone.utc))
    assert len(st["history"]) == 1


def test_half_day_detected():
    spy = frame(600, {}, [600] * 43, last_min=775)                 # dernière bougie 12:55
    assert live.session_done(spy, DAY, datetime(2026, 11, 24, 18, 40, tzinfo=timezone.utc))
    assert not live.session_done(spy, DAY, datetime(2026, 11, 24, 17, 50, tzinfo=timezone.utc))


def test_research_gap_trades_stop_is_exact():
    """Recherche : stop touché => sortie au stop, jamais au prix de clôture."""
    days = [DAY]
    S, T, B = 1, 1, 7
    o = np.full((S, T, B), 10.0, np.float32)
    h = o.copy(); l = o.copy(); c = o.copy(); v = np.ones_like(o)
    h[0, 0, 2] = 12.0
    c[0, 0, -1] = 9.0
    P = R.Panel("1h", days, ["X"], o, h, l, c, v, np.array([[7]]),
                {"prev_close": np.array([[9.5]]), "dvol20": np.array([[1e8]]), "atr14": np.array([[0.5]])})
    g = R.GapParams(min_gap=0.03, stop=0.15)
    tr = R.gap_trades(P, g, slip=0.0)
    assert len(tr) == 1 and tr.why.iloc[0] == "stop" and tr.exit.iloc[0] == pytest.approx(11.5)
