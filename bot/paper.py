"""Paper trading : même stratégie, même logique d'ordres que le backtest, sur les prix réels
de Binance (carnet bid/ask), sans argent réel et sans clé API.

Le capital est découpé en 6 TRANCHES égales. Chaque tranche applique la stratégie journalière
sur sa propre « journée », qui se termine à 00, 04, 08, 12, 16 ou 20 h UTC. Il y a donc une
décision toutes les 4 heures, chacune portant sur 1/6 du capital : on ne dépend plus de la
chance d'une heure de décision particulière (voir scripts/timing.py et scripts/tranches.py).

À lancer toutes les 4 h, quelques minutes après 00/04/08/12/16/20 h UTC. Idempotent : une tranche
ne traite chaque journée qu'une fois.

Fichiers d'état (dossier state/) :
  paper_state.json   tranches (cash, positions, stops), totaux, pic d'équité
  paper_trades.csv   journal des ordres simulés (la colonne reason indique la tranche)
  paper_equity.csv   équité après chaque exécution + BTC pour comparaison
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import prepare
from .data import API, CANDIDATES, _get, daily_from_intraday, load_panel, to_matrix
from .execution import generate_orders, scale_buys
from .strategy import Config, target_weights

STATE_DIR = Path(os.environ.get("BOT_STATE_DIR", Path(__file__).resolve().parent.parent / "state"))
STATE = STATE_DIR / "paper_state.json"
TRADES = STATE_DIR / "paper_trades.csv"
EQUITY = STATE_DIR / "paper_equity.csv"
FEE = 0.0010         # frais spot taker standard
EXTRA_SLIP = 0.0005  # slippage additionnel au-delà du bid/ask
TRANCHE_HOURS = (0, 4, 8, 12, 16, 20)   # heure UTC de fin de « journée » de chaque tranche
HALF_SPREAD = 0.0002                     # demi-écart bid/ask appliqué au prix d'exécution historique


def symbol_of(asset: str) -> str:
    return asset.split("#")[0] + "USDT"


def _new_tranche(cash: float) -> dict:
    return {"cash": cash, "units": {}, "peak": cash, "high_since_entry": {}, "blocked_until": {}, "last_bar": None}


def new_state(capital: float) -> dict:
    n = len(TRANCHE_HOURS)
    return {"version": 2, "initial": capital, "peak": capital,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "btc_at_start": None,
            "tranches": {str(h): _new_tranche(capital / n) for h in TRANCHE_HOURS},
            "cash": capital, "units": {}, "last_run_date": None}


def migrate(st: dict) -> dict:
    """État v1 (un seul portefeuille) → v2 (6 tranches égales, positions réparties)."""
    if "tranches" in st:
        return st
    n = len(TRANCHE_HOURS)
    st["tranches"] = {}
    for h in TRANCHE_HOURS:
        tr = _new_tranche(st["cash"] / n)
        tr["units"] = {a: u / n for a, u in st.get("units", {}).items()}
        tr["peak"] = st.get("peak", st["initial"]) / n
        tr["high_since_entry"] = dict(st.get("high_since_entry", {}))
        st["tranches"][str(h)] = tr
    for k in ("high_since_entry", "blocked_until"):
        st.pop(k, None)
    st["version"] = 2
    return st


def _aggregate(st: dict) -> None:
    st["cash"] = sum(tr["cash"] for tr in st["tranches"].values())
    units: dict[str, float] = {}
    for tr in st["tranches"].values():
        for a, u in tr["units"].items():
            units[a] = units.get(a, 0.0) + u
    st["units"] = {a: u for a, u in units.items() if u > 0}


def load_state() -> dict | None:
    return json.loads(STATE.read_text()) if STATE.exists() else None


def save_state(st: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1))


def book(symbols: list[str]) -> dict[str, tuple[float, float]]:
    """Meilleurs bid/ask actuels."""
    if not symbols:
        return {}
    syms = "[" + ",".join(f'"{s}"' for s in sorted(set(symbols))) + "]"
    r = _get(f"{API}/ticker/bookTicker", {"symbols": syms})
    if r.status_code != 200:   # un symbole invalide fait échouer le lot : repli un par un
        out = {}
        for s in set(symbols):
            r1 = _get(f"{API}/ticker/bookTicker", {"symbol": s})
            if r1.status_code == 200:
                j = r1.json(); out[s] = (float(j["bidPrice"]), float(j["askPrice"]))
        return out
    return {j["symbol"]: (float(j["bidPrice"]), float(j["askPrice"])) for j in r.json()}


def exec_prices(assets, ts: pd.Timestamp, fallback_mid: pd.Series) -> pd.Series:
    """Prix d'exécution d'une décision : ouverture de la bougie 5 min qui commence à `ts`
    (le moment prévu de la décision), quelle que soit l'heure réelle à laquelle le bot tourne.
    Repli sur le prix actuel si la bougie n'existe pas encore."""
    out = {}
    ms = int(pd.Timestamp(ts).timestamp() * 1000)
    for a in assets:
        try:
            r = _get(f"{API}/klines", {"symbol": symbol_of(a), "interval": "5m", "startTime": ms, "limit": 1})
            k = r.json() if r.status_code == 200 else []
            out[a] = float(k[0][1]) if k and int(k[0][0]) == ms else float(fallback_mid.get(a, np.nan))
        except Exception:
            out[a] = float(fallback_mid.get(a, np.nan))
    return pd.Series(out, dtype=float)


