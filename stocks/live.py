"""Bot n°2 — day trading actions US en paper trading : « fade » des gaps haussiers d'attention.

Chaque séance (heure de New York) :
  09:25  scan de pré-séance : actions liquides (> 50 M$ échangés/jour, > 5 $) qui cotent
         +3 % à +60 % au-dessus de la clôture de la veille ; scan d'actualité (Yahoo) qui écarte
         les annonces de rachat ; on garde les 15 plus gros gaps.
  09:30  vente à découvert à l'ouverture officielle (ordre « au marché à l'ouverture »),
         6,7 % du capital par titre (exposition brute 1×), stop de rachat à +15 %.
  16:00  rachat de tout à la clôture (ordre « au marché à la clôture ») : aucune position la nuit.

Tout est rejoué à partir des bougies 5 min (y compris la pré-séance) : le résultat ne dépend pas
de l'heure exacte à laquelle GitHub lance le script, et aucune décision n'utilise une donnée
postérieure à l'heure où elle aurait été prise (sélection figée avec les cours de 9:25).
"""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import data as D
from . import news as N

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "stocks" / "state.json"
UNIVERSE = ROOT / "stocks" / "universe.json"
SITE = ROOT / "docs" / "stocks.json"
NYZ = ZoneInfo(D.NY)

CONFIG = {
    "capital": 25_000.0,      # capital fictif en dollars
    "min_gap": 0.03,          # gap de pré-séance minimal
    "max_gap": 0.60,
    "min_price": 5.0,
    "min_dvol": 50e6,         # volume moyen échangé sur 20 séances, en dollars
    "top_n": 15,
    "weight": 1 / 15,         # part du capital par position (exposition brute 1×)
    "stop_pct": 0.15,         # stop de rachat à +15 % au-dessus du prix de vente
    "cutoff_min": 565,        # 9:25 New York : dernière bougie de pré-séance utilisée
    "per_share": 0.005,       # frais par action et par côté (commission + bourse + régulateur)
    "min_order": 0.35,        # commission minimale par ordre
    "slip": 0.0003,           # glissement par côté
    "news_lookback_h": 20,    # actualité depuis la veille ~13 h
}


# ------------------------------------------------------------------ état

def new_state(now: datetime) -> dict:
    return {"version": 1, "started_at": now.isoformat(timespec="seconds"), "capital": CONFIG["capital"],
            "equity": CONFIG["capital"], "sessions": {}, "history": [], "last_run": None}


def load_state(now: datetime | None = None) -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return new_state(now or datetime.now(timezone.utc))


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, default=str))


# ------------------------------------------------------------------ données

def universe() -> list[str]:
    if UNIVERSE.exists():
        return json.loads(UNIVERSE.read_text())["symbols"]
    return []


def universe_age_days() -> float:
    if not UNIVERSE.exists():
        return 1e9
    t = datetime.fromisoformat(json.loads(UNIVERSE.read_text())["updated"])
    return (datetime.now(timezone.utc) - t).total_seconds() / 86400


def refresh_universe(min_dvol: float = 30e6, min_price: float = 4.0, workers: int = 12) -> dict:
    """Pré-filtre hebdomadaire : toutes les actions ordinaires US, gardées si leur volume
    échangé moyen dépasse `min_dvol` (large marge sous le seuil de 50 M$ vérifié chaque matin
    avec les données de la veille, pour ne pas rater un titre devenu liquide)."""
    syms = list(D.symbol_directory()["symbol"])

    def one(s):
        try:
            d = D.yahoo_chart(s, "1d", "3mo")
            dv = float((d["close"] * d["volume"]).tail(20).mean())
            return s, dv, float(d["close"].iloc[-1])
        except Exception:  # noqa: BLE001
            return s, None, None

    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(one, syms))
    keep = sorted([(s, dv) for s, dv, px in res if dv and px and dv >= min_dvol and px >= min_price],
                  key=lambda x: -x[1])
    out = {"updated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "min_dvol": min_dvol,
           "scanned": len(syms), "symbols": [s for s, _ in keep]}
    UNIVERSE.write_text(json.dumps(out, indent=0))
    return out


def bars(sym: str) -> pd.DataFrame:
    """Bougies 5 min des 5 dernières séances, pré-séance incluse, colonnes + 'day' et 'mins'
    (minutes depuis minuit à New York)."""
    df = D.yahoo_chart(sym, "5m", "5d", prepost=True)
    loc = df.index.tz_convert(NYZ)
    df = df.assign(day=loc.date, mins=loc.hour * 60 + loc.minute)
    return df


def session_parts(df: pd.DataFrame, day: date):
    x = df[df["day"] == day]
    pre = x[(x["mins"] >= 240) & (x["mins"] + 5 <= CONFIG["cutoff_min"])]
    reg = x[(x["mins"] >= 570) & (x["mins"] < 960)]
    prev = df[(df["day"] < day) & (df["mins"] >= 570) & (df["mins"] < 960)]
    prev_close = float(prev["close"].iloc[-1]) if len(prev) else math.nan
    return pre, reg, prev_close


