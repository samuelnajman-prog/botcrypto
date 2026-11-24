"""Tableau de bord du paper trading, régénéré après chaque exécution.

Produit :
  - docs/img/*-light.svg et *-dark.svg (graphiques, fond plein lisible dans les deux thèmes)
  - le bloc entre <!-- DASHBOARD:START --> et <!-- DASHBOARD:END --> du README.md

GitHub affiche le README en page d'accueil du dépôt : ouvrir le dépôt = voir les résultats.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .backtest import prepare  # noqa: E402
from .data import load_panel, to_matrix  # noqa: E402
from .strategy import Config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
IMG = ROOT / "docs" / "img"
README = ROOT / "README.md"
START, END = "<!-- DASHBOARD:START -->", "<!-- DASHBOARD:END -->"

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781", grid="#e1e0d9",
                  axis="#c3c2b7", s1="#2a78d6", s2="#eb6834", pos="#2a78d6", neg="#e34948"),
    "dark": dict(surface="#1a1a19", ink="#f0efec", ink2="#c3c2b7", muted="#898781", grid="#2c2c2a",
                 axis="#383835", s1="#3987e5", s2="#d95926", pos="#3987e5", neg="#e66767"),
}


# ---------- formats français ----------------------------------------------------

def pct(x: float, sign: bool = True, dec: int = 1) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    if round(abs(x) * 100, dec) == 0:
        x, sign = 0.0, False
    s = f"{x * 100:+.{dec}f}" if sign else f"{x * 100:.{dec}f}"
    return s.replace("-", "−").replace(".", ",") + " %"


def num(x: float, dec: int = 0) -> str:
    s = f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")
    return s.replace("-", "−")


def fdate(d) -> str:
    return pd.Timestamp(d).strftime("%d/%m/%Y")


# ---------- habillage commun des graphiques --------------------------------------

def _frame(theme: str, h: float = 3.0):
    t = THEMES[theme]
    fig, ax = plt.subplots(figsize=(7.2, h), dpi=100)
    fig.patch.set_facecolor(t["surface"])
    ax.set_facecolor(t["surface"])
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(t["axis"])
    ax.tick_params(colors=t["ink2"], labelsize=9, length=0)
    ax.grid(axis="y", color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax, t


def _title(fig, t, title: str, sub: str):
    fig.text(0.015, 0.965, title, fontsize=12, fontweight="bold", color=t["ink"], va="top")
    fig.text(0.015, 0.885, sub, fontsize=9, color=t["ink2"], va="top")


def _save(fig, name: str, theme: str, top: float = 0.78):
    fig.subplots_adjust(left=0.09, right=0.84, top=top, bottom=0.12)
    IMG.mkdir(parents=True, exist_ok=True)
    fig.savefig(IMG / f"{name}-{theme}.svg", facecolor=fig.get_facecolor())
    plt.close(fig)


def _end_labels(ax, items, t):
    """Étiquettes en bout de ligne, écartées pour ne pas se chevaucher."""
    ymin, ymax = ax.get_ylim()
    gap = (ymax - ymin) * 0.08
    items = sorted(items, key=lambda i: i[1])
    placed = []
    for label, y, color in items:
        if placed and y - placed[-1] < gap:
            y = placed[-1] + gap
        placed.append(y)
        ax.annotate(label, xy=(1.0, y), xycoords=("axes fraction", "data"), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9, color=t["ink"])


# ---------- graphiques -------------------------------------------------------------

def chart_perf(eq: pd.DataFrame, theme: str):
    fig, ax, t = _frame(theme)
    x = eq["date"]
    bot = eq["equity"] / eq["equity"].iloc[0] - 1
    btc = eq["btc_price"] / eq["btc_price"].iloc[0] - 1
    mk = "o" if len(eq) < 15 else None
    ax.plot(x, btc, color=t["s2"], lw=2, marker=mk, ms=5)
    ax.plot(x, bot, color=t["s1"], lw=2.5, marker=mk, ms=5)
    ax.axhline(0, color=t["axis"], lw=1)
    lo, hi = min(bot.min(), btc.min(), -0.02), max(bot.max(), btc.max(), 0.02)
    pad = (hi - lo) * 0.15
    ax.set_ylim(lo - pad, hi + pad)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: pct(v, dec=0)))
    if (x.iloc[-1] - x.iloc[0]).days <= 10:
        ax.set_xticks(list(x))
    else:
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    if len(eq) < 2:
        ax.set_xlim(x.iloc[0] - pd.Timedelta(days=1), x.iloc[0] + pd.Timedelta(days=1))
    _end_labels(ax, [(f"Bot {pct(bot.iloc[-1])}", bot.iloc[-1], t["s1"]),
                     (f"BTC {pct(btc.iloc[-1])}", btc.iloc[-1], t["s2"])], t)
    _title(fig, t, "Performance depuis le départ", "Paper trading (bleu) vs garder du BTC (orange), même date de départ")
    _save(fig, "perf", theme)


def chart_regime(btc: pd.Series, ma: pd.Series, theme: str):
    fig, ax, t = _frame(theme)
    b, m = btc.tail(365), ma.tail(365)
    ax.plot(m.index, m, color=t["s2"], lw=2, ls=(0, (4, 2)))
    ax.plot(b.index, b, color=t["s1"], lw=2)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:.0f} k$"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 4, 7, 10]))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%y"))
    _end_labels(ax, [(f"BTC {num(b.iloc[-1] / 1000, 1)}k", b.iloc[-1], t["s1"]),
                     (f"MM200 {num(m.iloc[-1] / 1000, 1)}k", m.iloc[-1], t["s2"])], t)
    _title(fig, t, "Le feu vert du marché : BTC vs sa moyenne 200 jours",
           "BTC au-dessus de la ligne pointillée = le bot peut investir normalement (12 derniers mois)")
    _save(fig, "regime", theme)


def chart_signals(fc: pd.Series, theme: str):
    fc = fc.sort_values()
    fig, ax, t = _frame(theme, h=0.9 + 0.24 * len(fc))
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=t["grid"], linewidth=0.8)
    ax.spines["bottom"].set_visible(False)
    colors = [t["pos"] if v > 0 else t["neg"] for v in fc]
    ax.barh(fc.index, fc.values, color=colors, height=0.68)
    ax.axvline(0, color=t["ink2"], lw=1)
    lim = max(0.5, float(np.abs(fc).max()) * 1.25)
    ax.set_xlim(-lim, lim)
    for i, (a, v) in enumerate(fc.items()):
        ax.text(v + (0.03 * lim if v >= 0 else -0.03 * lim), i, f"{v:+.2f}".replace(".", ",").replace("-", "−"),
                va="center", ha="left" if v >= 0 else "right", fontsize=8, color=t["ink2"])
    ax.tick_params(axis="y", labelsize=9, colors=t["ink"])
    ax.tick_params(axis="x", labelsize=8)
    _title(fig, t, "Force de tendance par crypto", "Achat possible seulement au-dessus de 0 (bleu). Les cryptos les plus liquides du jour.")
    top = 1 - 0.75 / (0.9 + 0.24 * len(fc))
    fig.subplots_adjust(left=0.12, right=0.97, top=top, bottom=0.04)
    IMG.mkdir(parents=True, exist_ok=True)
    fig.savefig(IMG / f"signals-{theme}.svg", facecolor=fig.get_facecolor())
    plt.close(fig)


def _picture(name: str, alt: str) -> str:
    return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="docs/img/{name}-dark.svg">'
            f'<img src="docs/img/{name}-light.svg" alt="{alt}" width="100%"></picture>')


# ---------- assemblage ----------------------------------------------------------------

def build(cfg: Config | None = None) -> str:
    cfg = cfg or Config()
    st = json.loads((STATE / "paper_state.json").read_text())
    eq = pd.read_csv(STATE / "paper_equity.csv", parse_dates=["bar_date"])
    eq["date"] = eq["bar_date"] + pd.Timedelta(days=1)   # valorisation juste après la clôture
    tr_path = STATE / "paper_trades.csv"
    trades = pd.read_csv(tr_path) if tr_path.exists() else pd.DataFrame()

    panel = load_panel(verbose=False)
    mats = {f: to_matrix(panel, f) for f in ["open", "high", "low", "close", "quote_volume"]}
    sig = prepare(mats, cfg)
    t = mats["close"].index[-1]
    btc = mats["close"]["BTC"]
    ma = btc.rolling(cfg.regime_ma).mean()
    risk_on = bool(sig.risk_on.loc[t])
    fc = sig.forecast.loc[t][sig.eligible.loc[t]].dropna()

    for th in THEMES:
        chart_perf(eq, th)
        chart_regime(btc.dropna(), ma.dropna(), th)
        chart_signals(fc, th)

    last = eq.iloc[-1]
    perf = last.equity / st["initial"] - 1
    btc_perf = last.btc_price / st["btc_at_start"] - 1
    dd_series = eq.equity / eq.equity.cummax() - 1
    dd_now, dd_max = float(dd_series.iloc[-1]), float(dd_series.min())
    days = (pd.Timestamp(t) - pd.Timestamp(st["started_at"][:10])).days + 1
    gap = (pd.Timestamp(t) - eq.bar_date.max()).days
    n_pos = int((fc > 0).sum())
    dist = btc.loc[t] / ma.loc[t] - 1

    if risk_on:
        regime = (f"🟢 **Marché haussier (risk-on)** : BTC à {num(btc.loc[t])} $, {pct(dist)} au-dessus de sa "
                  f"moyenne 200 jours. Le bot peut investir jusqu'à 100 %.")
    else:
        regime = (f"🔴 **Marché baissier (risk-off)** : BTC à {num(btc.loc[t])} $, {pct(dist)} sous sa moyenne "
                  f"200 jours ({num(ma.loc[t])} $). Le bot n'investit que sur BTC/ETH, 35 % maximum, "
                  f"et seulement s'ils sont en tendance haussière.")
    if st["units"]:
        lines = ["| Crypto | Quantité | Poids |", "|---|---:|---:|"]
        pos = {a: u * mats["close"][a].loc[t] for a, u in st["units"].items() if a in mats["close"]}
        for a, v in sorted(pos.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {a} | {num(st['units'][a], 4)} | {pct(v / last.equity, sign=False, dec=0)} |")
        positions = "\n".join(lines)
    else:
        positions = "Aucune : 100 % en stablecoin (USDT)."
    if len(trades):
        tl = ["| Bougie | Ordre | Crypto | Montant | Prix |", "|---|---|---|---:|---:|"]
        for _, r in trades.tail(10).iloc[::-1].iterrows():
            side = "Achat" if r.side == "BUY" else "Vente"
            tl.append(f"| {fdate(r.bar_date)} | {side} | {r.asset} | {num(abs(r.value))} $ | {num(r.price, 4)} |")
        trades_md = "\n".join(tl)
    else:
        trades_md = "Aucun ordre pour l'instant."

    def flag(ok: bool) -> str:
        return "✅" if ok else "⚠️"

    now = datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M UTC")
    block = f"""{START}