def notify(text: str) -> None:
    """Notification Telegram optionnelle (variables TELEGRAM_TOKEN et TELEGRAM_CHAT_ID)."""
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=15)
    except Exception as e:  # la notification ne doit jamais faire planter le bot
        print("Notification échouée:", e)


def _append_csv(path: Path, header: list[str], rows: list[list]) -> None:
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


def _run_tranche(h: int, tr: dict, m: dict, t: pd.Timestamp, cfg: Config,
                 live_mid: pd.Series, now: str) -> tuple[list, object, pd.Series]:
    """Une décision journalière pour une tranche, exécutée au prix du moment prévu (fin de sa journée).
    Retourne (ordres, signaux, poids cibles)."""
    sig = prepare(m, cfg)
    cols = m["close"].columns
    close_t = m["close"].loc[t]
    units = pd.Series({a: float(tr["units"].get(a, 0.0)) for a in cols})
    t_exec = t + pd.Timedelta(hours=24)
    bar = str(t_exec)[:16]   # fin de la journée de la tranche
    held = [a for a in cols if units[a] > 0]
    mid = exec_prices(held, t_exec, live_mid).reindex(cols)
    tag = f"T{h:02d}"
    rows = []
    last_close = m["close"].ffill().iloc[-1]
    # Actif détenu mais plus coté : liquidé au dernier prix connu
    for a in units.index[units > 0]:
        if np.isnan(close_t[a]) or np.isnan(mid.get(a, np.nan)):
            px = float(last_close[a]) if np.isnan(mid.get(a, np.nan)) else float(mid[a])
            val = units[a] * px
            tr["cash"] += val * (1 - FEE)
            rows.append([now, bar, a, "SELL", -units[a], px, val, val * FEE, f"delist {tag}"])
            units[a] = 0.0
            tr["high_since_entry"].pop(a, None)

    px_mid = mid.reindex(cols)
    equity = tr["cash"] + float((units * px_mid.fillna(0)).sum())
    tr["peak"] = max(tr.get("peak", equity), equity)
    dd = 1 - equity / tr["peak"] if tr["peak"] > 0 else 0.0

    if cfg.trailing_atr_mult > 0:
        for a, hh in list(tr["high_since_entry"].items()):
            c = close_t.get(a, np.nan)
            if np.isnan(c):
                continue
            hh = max(hh, c); tr["high_since_entry"][a] = hh
            atr = sig.atr.loc[t, a]
            if units.get(a, 0) > 0 and not np.isnan(atr) and c < hh - cfg.trailing_atr_mult * atr:
                tr["blocked_until"][a] = str((t + pd.Timedelta(days=cfg.stop_cooldown_days)).date())
    blocked = {a for a, d in tr["blocked_until"].items() if pd.Timestamp(d) > t}

    tgt = target_weights(sig, t, cfg, blocked=blocked, equity_dd=dd)
    new = [a for a in tgt.index if a not in held]
    if new:
        mid.update(exec_prices(new, t_exec, live_mid))
        px_mid = mid.reindex(cols)
    bid, ask = mid * (1 - HALF_SPREAD), mid * (1 + HALF_SPREAD)
    orders = generate_orders(units, px_mid, tr["cash"], tgt, cfg.rebalance_threshold)

    for a, du in orders.items():          # ventes au bid
        if du < 0:
            px = bid[a] * (1 - EXTRA_SLIP)
            val = -du * px
            tr["cash"] += val * (1 - FEE)
            units[a] += du
            if units[a] < 1e-12:
                units[a] = 0.0; tr["high_since_entry"].pop(a, None)
            rows.append([now, bar, a, "SELL", du, px, val, val * FEE, f"rebal {tag}"])
    buy_px = ask.reindex(cols) * (1 + EXTRA_SLIP)
    buys = {a: du for a, du in orders.items() if du > 0}
    k = scale_buys(buys, buy_px, tr["cash"], FEE)
    for a, du in buys.items():            # achats à l'ask
        du *= k
        val = du * buy_px[a]
        tr["cash"] -= val * (1 + FEE)
        units[a] += du
        tr["high_since_entry"][a] = max(tr["high_since_entry"].get(a, 0.0), float(close_t[a]))
        rows.append([now, bar, a, "BUY", du, buy_px[a], val, val * FEE, f"rebal {tag}"])

    tr["units"] = {a: float(u) for a, u in units.items() if u > 0}
    tr["last_bar"] = str(t)
    return rows, sig, tgt