def fetch_many(syms, workers: int = 10) -> dict:
    out = {}

    def one(s):
        try:
            return s, bars(s)
        except Exception:  # noqa: BLE001
            return s, None

    with ThreadPoolExecutor(workers) as ex:
        for s, df in ex.map(one, syms):
            if df is not None and len(df):
                out[s] = df
    return out


def daily_check(sym: str, day: date) -> tuple[float, float]:
    """(clôture officielle de la veille, volume moyen échangé sur les 20 séances avant `day`)."""
    d = D.yahoo_chart(sym, "1d", "3mo")
    d.index = d.index.tz_convert(NYZ).date
    d = d[d.index < day]
    return float(d["close"].iloc[-1]), float((d["close"] * d["volume"]).tail(20).mean())


# ------------------------------------------------------------------ séance

def ny(dt: datetime) -> datetime:
    return dt.astimezone(NYZ)


def session_done(spy: pd.DataFrame, day: date, now: datetime) -> bool:
    """Séance terminée : jour passé, ou après 16:10, ou plus aucune bougie depuis 20 min
    après 13:10 (demi-séances de fin novembre / Noël)."""
    n = ny(now)
    if day < n.date():
        return True
    reg = spy[(spy["day"] == day) & (spy["mins"] >= 570) & (spy["mins"] < 960)]
    if not len(reg):
        return False
    if n.hour * 60 + n.minute >= 970:
        return True
    last = reg.index[-1].to_pydatetime() + timedelta(minutes=5)
    return n.hour * 60 + n.minute >= 790 and (now - last) > timedelta(minutes=20)


def fees(shares: int) -> float:
    return max(CONFIG["min_order"], CONFIG["per_share"] * shares)


def select(day: date, data: dict, now: datetime, check_daily=daily_check, get_news=N.headlines) -> dict:
    """Scan de 9:25 : uniquement des cours de pré-séance <= 9:25 et la clôture de la veille."""
    c = CONFIG
    cands = []
    for s, df in data.items():
        pre, reg, pc = session_parts(df, day)
        if not len(pre) or not math.isfinite(pc) or pc <= c["min_price"]:
            continue
        gap = float(pre["close"].iloc[-1]) / pc - 1
        if c["min_gap"] <= gap < c["max_gap"]:
            cands.append((s, gap, pc, int(pre["volume"].sum())))
    cands.sort(key=lambda x: -x[1])
    picked, rejected = [], []
    cutoff = datetime.combine(day, datetime.min.time(), NYZ) + timedelta(minutes=c["cutoff_min"])
    since = cutoff - timedelta(hours=c["news_lookback_h"])
    for s, gap, pc5, pvol in cands:
        if len(picked) >= c["top_n"]:
            break
        try:
            pc, dv = check_daily(s, day)
        except Exception as e:  # noqa: BLE001
            rejected.append({"sym": s, "gap": gap, "why": f"données journalières indisponibles ({str(e)[:40]})"})
            continue
        gap = float(session_parts(data[s], day)[0]["close"].iloc[-1]) / pc - 1
        if dv < c["min_dvol"]:
            rejected.append({"sym": s, "gap": gap, "why": f"liquidité {dv / 1e6:.0f} M$/j < 50 M$"})
            continue
        if not (c["min_gap"] <= gap < c["max_gap"]) or pc <= c["min_price"]:
            rejected.append({"sym": s, "gap": gap, "why": "gap hors bornes avec la clôture officielle"})
            continue
        items = get_news(s, since.astimezone(timezone.utc), cutoff.astimezone(timezone.utc))
        excl, themes = N.verdict(items)
        row = {"sym": s, "gap": round(gap, 4), "prev_close": pc, "dvol20": round(dv), "pre_volume": pvol,
               "themes": themes, "news": items[:3]}
        if excl:
            rejected.append({**row, "why": "annonce de rachat / fusion : pas de reflux attendu"})
            continue
        picked.append(row)
    return {"scanned": len(data), "candidates": len(cands), "picked": picked, "rejected": rejected[:25],
            "selected_at": now.isoformat(timespec="seconds")}


