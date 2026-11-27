"""Apprentissage continu du bot n°2 : « champion / challengers ».

Chaque séance soldée :
1. le bot enregistre TOUS les gaps ≥ 3 % du jour (caractéristiques + résultat d'une vente à
   l'ouverture rachetée à la clôture) : une base de données qui grossit chaque jour, alors que
   Yahoo efface les bougies 5 min après 60 jours ;
2. il calcule le résultat qu'aurait eu chacune des 25 variantes de la règle (nombre de titres,
   stop, seuil de gap, liquidité, filtre appris) — en « fantôme », sans argent fictif engagé ;
3. il réentraîne chaque semaine un modèle (régression ridge) qui estime le reflux attendu
   d'un candidat ;
4. il ne change de règle que sur preuve : au moins 60 séances suivies, 40 séances depuis le
   dernier changement, un challenger meilleur que le champion de façon nette en direct
   (t de Student ≥ 2 sur les écarts quotidiens), pas moins bon sur les 2,9 ans d'historique,
   et une pire baisse pas plus profonde de plus de 5 points. Chaque décision est journalisée.

Pourquoi aussi prudent : testé dans l'ordre chronologique sur 2,9 ans, changer de réglage dès
qu'une variante fait mieux sur les 6 derniers mois n'améliore presque rien et creuse les baisses
(voir STRATEGIE_DAYTRADING.md). L'apprentissage sert d'abord à accumuler des preuves.
"""
from __future__ import annotations

import itertools
import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "state" / "stocks" / "learn"
SEED = ROOT / "stocks" / "learn_seed"
FEATS = ["gap", "atrp", "ldv", "lpx", "pret", "spyg", "rank", "ncand", "gap2", "gapatr"]
MIN_LIVE, COOLDOWN, T_LIVE, DD_TOL, RETRAIN_EVERY = 60, 40, 2.0, 0.05, 5


def vname(n, stop, gap, dvol):
    return f"N{n}_stop{int(stop * 100)}_gap{int(gap * 100)}_liq{int(dvol / 1e6)}"


VARIANTS = {vname(n, s, g, v): {"n": n, "stop": s, "gap": g, "dvol": v}
            for n, s, g, v in itertools.product([10, 15, 20], [0.15, 0.25], [0.03, 0.05], [50e6, 100e6])}
CHAMPION = vname(15, 0.15, 0.03, 50e6)


def all_variants() -> dict:
    out = dict(VARIANTS)
    out[CHAMPION + "+modèle"] = {**VARIANTS[CHAMPION], "model": True}
    return out


# ------------------------------------------------------------------ modèle

def add_features(C: pd.DataFrame) -> pd.DataFrame:
    C = C.copy()
    C["rank"] = C.groupby("day")["gap"].rank(ascending=False)
    C["ncand"] = C.groupby("day")["gap"].transform("size")
    C["gap2"] = C["gap"] ** 2
    C["gapatr"] = C["gap"] / C["atrp"].clip(lower=1e-3)
    C["ldv"] = np.log(C["dvol"].clip(lower=1.0))
    C["lpx"] = np.log(C["price"].clip(lower=0.01))
    return C


def train(C: pd.DataFrame, lam: float = 10.0) -> dict:
    X = C[FEATS].to_numpy(float)
    y = C["r15"].clip(-0.3, 0.3).to_numpy(float)
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    w = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    return {"mu": mu.tolist(), "sd": sd.tolist(), "w": w.tolist(), "b": float(y.mean()), "rows": len(C)}


def predict(model: dict, C: pd.DataFrame) -> np.ndarray:
    Z = (C[FEATS].to_numpy(float) - np.array(model["mu"])) / np.array(model["sd"])
    return Z @ np.array(model["w"]) + model["b"]


