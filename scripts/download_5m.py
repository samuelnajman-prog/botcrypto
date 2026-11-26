"""Télécharge les bougies 5 min Binance (archives mensuelles) : python scripts/download_5m.py BTCUSDT ETHUSDT ..."""
import io, zipfile, sys, time
from concurrent.futures import ThreadPoolExecutor
import pandas as pd, requests
ARCH = "https://data.binance.vision/data/spot/monthly/klines"
S = requests.Session()
def month(sym, p):
    url = f"{ARCH}/{sym}/5m/{sym}-5m-{p}.zip"
    for i in range(4):
        try:
            r = S.get(url, timeout=60)
            if r.status_code == 404: return None
            if r.status_code == 200: break
        except Exception: pass
        time.sleep(2 * (i + 1))
    else: return None
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=range(8))
    df = df[pd.to_numeric(df[0], errors="coerce").notna()]
    ot = pd.to_numeric(df[0]); ot = ot.where(ot < 1e14, ot // 1000)
    out = pd.DataFrame({"open": df[1].astype(float).values, "high": df[2].astype(float).values, "low": df[3].astype(float).values,
                        "close": df[4].astype(float).values, "volume": df[5].astype(float).values, "quote_volume": df[7].astype(float).values},
                       index=pd.to_datetime(ot.values, unit="ms"))
    return out
months = [p.strftime("%Y-%m") for p in pd.period_range("2020-01", "2026-10", freq="M")]
for sym in sys.argv[1:]:
    t0 = time.time()
    with ThreadPoolExecutor(8) as ex:
        parts = [x for x in ex.map(lambda p: month(sym, p), months) if x is not None]
    df = pd.concat(parts).sort_index(); df = df[~df.index.duplicated()]
    df.index.name = "date"
    df.to_csv(f"data/{sym}_5m.csv.gz")
    print(sym, len(df), df.index[0], df.index[-1], round(time.time() - t0), "s", flush=True)
