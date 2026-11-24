"""Taux de financement (funding) des contrats perpétuels, pour simuler des positions short.

Historique : archives publiques Binance USDⓈ-M (data.binance.vision), cumul journalier.
Convention : taux > 0 → les longs paient les shorts (un short ENCAISSE le funding).
"""
from __future__ import annotations

import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd

from .data import DATA_DIR, _get

ARCHIVE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"


def fetch_funding(asset: str, start: str = "2019-09", end: str | None = None) -> pd.Series | None:
    sym = f"{asset.split('#')[0]}USDT"
    end = end or datetime.now(timezone.utc).strftime("%Y-%m")
    parts = []
    for p in pd.period_range(start, end, freq="M"):
        r = _get(f"{ARCHIVE}/{sym}/{sym}-fundingRate-{p.strftime('%Y-%m')}.zip")
        if r.status_code != 200:
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            df = pd.read_csv(z.open(z.namelist()[0]))
        tcol = "calc_time" if "calc_time" in df.columns else df.columns[0]
        rcol = "last_funding_rate" if "last_funding_rate" in df.columns else df.columns[-1]
        ts = pd.to_numeric(df[tcol])
        ts = ts.where(ts < 1e14, ts // 1000)
        parts.append(pd.Series(df[rcol].astype(float).values,
                               index=pd.to_datetime(ts, unit="ms").dt.normalize()))
    if not parts:
        return None
    s = pd.concat(parts)
    return s.groupby(level=0).sum().rename(asset)   # cumul journalier


def load_funding(assets: list[str], workers: int = 8, refresh: bool = False) -> pd.DataFrame:
    path = DATA_DIR / "funding_daily.csv"
    cached = pd.read_csv(path, index_col=0, parse_dates=True) if path.exists() and not refresh else pd.DataFrame()
    todo = [a for a in assets if a not in cached.columns]
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            res = list(ex.map(fetch_funding, todo))
        new = pd.concat([s for s in res if s is not None], axis=1) if any(s is not None for s in res) else pd.DataFrame()
        # marque les actifs sans perpétuel (colonne vide) pour ne pas re-télécharger
        for a, s in zip(todo, res):
            if s is None:
                new[a] = float("nan")
        cached = pd.concat([cached, new], axis=1).sort_index()
        DATA_DIR.mkdir(exist_ok=True)
        cached.to_csv(path)
    return cached.reindex(columns=assets)
