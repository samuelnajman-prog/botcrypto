"""Données de marché : bougies journalières Binance spot (API publique, sans clé).

- Source 1 : data-api.binance.vision (miroir public de l'API Binance, market data only)
- Source 2 (repli) : archives mensuelles data.binance.vision (paires délistées)
- Cache local en CSV dans data/ pour ne pas re-télécharger.

Les séries sont découpées aux trous > MAX_GAP_DAYS : un ticker réutilisé pour un
nouveau token (ex. LUNA après l'effondrement de 2022) devient un actif distinct
("LUNA#2"), ce qui évite des rendements fictifs et garde l'actif mort dans
l'historique (moins de biais du survivant).
"""
from __future__ import annotations

import io
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

API = "https://data-api.binance.vision/api/v3"
ARCHIVE = "https://data.binance.vision/data/spot/monthly/klines"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "tb_base", "tb_quote", "ignore"]
MAX_GAP_DAYS = 7
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Liste large de candidats, y compris des tokens morts ou délistés, pour limiter
# le biais du survivant. L'univers réellement tradé est choisi point-in-time
# par la liquidité (voir universe.py).
CANDIDATES = (
    "BTC ETH BNB XRP ADA SOL DOGE DOT AVAX LINK LTC BCH TRX XLM ETC EOS ATOM UNI "
    "FIL MATIC POL ICP NEAR XTZ VET THETA ALGO AAVE SHIB LUNA FTT NEO XMR EGLD "
    "SAND MANA AXS APT ARB OP SUI HBAR PEPE INJ TON RENDER FET WLD SEI TIA"
).split()

_session = requests.Session()


def _get(url: str, params: dict | None = None, retries: int = 4):
    for i in range(retries):
        try:
            r = _session.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r
            if r.status_code in (400, 404):
                return r
        except requests.RequestException:
            pass
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"Échec réseau: {url} {params}")


def _to_frame(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLS)
    ot = pd.to_numeric(df["open_time"])
    # Les archives Binance post-2025 sont en microsecondes
    ot = ot.where(ot < 1e14, ot // 1000)
    df.index = pd.to_datetime(ot, unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
    df.index.name = "date"
    out = df[["open", "high", "low", "close", "volume", "quote_volume"]].astype(float)
    return out[~out.index.duplicated(keep="last")].sort_index()


def fetch_api(symbol: str, start: str = "2017-07-01") -> pd.DataFrame | None:
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    rows: list = []
    while True:
        r = _get(f"{API}/klines", {"symbol": symbol, "interval": "1d",
                                   "startTime": start_ms, "limit": 1000})
        if r.status_code != 200:
            return None
        batch = r.json()
        if not batch:
            break
        rows += batch
        if len(batch) < 1000:
            break
        start_ms = batch[-1][0] + 86_400_000
    return _to_frame(rows) if rows else None


def fetch_archive(symbol: str, start: str = "2017-08", end: str | None = None) -> pd.DataFrame | None:
    end = end or datetime.now(timezone.utc).strftime("%Y-%m")
    frames = []
    misses = 0
    for p in pd.period_range(start, end, freq="M"):
        url = f"{ARCHIVE}/{symbol}/1d/{symbol}-1d-{p.strftime('%Y-%m')}.zip"
        r = _get(url)
        if r.status_code != 200:
            misses += 1
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            raw = z.read(z.namelist()[0]).decode()
        lines = [l.split(",") for l in raw.strip().splitlines() if l and l[0].isdigit()]
        frames.append(_to_frame(lines))
    if not frames:
        return None
    return pd.concat(frames).sort_index()


def split_on_gaps(name: str, df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Découpe une série aux trous > MAX_GAP_DAYS (ticker réutilisé, suspension)."""
    gaps = df.index.to_series().diff().dt.days.fillna(1)
    seg_id = (gaps > MAX_GAP_DAYS).cumsum()
    out = {}
    for k, seg in df.groupby(seg_id):
        if len(seg) < 30:
            continue
        out[name if k == 0 else f"{name}#{k + 1}"] = seg
    return out


def load_symbol(asset: str, refresh: bool = False) -> dict[str, pd.DataFrame]:
    DATA_DIR.mkdir(exist_ok=True)
    symbol = f"{asset}USDT"
    path = DATA_DIR / f"{symbol}.csv"
    now = pd.Timestamp.now("UTC").tz_localize(None)
    if path.exists() and not refresh:
        df = pd.read_csv(path, index_col="date", parse_dates=True)
        last = df.index.max()
        # Le cache ne contient que des bougies clôturées : on recharge la fin dès
        # qu'une nouvelle bougie a pu clôturer (les dernières lignes sont réécrites).
        if last + pd.Timedelta(days=2) <= now:
            new = fetch_api(symbol, start=str((last - pd.Timedelta(days=2)).date()))
            if new is not None and len(new):
                df = pd.concat([df, new])
                df = df[~df.index.duplicated(keep="last")].sort_index()
                drop_open_candle(df, now).to_csv(path)
    else:
        df = fetch_api(symbol)
        if df is None or len(df) < 30:
            df = fetch_archive(symbol)
        if df is None or len(df) < 30:
            return {}
        drop_open_candle(df, now).to_csv(path)
    return split_on_gaps(asset, drop_open_candle(df, now))


def drop_open_candle(df: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Retire la bougie du jour en cours (non clôturée) : indispensable en live."""
    now = now or pd.Timestamp.now('UTC').tz_localize(None)
    return df[df.index + pd.Timedelta(days=1) <= now]


def load_panel(assets=CANDIDATES, refresh: bool = False, verbose: bool = True) -> dict[str, pd.DataFrame]:
    """Retourne {actif: DataFrame OHLCV journalier} pour tous les candidats disponibles."""
    panel: dict[str, pd.DataFrame] = {}
    for a in assets:
        segs = load_symbol(a, refresh=refresh)
        for k, v in segs.items():
            panel[k] = drop_open_candle(v)
        if verbose:
            info = ", ".join(f"{k}:{v.index[0].date()}→{v.index[-1].date()}" for k, v in segs.items()) or "indisponible"
            print(f"  {a:7s} {info}")
    return panel


def to_matrix(panel: dict[str, pd.DataFrame], field: str) -> pd.DataFrame:
    return pd.DataFrame({k: v[field] for k, v in panel.items()}).sort_index()