def run_once(cfg: Config | None = None, capital: float = 10_000.0, force: bool = False) -> str:
    cfg = cfg or Config()
    st = migrate(load_state() or new_state(capital))

    panel = load_panel(CANDIDATES, verbose=False, interval="4h")   # bougies 4h clôturées uniquement
    m4 = {f: to_matrix(panel, f) for f in ["open", "high", "low", "close", "quote_volume"]}
    todo = []
    for h in TRANCHE_HOURS:
        m = daily_from_intraday(m4, h)
        t = m["close"].index[-1]
        if force or st["tranches"][str(h)]["last_bar"] != str(t):
            todo.append((h, m, t))
    if not todo:
        return "Rien de nouveau : chaque tranche a déjà traité sa dernière journée clôturée."

    cols = m4["close"].columns
    held = {a for tr in st["tranches"].values() for a in tr["units"]}
    live = {a for a in cols if not np.isnan(m4["close"][a].iloc[-1])}
    ba = book([symbol_of(a) for a in held | live])
    bid = pd.Series({a: ba[symbol_of(a)][0] for a in cols if symbol_of(a) in ba}).reindex(cols)
    ask = pd.Series({a: ba[symbol_of(a)][1] for a in cols if symbol_of(a) in ba}).reindex(cols)
    mid = (bid + ask) / 2

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows, done = [], []
    sig = t_last = tgt = None
    for h, m, t in todo:
        r, sig, tgt = _run_tranche(h, st["tranches"][str(h)], m, t, cfg, mid, now)
        rows += r
        done.append(h)
        if t_last is None or t > t_last:
            sig_last, tgt_last, t_last = sig, tgt, t
    sig = sig_last

    _aggregate(st)
    btc = float(mid.get("BTC", np.nan))
    if st["btc_at_start"] is None:
        st["btc_at_start"] = btc
    mid_f = mid.fillna(0.0)
    equity = st["cash"] + float(sum(u * mid_f.get(a, 0.0) for a, u in st["units"].items()))
    st["peak"] = max(st["peak"], equity)
    bar_end = t_last + pd.Timedelta(hours=24)
    st["last_run_date"] = str(bar_end)[:16]
    save_state(st)
    _append_csv(TRADES, ["ts", "bar_date", "asset", "side", "units", "price", "value", "fee", "reason"], rows)
    _append_csv(EQUITY, ["ts", "bar_date", "equity", "cash", "exposure", "btc_price"],
                [[now, str(bar_end)[:16], round(equity, 2), round(st["cash"], 2),
                  round(1 - st["cash"] / equity, 4), btc]])

    report = format_report(st, sig, t_last, cfg, mid, equity, rows, done)
    notify(report)
    return report


