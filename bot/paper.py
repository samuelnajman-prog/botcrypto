"""Paper trading : même stratégie, même logique d'ordres que le backtest, sur les prix réels
de Binance (carnet bid/ask), sans argent réel et sans clé API.

À lancer une fois par jour juste après la clôture de la bougie journalière (00:00 UTC),
par ex. 00:10 UTC. L'exécution est idempotente : relancer le même jour ne fait rien.

Fichiers d'état (dossier state/) :
  paper_state.json   positions, cash, pic d'équité, stops
  paper_trades.csv   journal des ordres simulés
  paper_equity.csv   équité quotidienne + BTC pour comparaison
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
from .data import API, CANDIDATES, _get, load_panel, to_matrix
from .execution import generate_orders, scale_buys
from .strategy import Config, target_weights

STATE_DIR = Path(os.environ.get("BOT_STATE_DIR", Path(__file__).resolve().parent.parent / "state"))
STATE = STATE_DIR / "paper_state.json"
TRADES = STATE_DIR / "paper_trades.csv"
EQUITY = STATE_DIR / "paper_equity.csv"
FEE = 0.0010        # frais Binance spot taker standard
EXTRA_SLIP = 0.0005  # slippage additionnel au-delà du bid/ask


def symbol_of(asset: str) -> str:
    return asset.split("#")[0] + "USDT"


def new_state(capital: float) -> dict:
    return {"initial": capital, "cash": capital, "units": {}, "peak": capital,
            "high_since_entry": {}, "blocked_until": {}, "last_run_date": None,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "btc_at_start": None}


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


def run_once(cfg: Config | None = None, capital: float = 10_000.0, force: bool = False) -> str:
    cfg = cfg or Config()
    st = load_state() or new_state(capital)

    panel = load_panel(CANDIDATES, verbose=False)          # bougies clôturées uniquement
    mats = {f: to_matrix(panel, f) for f in ["open", "high", "low", "close", "quote_volume"]}
    t = mats["close"].index[-1]
    if st["last_run_date"] == str(t.date()) and not force:
        return f"Déjà exécuté pour la bougie du {t.date()} — rien à faire."

    sig = prepare(mats, cfg)
    cols = mats["close"].columns
    units = pd.Series({a: float(st["units"].get(a, 0.0)) for a in cols})
    live_assets = [a for a in cols if not np.isnan(mats["close"].loc[t, a])]

    # Prix d'exécution : carnet réel
    held = [a for a in cols if units[a] > 0]
    ba = book([symbol_of(a) for a in set(held) | set(live_assets)])
    bid = pd.Series({a: ba[symbol_of(a)][0] for a in cols if symbol_of(a) in ba}).reindex(cols)
    ask = pd.Series({a: ba[symbol_of(a)][1] for a in cols if symbol_of(a) in ba}).reindex(cols)
    mid = (bid + ask) / 2
    # Un actif détenu mais plus coté (délisté) est liquidé au dernier prix connu
    last_close = mats["close"].ffill().iloc[-1]
    rows, now = [], datetime.now(timezone.utc).isoformat(timespec="seconds")
    for a in held:
        if np.isnan(mid.get(a, np.nan)) or a not in live_assets:
            px = float(last_close[a]) if a not in live_assets else float(mid[a])
            val = units[a] * px
            st["cash"] += val * (1 - FEE)
            rows.append([now, str(t.date()), a, "SELL", -units[a], px, val, val * FEE, "delist"])
            units[a] = 0.0

    equity = st["cash"] + float((units * mid.fillna(0)).sum())
    st["peak"] = max(st["peak"], equity)
    dd = 1 - equity / st["peak"]

    # Stops suiveurs (optionnels, même règle que le backtest)
    if cfg.trailing_atr_mult > 0:
        for a, hh in list(st["high_since_entry"].items()):
            c = mats["close"].loc[t, a]
            hh = max(hh, c); st["high_since_entry"][a] = hh
            atr = sig.atr.loc[t, a]
            if units.get(a, 0) > 0 and not np.isnan(atr) and c < hh - cfg.trailing_atr_mult * atr:
                st["blocked_until"][a] = str((t + pd.Timedelta(days=cfg.stop_cooldown_days)).date())
    blocked = {a for a, d in st["blocked_until"].items() if pd.Timestamp(d) > t}

    tgt = target_weights(sig, t, cfg, blocked=blocked, equity_dd=dd)
    orders = generate_orders(units, mid, st["cash"], tgt, cfg.rebalance_threshold)

    for a, du in orders.items():          # ventes au bid
        if du < 0:
            px = bid[a] * (1 - EXTRA_SLIP)
            val = -du * px
            st["cash"] += val * (1 - FEE)
            units[a] += du
            if units[a] < 1e-12:
                units[a] = 0.0; st["high_since_entry"].pop(a, None)
            rows.append([now, str(t.date()), a, "SELL", du, px, val, val * FEE, "rebal"])
    buy_px = ask * (1 + EXTRA_SLIP)
    buys = {a: du for a, du in orders.items() if du > 0}
    k = scale_buys(buys, buy_px, st["cash"], FEE)
    for a, du in buys.items():            # achats à l'ask
        du *= k
        val = du * buy_px[a]
        st["cash"] -= val * (1 + FEE)
        units[a] += du
        st["high_since_entry"][a] = max(st["high_since_entry"].get(a, 0.0), float(mats["close"].loc[t, a]))
        rows.append([now, str(t.date()), a, "BUY", du, buy_px[a], val, val * FEE, "rebal"])

    st["units"] = {a: float(u) for a, u in units.items() if u > 0}
    st["last_run_date"] = str(t.date())
    btc = float(mid.get("BTC", np.nan))
    if st["btc_at_start"] is None:
        st["btc_at_start"] = btc
    equity = st["cash"] + float((units * mid.fillna(0)).sum())
    save_state(st)
    _append_csv(TRADES, ["ts", "bar_date", "asset", "side", "units", "price", "value", "fee", "reason"], rows)
    _append_csv(EQUITY, ["ts", "bar_date", "equity", "cash", "exposure", "btc_price"],
                [[now, str(t.date()), round(equity, 2), round(st["cash"], 2),
                  round(1 - st["cash"] / equity, 4), btc]])

    report = format_report(st, sig, t, cfg, mid, equity, tgt, rows)
    notify(report)
    return report


def format_report(st, sig, t, cfg, mid, equity, tgt, rows) -> str:
    risk_on = bool(sig.risk_on.loc[t])
    btc_ma = sig.close["BTC"].rolling(cfg.regime_ma).mean().loc[t]
    perf = equity / st["initial"] - 1
    btc_perf = (mid["BTC"] / st["btc_at_start"] - 1) if st["btc_at_start"] else 0.0
    lines = [f"🤖 Bot crypto — bougie du {t.date()} (paper trading)",
             f"Équité : {equity:,.0f} USDT ({perf:+.1%} depuis le départ ; BTC {btc_perf:+.1%})",
             f"Drawdown : {1 - equity / st['peak']:.1%}  |  Cash : {st['cash'] / equity:.0%}",
             f"Régime : {'RISK-ON' if risk_on else 'RISK-OFF'} (BTC {sig.close['BTC'].loc[t]:,.0f} vs MM200 {btc_ma:,.0f})"]
    if st["units"]:
        lines.append("Positions : " + ", ".join(
            f"{a} {st['units'][a] * mid[a] / equity:.0%}" for a in sorted(st["units"], key=lambda x: -st["units"][x] * mid[x])))
    else:
        lines.append("Positions : aucune (100 % stablecoin)")
    if rows:
        lines.append("Ordres : " + ", ".join(f"{r[3]} {r[2]} {abs(r[6]):,.0f}$" for r in rows))
    else:
        lines.append("Ordres : aucun")
    fc = sig.forecast.loc[t][sig.eligible.loc[t]].dropna().sort_values(ascending=False)
    lines.append("Tendances les plus fortes : " + ", ".join(f"{a} {v:+.2f}" for a, v in fc.head(5).items()))
    return "\n".join(lines)


def status() -> str:
    st = load_state()
    if not st:
        return "Aucun portefeuille paper. Lance : python main.py paper run"
    ba = book([symbol_of(a) for a in st["units"]] + ["BTCUSDT"])
    val = sum(u * sum(ba[symbol_of(a)]) / 2 for a, u in st["units"].items() if symbol_of(a) in ba)
    eq = st["cash"] + val
    btc = sum(ba["BTCUSDT"]) / 2
    txt = [f"Départ : {st['started_at']}  |  dernier run : bougie du {st['last_run_date']}",
           f"Équité live : {eq:,.2f} USDT ({eq / st['initial'] - 1:+.2%})  |  BTC depuis le départ : "
           f"{(btc / st['btc_at_start'] - 1) if st['btc_at_start'] else 0:+.2%}",
           f"Cash : {st['cash']:,.2f}"]
    for a, u in st["units"].items():
        p = sum(ba[symbol_of(a)]) / 2
        txt.append(f"  {a:7s} {u:.6f} × {p:,.4f} = {u * p:,.2f} ({u * p / eq:.0%})")
    return "\n".join(txt)


def reset(capital: float = 10_000.0) -> str:
    for p in (STATE, TRADES, EQUITY):
        if p.exists():
            p.unlink()
    save_state(new_state(capital))
    return f"Portefeuille paper réinitialisé : {capital:,.0f} USDT."
