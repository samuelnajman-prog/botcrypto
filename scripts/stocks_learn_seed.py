"""Données de départ de l'apprentissage du bot n°2 (à partir de la recherche 1 h, 2,9 ans) :
- stocks/learn_seed/candidates.csv.gz : chaque gap ≥ 3 % (pré-séance 9:00), ses caractéristiques
  et son résultat de vente à découvert (stop 15 % / 25 %, rachat à la clôture) ;
- stocks/learn_seed/variants.csv.gz : rendement journalier de chaque variante de la règle,
  y compris le filtre appris évalué sans regard vers l'avenir (réentraîné tous les 20 jours)."""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.stocks_panel import get  # noqa: E402
from stocks import learn as LN  # noqa: E402
from stocks import data as D  # noqa: E402

SLIP, CS = 0.0003, 0.0002


def main() -> None:
    P = get("1h")
    pg = np.load(D.DATA / "panels" / "pg1h.npy")
    dl = P.daily
    pc = dl["prev_close"]
    prev_ret = np.c_[np.full((pc.shape[0], 1), np.nan), pc[:, 1:] / pc[:, :-1] - 1]
    spy_gap = pg[P.syms.index("SPY")]
    m = (pc > 5) & (dl["dvol20"] > 30e6) & (pg >= 0.03) & (pg < 0.6) & (P.nbar >= 2) & np.isfinite(pg)
    rows = []
    for i, t in zip(*np.where(m)):
        nb = int(P.nbar[i, t])
        e = P.o[i, t, 0] * (1 - SLIP)
        hi = np.nanmax(P.h[i, t, :nb])
        c = P.c[i, t, nb - 1]
        r = {sp: -((e * (1 + sp) if hi >= e * (1 + sp) else c) * (1 + SLIP) / e - 1) - 2 * CS for sp in (0.15, 0.25)}
        rows.append({"day": str(P.days[t]), "sym": P.syms[i], "gap": pg[i, t], "atrp": dl["atr14"][i, t] / pc[i, t],
                     "dvol": dl["dvol20"][i, t], "price": pc[i, t], "pret": prev_ret[i, t], "spyg": spy_gap[t],
                     "r15": r[0.15], "r25": r[0.25]})
    C = LN.add_features(pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan).fillna(0.0))
    C.round(6).to_csv(ROOT / "stocks" / "learn_seed" / "candidates.csv.gz", index=False)
    days = [str(d) for d in P.days[20:]]
    V = pd.DataFrame({name: LN.variant_returns(C, v, days) for name, v in LN.VARIANTS.items()})
    # filtre appris, réentraîné tous les 20 jours sur le passé seulement
    filt = pd.Series(0.0, index=days)
    for s in range(140, len(days), 20):
        past = C[C.day < days[s]]
        model = LN.train(past)
        block = days[s:s + 20]
        cur = C[C.day.isin(block)]
        filt.loc[block] = LN.variant_returns(cur, {**LN.VARIANTS[LN.CHAMPION], "model": True}, block, model).values
    V[LN.CHAMPION + "+modèle"] = filt
    V.iloc[120:].round(6).to_csv(ROOT / "stocks" / "learn_seed" / "variants.csv.gz")
    print(len(C), "candidats ;", V.shape, "variantes")


if __name__ == "__main__":
    main()
