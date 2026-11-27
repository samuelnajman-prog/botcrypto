"""Construit (ou recharge) les panneaux intraday actions pour la recherche."""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from stocks import data as D  # noqa: E402
from stocks import research as R  # noqa: E402

CACHE = D.DATA / "panels"


def get(interval: str, rebuild: bool = False) -> R.Panel:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{interval}.pkl"
    if p.exists() and not rebuild:
        return pickle.loads(p.read_bytes())
    files = sorted((D.DATA / interval).glob("*.parquet"))
    intra = {f.stem: pd.read_parquet(f) for f in files}
    daily = {s: D.load(s, "1d") for s in intra}
    daily = {s: d for s, d in daily.items() if d is not None and len(d) > 30}
    bpd = {"5m": 78, "15m": 26, "1h": 7}[interval]
    P = R.build_panel(intra, daily, interval, bpd)
    p.write_bytes(pickle.dumps(P))
    return P


if __name__ == "__main__":
    for iv in sys.argv[1:] or ["5m"]:
        P = get(iv, rebuild=True)
        print(iv, len(P.syms), "titres", len(P.days), "séances", P.days[0], "→", P.days[-1])