def variant_returns(C: pd.DataFrame, v: dict, days: list, model: dict | None = None) -> pd.Series:
    x = C[(C["gap"] >= v["gap"]) & (C["dvol"] >= v["dvol"])]
    x = x[x.groupby("day")["gap"].rank(ascending=False, method="first") <= v["n"]]
    if v.get("model") and model is not None and len(x):
        x = x[predict(model, x) > 0]
    r = x["r15" if v["stop"] == 0.15 else "r25"].groupby(x["day"]).sum() / v["n"]
    return r.reindex(days).fillna(0.0)


# ------------------------------------------------------------------ fichiers

def _csv(name: str) -> Path:
    return DIR / name


def load_live(name: str) -> pd.DataFrame:
    p = _csv(name)
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


def seed_candidates() -> pd.DataFrame:
    p = SEED / "candidates.csv.gz"
    return pd.read_csv(p) if p.exists() else pd.DataFrame(columns=["day"] + FEATS + ["r15", "r25"])


def seed_variants() -> pd.DataFrame:
    p = SEED / "variants.csv.gz"
    return pd.read_csv(p, index_col=0) if p.exists() else pd.DataFrame()


def load_model() -> dict | None:
    p = _csv("model.json")
    return json.loads(p.read_text()) if p.exists() else None


# ------------------------------------------------------------------ statistiques

def tstat(d: pd.Series) -> float:
    d = d.dropna()
    if len(d) < 5 or d.std() == 0:
        return 0.0
    return float(d.mean() / (d.std() / math.sqrt(len(d))))


def maxdd(r: pd.Series) -> float:
    eq = (1 + r.fillna(0)).cumprod()
    return float((eq / eq.cummax() - 1).min()) if len(eq) else 0.0


def decide(champion: str, live: pd.DataFrame, hist: pd.DataFrame, since_change: int) -> tuple[str, dict | None]:
    """Retourne (champion, entrée de journal si changement)."""
    if len(live) < MIN_LIVE or since_change < COOLDOWN or champion not in live:
        return champion, None
    best, best_t = None, T_LIVE
    for c in live.columns:
        if c == champion:
            continue
        t_live = tstat(live[c] - live[champion])
        t_hist = tstat(hist[c] - hist[champion]) if c in hist and champion in hist else 0.0
        if t_live >= best_t and t_hist >= 0 and maxdd(live[c]) >= maxdd(live[champion]) - DD_TOL:
            best, best_t = c, t_live
    if best is None:
        return champion, None
    return best, {"from": champion, "to": best, "t_live": round(best_t, 2), "sessions": len(live),
                  "live_gain_from": round(float((1 + live[champion]).prod() - 1), 4),
                  "live_gain_to": round(float((1 + live[best]).prod() - 1), 4)}


# ------------------------------------------------------------------ séance soldée

def outcome(reg: pd.DataFrame, slip: float = 0.0003, cs: float = 0.0002) -> dict | None:
    if not len(reg) or reg["mins"].iloc[0] != 570:
        return None
    e = float(reg["open"].iloc[0]) * (1 - slip)
    hi, c = float(reg["high"].max()), float(reg["close"].iloc[-1])
    return {f"r{int(sp * 100)}": -((e * (1 + sp) if hi >= e * (1 + sp) else c) * (1 + slip) / e - 1) - 2 * cs
            for sp in (0.15, 0.25)}