## 📊 Suivi du paper trading

*Mis à jour automatiquement le {now}, bougie du {fdate(t)}. Jour {days} du test, argent fictif.*

| Valeur | Depuis le départ | BTC, même période | Repli actuel | Investi |
|:---:|:---:|:---:|:---:|:---:|
| **{num(last.equity)} $** | **{pct(perf)}** | {pct(btc_perf)} | {pct(dd_now)} | {pct(last.exposure, sign=False, dec=0)} |

{regime} Cryptos en tendance haussière aujourd'hui : **{n_pos} sur {len(fc)}**.

{_picture("perf", "Performance du bot et de BTC depuis le départ")}

{_picture("regime", "BTC et sa moyenne 200 jours sur 12 mois")}

<details>
<summary><b>Force de tendance de chaque crypto</b> (achat possible si &gt; 0)</summary>

{_picture("signals", "Force de tendance des 20 cryptos les plus liquides")}

</details>

### Positions
{positions}

### Derniers ordres
{trades_md}

### Seuils d'alerte
| Indicateur | Actuel | Normal | Alerte |
|---|---:|---:|---:|
| {flag(dd_max > -0.40)} Pire repli depuis le départ | {pct(dd_max)} | > −25 % | < −40 % |
| {flag(gap <= 1)} Bougies non traitées | {gap} | 0 | ≥ 2 |
| {flag(True)} Nombre d'ordres | {len(trades)} | — | — |

Repères du backtest (2018-2026) sur 6 mois : médiane +4 %, 5 % pires cas −16 %, 5 % meilleurs cas +136 %.
{END}"""

    text = README.read_text()
    if START in text and END in text:
        text = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    else:
        head, _, rest = text.partition("\n")
        text = f"{head}\n\n{block}\n{rest}"
    README.write_text(text)
    return block