def format_report(st, sig, t, cfg, mid, equity, rows, done) -> str:
    risk_on = bool(sig.risk_on.loc[t])
    btc_ma = sig.close["BTC"].rolling(cfg.regime_ma).mean().loc[t]
    perf = equity / st["initial"] - 1
    btc_perf = (mid["BTC"] / st["btc_at_start"] - 1) if st["btc_at_start"] else 0.0
    tranches = ", ".join(f"{(h + 1) % 24:02d}h Paris" for h in done)
    lines = [f"🤖 Bot crypto — paper trading — tranche(s) traitée(s) : {tranches}",
             f"Équité : {equity:,.0f} USDT ({perf:+.1%} depuis le départ ; BTC {btc_perf:+.1%})",
             f"Drawdown : {1 - equity / st['peak']:.1%}  |  Cash : {st['cash'] / equity:.0%}",
             f"Régime : {'RISK-ON' if risk_on else 'RISK-OFF'} (BTC {sig.close['BTC'].loc[t]:,.0f} vs MM200 {btc_ma:,.0f})"]
    if st["units"]:
        lines.append("Positions : " + ", ".join(
            f"{a} {st['units'][a] * mid[a] / equity:.0%}" for a in sorted(st["units"], key=lambda x: -st["units"][x] * mid[x])))
    else:
        lines.append("Positions : aucune (100 % stablecoin)")
    lines.append("Ordres : " + (", ".join(f"{r[3]} {r[2]} {abs(r[6]):,.0f}$ ({r[8].split()[-1]})" for r in rows) if rows else "aucun"))
    fc = sig.forecast.loc[t][sig.eligible.loc[t]].dropna().sort_values(ascending=False)
    lines.append("Tendances les plus fortes : " + ", ".join(f"{a} {v:+.2f}" for a, v in fc.head(5).items()))
    return "\n".join(lines)


def status() -> str:
    st = load_state()
    if not st:
        return "Aucun portefeuille paper. Lance : python main.py paper run"
    st = migrate(st); _aggregate(st)
    ba = book([symbol_of(a) for a in st["units"]] + ["BTCUSDT"])
    val = sum(u * sum(ba[symbol_of(a)]) / 2 for a, u in st["units"].items() if symbol_of(a) in ba)
    eq = st["cash"] + val
    btc = sum(ba["BTCUSDT"]) / 2
    txt = [f"Départ : {st['started_at']}  |  dernière journée traitée : {st['last_run_date']}",
           f"Équité live : {eq:,.2f} USDT ({eq / st['initial'] - 1:+.2%})  |  BTC depuis le départ : "
           f"{(btc / st['btc_at_start'] - 1) if st['btc_at_start'] else 0:+.2%}",
           f"Cash : {st['cash']:,.2f}"]
    for a, u in st["units"].items():
        p = sum(ba[symbol_of(a)]) / 2
        txt.append(f"  {a:7s} {u:.6f} × {p:,.4f} = {u * p:,.2f} ({u * p / eq:.0%})")
    for h, tr in st["tranches"].items():
        txt.append(f"  tranche {int(h):02d}h UTC : cash {tr['cash']:,.0f}, {len(tr['units'])} position(s), journée {tr['last_bar']}")
    return "\n".join(txt)


def reset(capital: float = 10_000.0) -> str:
    for p in (STATE, TRADES, EQUITY):
        if p.exists():
            p.unlink()
    save_state(new_state(capital))
    return f"Portefeuille paper réinitialisé : {capital:,.0f} USDT en 6 tranches."
