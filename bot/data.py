"""Données de marché : bougies Binance spot (API publique, sans clé), journalières ou 4h.

- Source 1 : data-api.binance.vision (miroir public de l'API Binance, market data only)
- Source 2 (repli) : archives mensuelles data.binance.vision (paires délistées)
- Cache local en CSV dans data/ : uniquement des bougies clôturées.

Les séries sont découpées aux trous > MAX_GAP_DAYS : un ticker réutilisé pour un
nouveau token (ex. LUNA après l'effondrement de 2022) devient un actif distinct
("LUNA#2"), ce qui évite des rendements fictifs et garde l'actif mort dans
l'historique (moins de biais du survivant).
"""
from __future__ import annotations

import io
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

API = "https://data-api.binance.vision/api/v3"
ARCHIVE = "https://data.binance.vision/data/spot/monthly/klines"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "tb_base", "tb_quote", "ignore"]
MAX_GAP_DAYS = 7
BAR_HOURS = {"1d": 24, "4h": 4, "1h": 1}
DEFAULT_START = {"1d": "2017-07-01", "4h": "2019-01-01", "1h": "2022-01-01"}
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Univers "core" (v1) : ~50 grandes cryptos, y compris des tokens morts ou délistés,
# pour limiter le biais du survivant.
CANDIDATES = (
    "BTC ETH BNB XRP ADA SOL DOGE DOT AVAX LINK LTC BCH TRX XLM ETC EOS ATOM UNI "
    "FIL MATIC POL ICP NEAR XTZ VET THETA ALGO AAVE SHIB LUNA FTT NEO XMR EGLD "
    "SAND MANA AXS APT ARB OP SUI HBAR PEPE INJ TON RENDER FET WLD SEI TIA"
).split()

# Univers "wide" (v2) : + memecoins, DeFi, IA, nouveaux L1/L2, tokens morts ou délistés.
EXTRA = (
    "WIF BONK FLOKI ENA ONDO JUP PENGU TRUMP TAO ORDI JASMY GALA IMX STX RUNE GRT LDO CRV "
    "KAVA CHZ ENS DYDX GMT APE BLUR PYTH STRK ZK NOT PEOPLE BOME W ETHFI AR QNT ROSE FTM S "
    "BERA VIRTUAL KAITO LAYER JTO ARKM CAKE COMP SNX 1INCH SUSHI YFI ZEC DASH IOTA ZIL ONE "
    "CELO FLOW MINA KSM WOO MASK LRC ENJ BAT RVN WAVES OCEAN AGIX FXS LUNC USTC MEME NEIRO "
    "ACT PNUT MOVE USUAL OM TURBO EIGEN SAGA DOGS HMSTR SRM SXP BTT WIN"
).split()
CANDIDATE_SETS = {"core": CANDIDATES, "wide": CANDIDATES + [a for a in EXTRA if a not in CANDIDATES]}

_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def _get(url: str, params: dict | None = None, retries: int = 5):
    for i in range(retries):
        try:
            r = _session().get(url, params=params, timeout=30)
            if r.status_code in (200, 400, 404):
                return r
        except requests.RequestException:
            pass
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"Échec réseau: {url} {params}")


