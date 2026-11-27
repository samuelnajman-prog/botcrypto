"""Données actions US : univers (Nasdaq Trader), bougies Yahoo (1d / 1h / 5m), cache local.

Sources sans clé :
- https://www.nasdaqtrader.com/dynamic/SymDir/{nasdaqlisted,otherlisted}.txt  (liste des titres cotés)
- https://query2.finance.yahoo.com/v8/finance/chart/<SYM>  (bougies, non officiel)

Toutes les heures sont en UTC dans les tableaux ; la séance régulière US est 9:30-16:00
heure de New York (le passage heure d'été / d'hiver est géré par zoneinfo).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

UA = "Mozilla/5.0"  # un user-agent de navigateur complet déclenche des 429 chez Yahoo
HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
NASDAQ = "https://www.nasdaqtrader.com/dynamic/SymDir/%s.txt"
NY = "America/New_York"
DATA = Path(os.environ.get("STOCK_DATA", Path(__file__).resolve().parent.parent / "data" / "stocks"))

# noms de titres qui ne sont pas des actions ordinaires
_OUT = re.compile(r"warrant|\bunits?\b|\bright(s)?\b|preferred|depositary shares? represent|"
                  r"\bnotes? due\b|debenture|% |percent|trust preferred|acquisition corp|"
                  r"\bspac\b|closed[- ]end|\bfund\b|\betn\b|exchange[- ]traded", re.I)
_TICKER = re.compile(r"^[A-Z]{1,5}$")


class DataError(RuntimeError):
    pass


def http_get(url: str, timeout: int = 25, tries: int = 4) -> bytes:
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:  # 404 = symbole inconnu : inutile d'insister
            last = e
            if e.code == 404:
                raise DataError(f"404 {url}") from e
            time.sleep(1.5 * (k + 1) + (5 if e.code == 429 else 0))
        except Exception as e:  # réseau
            last = e
            time.sleep(1.5 * (k + 1))
    raise DataError(f"{url}: {last}")


# ------------------------------------------------------------------ univers

def symbol_directory(cache_dir: Path | None = None) -> pd.DataFrame:
    """Actions ordinaires cotées aux US (Nasdaq, NYSE, NYSE American, Arca...), hors ETF."""
    rows = []
    for name in ("nasdaqlisted", "otherlisted"):
        txt = None
        if cache_dir and (cache_dir / f"{name}.txt").exists():
            txt = (cache_dir / f"{name}.txt").read_text()
        if txt is None:
            txt = http_get(NASDAQ % name).decode("utf-8", "replace")
        lines = [ln for ln in txt.replace("\r", "").split("\n") if ln.strip()]
        hdr = lines[0].split("|")
        for ln in lines[1:]:
            if ln.startswith("File Creation Time"):
                continue
            r = dict(zip(hdr, ln.split("|")))
            sym = (r.get("Symbol") or r.get("ACT Symbol") or "").strip()
            if r.get("Test Issue", "N") != "N" or r.get("ETF", "N") != "N":
                continue
            if not _TICKER.match(sym):
                continue
            nm = " ".join(r.get("Security Name", "").split())
            if _OUT.search(nm):
                continue
            rows.append((sym, nm, "Q" if name == "nasdaqlisted" else r.get("Exchange", "")))
    df = pd.DataFrame(rows, columns=["symbol", "name", "exchange"]).drop_duplicates("symbol")
    return df.reset_index(drop=True)


# ------------------------------------------------------------------ bougies Yahoo

def _parse_chart(doc: dict) -> pd.DataFrame:
    res = (doc.get("chart") or {}).get("result")
    if not res:
        err = (doc.get("chart") or {}).get("error")
        raise DataError(str(err))
    r = res[0]
    ts = r.get("timestamp") or []
    if not ts:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({k: q.get(k) for k in ("open", "high", "low", "close", "volume")},
                      index=pd.to_datetime(ts, unit="s", utc=True))
    df = df.astype(float).dropna(subset=["open", "high", "low", "close"])
    df = df[~df.index.duplicated(keep="last")]
    return df


def yahoo_chart(sym: str, interval: str, rng: str | None = None, start: int | None = None,
                end: int | None = None, prepost: bool = False) -> pd.DataFrame:
    ysym = sym.replace(".", "-")
    if rng:
        q = f"interval={interval}&range={rng}"
    else:
        q = f"interval={interval}&period1={int(start)}&period2={int(end)}"
    q += "&includePrePost=" + ("true" if prepost else "false") + "&events=split"
    last = None
    for host in HOSTS:
        try:
            raw = http_get(f"{host}/v8/finance/chart/{ysym}?{q}")
            return _parse_chart(json.loads(raw))
        except DataError as e:
            last = e
            if "404" in str(e):
                break
    raise DataError(f"{sym} {interval}: {last}")


def regular_session(df: pd.DataFrame) -> pd.DataFrame:
    """Garde les bougies de 9:30 à 16:00 (heure de New York) et ajoute la colonne 'day'."""
    if df.empty:
        return df.assign(day=pd.Series(dtype="object"))
    loc = df.index.tz_convert(NY)
    mins = loc.hour * 60 + loc.minute
    keep = (mins >= 570) & (mins < 960)
    out = df[keep].copy()
    out["day"] = loc[keep].date
    return out


# ------------------------------------------------------------------ cache

def _path(interval: str, sym: str) -> Path:
    return DATA / interval / f"{sym}.parquet"


def load(sym: str, interval: str) -> pd.DataFrame | None:
    p = _path(interval, sym)
    if p.exists():
        return pd.read_parquet(p)
    return None


def save(sym: str, interval: str, df: pd.DataFrame) -> None:
    p = _path(interval, sym)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p)


def update(sym: str, interval: str, rng: str) -> pd.DataFrame:
    """Télécharge `rng` et fusionne avec le cache (ce qui permet d'accumuler plus de 60 jours
    de bougies 5 min au fil des semaines)."""
    new = yahoo_chart(sym, interval, rng)
    old = load(sym, interval)
    if old is not None and not old.empty:
        new = pd.concat([old[old.index < new.index.min()], new]) if not new.empty else old
    save(sym, interval, new)
    return new


def bulk(symbols, interval: str, rng: str, workers: int = 8, refresh: bool = False,
         verbose: bool = True) -> dict:
    """Charge/télécharge en parallèle. Retourne {sym: DataFrame} (les échecs sont ignorés)."""
    out, errors = {}, []

    def one(s):
        if not refresh:
            df = load(s, interval)
            if df is not None:
                return s, df
        try:
            return s, update(s, interval, rng)
        except Exception as e:  # noqa: BLE001
            errors.append((s, str(e)[:80]))
            return s, None

    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        for i, (s, df) in enumerate(ex.map(one, symbols)):
            if df is not None and len(df):
                out[s] = df
            if verbose and (i + 1) % 200 == 0:
                print(f"  {interval}: {i + 1}/{len(symbols)} ({time.time() - t0:.0f}s)", flush=True)
    if verbose:
        print(f"{interval}: {len(out)} ok, {len(errors)} échecs, {time.time() - t0:.0f}s")
    return out


def daily_stats(d: pd.DataFrame) -> pd.DataFrame:
    """ATR14, volume moyen 14 j, dollar-volume moyen 20 j — tous décalés d'un jour (connus
    avant l'ouverture)."""
    pc = d["close"].shift(1)
    tr = np.maximum(d["high"] - d["low"], np.maximum((d["high"] - pc).abs(), (d["low"] - pc).abs()))
    s = pd.DataFrame(index=d.index)
    s["atr14"] = tr.rolling(14).mean().shift(1)
    s["vol14"] = d["volume"].rolling(14).mean().shift(1)
    s["dvol20"] = (d["close"] * d["volume"]).rolling(20).mean().shift(1)
    s["prev_close"] = pc
    s["close"] = d["close"]
    s["open"] = d["open"]
    return s