def on_session_close(st: dict, day: date, sel: dict, spyg: float, fetch, daily_feats, session_parts) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    key = day.isoformat()
    L = st.setdefault("learn", {"champion": CHAMPION, "since_change": 0, "journal": [], "last_train": None})
    cands = sel.get("cands") or []
    data = fetch([s for s, _ in cands]) if cands else {}
    rows = []
    for s, gap in cands:
        if s not in data:
            continue
        _, reg, _ = session_parts(data[s], day)
        out = outcome(reg)
        if out is None:
            continue
        try:
            f = daily_feats(s, day)
        except Exception:  # noqa: BLE001
            continue
        rows.append({"day": key, "sym": s, "gap": gap, "spyg": spyg, **f, **out})
    C = load_live("candidates.csv")
    if rows:
        new = add_features(pd.DataFrame(rows))
        C = pd.concat([C[C["day"] != key] if len(C) else C, new], ignore_index=True)
        C.round(6).to_csv(_csv("candidates.csv"), index=False)
    # résultats fantômes des variantes pour la séance
    model = load_model()
    today = C[C["day"] == key] if len(C) else pd.DataFrame(columns=["day"] + FEATS + ["r15", "r25"])
    vrow = {name: float(variant_returns(today, v, [key], model).iloc[0]) for name, v in all_variants().items()}
    V = load_live("variants.csv")
    V = pd.concat([V[V["day"] != key] if len(V) else V, pd.DataFrame([{"day": key, **vrow}])], ignore_index=True)
    V.to_csv(_csv("variants.csv"), index=False)
    # réentraînement hebdomadaire sur l'historique + le direct
    n_live = len(V)
    if L.get("last_train") is None or n_live - L.get("trained_at_n", 0) >= RETRAIN_EVERY:
        base = seed_candidates()
        data_all = pd.concat([base, C], ignore_index=True) if len(C) else base
        if len(data_all) > 50:
            m = train(data_all)
            _csv("model.json").write_text(json.dumps(m))
            L.update(last_train=key, trained_at_n=n_live, model_rows=m["rows"])
    # décision champion / challenger
    L["since_change"] = L.get("since_change", 0) + 1
    live = V.set_index("day")
    champ, entry = decide(L["champion"], live, seed_variants(), L["since_change"])
    if entry:
        entry["day"] = key
        L["journal"].append(entry)
        L["champion"], L["since_change"] = champ, 0
    L["sessions"] = n_live
    L["live_rows"] = int(len(C))


def champion_config(st: dict) -> dict:
    """Paramètres du champion à appliquer à la configuration du bot."""
    name = st.get("learn", {}).get("champion", CHAMPION)
    v = all_variants().get(name, VARIANTS[CHAMPION])
    return {"top_n": v["n"], "weight": 1 / v["n"], "stop_pct": v["stop"], "min_gap": v["gap"],
            "min_dvol": v["dvol"], "model": bool(v.get("model"))}


def summary(st: dict) -> dict:
    L = st.get("learn", {})
    V = load_live("variants.csv")
    H = seed_variants()
    board = []
    for name in all_variants():
        live = V[name] if name in V else pd.Series(dtype=float)
        hist = H[name] if name in H else pd.Series(dtype=float)
        board.append({"name": name, "live_ret": float((1 + live).prod() - 1) if len(live) else None,
                      "hist_cagr": float((1 + hist).prod() ** (252 / len(hist)) - 1) if len(hist) else None,
                      "hist_sharpe": float(hist.mean() / hist.std() * math.sqrt(252)) if len(hist) and hist.std() else None})
    board.sort(key=lambda x: -(x["live_ret"] if x["live_ret"] is not None else -9) if len(V) else -(x["hist_sharpe"] or -9))
    model = load_model()
    top = None
    if model:
        top = sorted(zip(FEATS, model["w"]), key=lambda x: -abs(x[1]))[:5]
    return {"champion": L.get("champion", CHAMPION), "sessions": int(len(V)), "live_rows": L.get("live_rows", 0),
            "model_rows": L.get("model_rows"), "last_train": L.get("last_train"), "since_change": L.get("since_change", 0),
            "rules": {"min_live": MIN_LIVE, "cooldown": COOLDOWN, "t_live": T_LIVE, "dd_tol": DD_TOL},
            "journal": L.get("journal", [])[-10:],
            "board": board[:8] + [b for b in board[8:] if b["name"] == L.get("champion", CHAMPION)],
            "model_top": [[f, round(w, 5)] for f, w in top] if top else None}