def open_positions(sess: dict, day: date, data: dict, equity: float) -> None:
    c = CONFIG
    for row in sess["selection"]["picked"]:
        s = row["sym"]
        if s in sess["positions"] or s not in data:
            continue
        _, reg, _ = session_parts(data[s], day)
        if not len(reg) or reg["mins"].iloc[0] != 570:
            sess["skipped"].append({"sym": s, "why": "pas de cotation à l'ouverture (suspension ?)"})
            continue
        open_px = float(reg["open"].iloc[0])
        entry = open_px * (1 - c["slip"])
        sh = int(equity * c["weight"] // open_px)
        if sh <= 0:
            continue
        sess["positions"][s] = {"shares": sh, "open": open_px, "entry": round(entry, 4),
                                "stop": round(entry * (1 + c["stop_pct"]), 4), "gap": row["gap"],
                                "status": "ouverte", "exit": None, "exit_time": None, "last": open_px,
                                "fees": round(fees(sh), 2)}


def update_positions(sess: dict, day: date, data: dict, done: bool) -> None:
    c = CONFIG
    for s, p in sess["positions"].items():
        if p["status"] != "ouverte" or s not in data:
            continue
        _, reg, _ = session_parts(data[s], day)
        if not len(reg):
            continue
        for ts, b in reg.iterrows():
            if b["high"] >= p["stop"]:
                px = max(p["stop"], b["open"]) if b["mins"] > 570 else p["stop"]
                p.update(status="stop", exit=round(px * (1 + c["slip"]), 4),
                         exit_time=ts.tz_convert(NYZ).strftime("%H:%M"))
                break
        p["last"] = float(reg["close"].iloc[-1])
        if p["status"] == "ouverte" and done:
            p.update(status="clôture", exit=round(p["last"] * (1 + c["slip"]), 4),
                     exit_time=reg.index[-1].tz_convert(NYZ).strftime("%H:%M"))
        if p["exit"] is not None:
            p["fees"] = round(2 * fees(p["shares"]), 2)


def pnl(p: dict) -> float:
    px = p["exit"] if p["exit"] is not None else p["last"]
    return p["shares"] * (p["entry"] - px) - p["fees"]


def run_once(now: datetime | None = None, fetch=fetch_many, check_daily=daily_check,
             get_news=N.headlines, syms: list | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    st = load_state(now)
    started = datetime.fromisoformat(st["started_at"])
    syms = syms if syms is not None else universe()
    spy = fetch(["SPY"]).get("SPY")
    if spy is None:
        raise RuntimeError("SPY indisponible : rien à faire")
    days = sorted({d for d, m in zip(spy["day"], spy["mins"]) if 570 <= m < 960})
    for day in days:
        key = day.isoformat()
        cutoff = datetime.combine(day, datetime.min.time(), NYZ) + timedelta(minutes=CONFIG["cutoff_min"])
        if cutoff <= started or key in {h["day"] for h in st["history"]}:
            continue  # pas de trading rétroactif ; séance déjà soldée
        n = ny(now)
        if day == n.date() and n.hour * 60 + n.minute < 575:
            continue  # on attend la première bougie de 9:30
        done = session_done(spy, day, now)
        sess = st["sessions"].setdefault(key, {"selection": None, "positions": {}, "skipped": []})
        if sess["selection"] is None:
            data = fetch(syms)
            sess["selection"] = select(day, data, now, check_daily, get_news)
            sess["selection"]["fetched"] = len(data)
            open_positions(sess, day, data, st["equity"])
        held = [s for s, p in sess["positions"].items() if p["status"] == "ouverte"]
        if held:
            update_positions(sess, day, fetch(held), done)
        if done:
            total = sum(pnl(p) for p in sess["positions"].values())
            eq0 = st["equity"]
            st["equity"] = round(eq0 + total, 2)
            st["history"].append({"day": key, "trades": len(sess["positions"]), "pnl": round(total, 2),
                                  "ret": round(total / eq0, 5), "equity": st["equity"],
                                  "stops": sum(p["status"] == "stop" for p in sess["positions"].values())})
            sess["closed"] = True
    # on ne garde le détail que des 30 dernières séances
    for k in sorted(st["sessions"])[:-30]:
        st["sessions"].pop(k)
    st["last_run"] = now.isoformat(timespec="seconds")
    save_state(st)
    export_site(st, now)
    return st


# ------------------------------------------------------------------ page

def export_site(st: dict, now: datetime) -> None:
    live = None
    if st["sessions"]:
        k = sorted(st["sessions"])[-1]
        s = st["sessions"][k]
        pos = [{"sym": sym, **p, "pnl": round(pnl(p), 2),
                "ret": round((p["entry"] - (p["exit"] or p["last"])) / p["entry"], 5)}
               for sym, p in s["positions"].items()]
        live = {"day": k, "closed": bool(s.get("closed")), "positions": pos, "skipped": s.get("skipped", []),
                "selection": s["selection"],
                "pnl": round(sum(p["pnl"] for p in pos), 2)}
    unreal = 0.0 if not live or live["closed"] else live["pnl"]
    bt = {}
    p = ROOT / "docs" / "stocks_backtest.json"
    if p.exists():
        b = json.loads(p.read_text())
        bt = {k: b.get(k) for k in ("samples", "years", "years_10y", "costs", "bootstrap_6m", "curve")}
    out = {"updated": now.isoformat(timespec="seconds"), "started_at": st["started_at"], "config": CONFIG,
           "capital": st["capital"], "equity": st["equity"], "equity_live": round(st["equity"] + unreal, 2),
           "history": st["history"][-250:], "today": live, "backtest": bt}
    SITE.write_text(json.dumps(out, indent=0, default=str))


def status() -> str:
    st = load_state()
    r = st["equity"] / st["capital"] - 1
    lines = [f"Bot actions — capital {st['equity']:.2f} $ ({r:+.2%}) depuis {st['started_at'][:10]}",
             f"séances soldées : {len(st['history'])}, dernier passage {st['last_run']}"]
    for h in st["history"][-5:]:
        lines.append(f"  {h['day']}  {h['trades']:2d} trades  {h['ret']:+.2%}  ({h['stops']} stops)")
    return "\n".join(lines)