def _to_frame(rows: list, interval: str = "1d") -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLS)
    ot = pd.to_numeric(df["open_time"])
    ot = ot.where(ot < 1e14, ot // 1000)    # archives post-2025 en microsecondes
    idx = pd.to_datetime(ot, unit="ms", utc=True).dt.tz_localize(None)
    df.index = idx.dt.normalize() if interval == "1d" else idx.dt.floor("h")
    df.index.name = "date"
    out = df[["open", "high", "low", "close", "volume", "quote_volume"]].astype(float)
    return out[~out.index.duplicated(keep="last")].sort_index()


def fetch_api(symbol: str, start: str | None = None, interval: str = "1d") -> pd.DataFrame | None:
    start = start or DEFAULT_START[interval]
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    step = BAR_HOURS[interval] * 3_600_000
    rows: list = []
    while True:
        r = _get(f"{API}/klines", {"symbol": symbol, "interval": interval,
                                   "startTime": start_ms, "limit": 1000})
        if r.status_code != 200:
            return None
        batch = r.json()
        if not batch:
            break
        rows += batch
        if len(batch) < 1000:
            break
        start_ms = batch[-1][0] + step
    return _to_frame(rows, interval) if rows else None


def fetch_archive(symbol: str, start: str = "2017-08", end: str | None = None,
                  interval: str = "1d") -> pd.DataFrame | None:
    end = end or datetime.now(timezone.utc).strftime("%Y-%m")
    frames = []
    for p in pd.period_range(start, end, freq="M"):
        url = f"{ARCHIVE}/{symbol}/{interval}/{symbol}-{interval}-{p.strftime('%Y-%m')}.zip"
        r = _get(url)
        if r.status_code != 200:
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            raw = z.read(z.namelist()[0]).decode()
        lines = [l.split(",") for l in raw.strip().splitlines() if l and l[0].isdigit()]
        frames.append(_to_frame(lines, interval))
    if not frames:
        return None
    return pd.concat(frames).sort_index()


def split_on_gaps(name: str, df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Découpe une série aux trous > MAX_GAP_DAYS (ticker réutilisé, suspension)."""
    gaps = df.index.to_series().diff().dt.total_seconds().fillna(0) / 86_400
    seg_id = (gaps > MAX_GAP_DAYS).cumsum()
    out = {}
    for k, seg in df.groupby(seg_id):
        if len(seg) < 30:
            continue
        out[name if k == 0 else f"{name}#{k + 1}"] = seg
    return out


def drop_open_candle(df: pd.DataFrame, now: pd.Timestamp | None = None, interval: str = "1d") -> pd.DataFrame:
    """Retire la bougie en cours (non clôturée) : indispensable en live."""
    now = now or pd.Timestamp.now("UTC").tz_localize(None)
    return df[df.index + pd.Timedelta(hours=BAR_HOURS[interval]) <= now]


def _cache_path(symbol: str, interval: str) -> Path:
    return DATA_DIR / (f"{symbol}.csv" if interval == "1d" else f"{symbol}_{interval}.csv")


def load_symbol(asset: str, refresh: bool = False, interval: str = "1d") -> dict[str, pd.DataFrame]:
    DATA_DIR.mkdir(exist_ok=True)
    symbol = f"{asset}USDT"
    path = _cache_path(symbol, interval)
    now = pd.Timestamp.now("UTC").tz_localize(None)
    bar = pd.Timedelta(hours=BAR_HOURS[interval])
    if path.exists() and not refresh:
        df = pd.read_csv(path, index_col="date", parse_dates=True)
        if df.empty:
            return {}
        last = df.index.max()
        # Une nouvelle bougie a pu clôturer : on recharge la fin (dernières lignes réécrites)
        if last + 2 * bar <= now:
            new = fetch_api(symbol, start=str(last - 2 * bar), interval=interval)
            if new is not None and len(new):
                df = pd.concat([df, new])
                df = df[~df.index.duplicated(keep="last")].sort_index()
                drop_open_candle(df, now, interval).to_csv(path)
    else:
        df = fetch_api(symbol, interval=interval)
        if df is None or len(df) < 30:
            df = fetch_archive(symbol, start=DEFAULT_START[interval][:7], interval=interval)
        if df is None or len(df) < 30:
            pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "quote_volume"]).to_csv(path, index=False)
            return {}
        drop_open_candle(df, now, interval).to_csv(path)
    return split_on_gaps(asset, drop_open_candle(df, now, interval))


def load_panel(assets=CANDIDATES, refresh: bool = False, verbose: bool = True,
               interval: str = "1d", workers: int = 6) -> dict[str, pd.DataFrame]:
    """Retourne {actif: DataFrame OHLCV} pour tous les candidats disponibles."""
    if isinstance(assets, str):
        assets = CANDIDATE_SETS[assets]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda a: (a, load_symbol(a, refresh=refresh, interval=interval)), assets))
    panel: dict[str, pd.DataFrame] = {}
    for a, segs in results:
        panel.update(segs)
        if verbose:
            info = ", ".join(f"{k}:{v.index[0].date()}→{v.index[-1].date()}" for k, v in segs.items()) or "indisponible"
            print(f"  {a:7s} {info}")
    return panel


def to_matrix(panel: dict[str, pd.DataFrame], field: str) -> pd.DataFrame:
    return pd.DataFrame({k: v[field] for k, v in panel.items()}).sort_index()
