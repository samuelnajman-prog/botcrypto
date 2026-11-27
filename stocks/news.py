"""Scan d'actualité des candidats : titres Yahoo Finance (RSS public) classés par thème.

Règle de trading appliquée : un titre qui monte sur une annonce de rachat / fusion (OPA, offre
en numéraire, accord définitif...) est EXCLU des ventes, car son cours se colle au prix de l'offre
(pas de reflux) et une surenchère peut le faire bondir. Les autres thèmes sont affichés pour
information (résultats, analystes, FDA, émission d'actions...).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import data as D

RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=US&lang=en-US"

TAGS = [
    ("rachat", re.compile(r"\b(to acquire|acquires?|acquired by|acquisition of|merger|merge with|buyout|"
                          r"take[- ]private|takeover|tender offer|definitive agreement|all[- ]cash|"
                          r"per share in cash|to be acquired|go[- ]private|bid for)\b", re.I)),
    ("résultats", re.compile(r"\b(earnings|results|quarter|q[1-4]\b|revenue|eps|guidance|outlook|beats?|misses?)\b", re.I)),
    ("analyste", re.compile(r"\b(upgrade[sd]?|downgrade[sd]?|price target|initiates?|overweight|outperform|buy rating)\b", re.I)),
    ("santé/FDA", re.compile(r"\b(fda|phase [123i]+|trial|approval|clinical)\b", re.I)),
    ("capital", re.compile(r"\b(offering|private placement|convertible|buyback|repurchase|dividend|split)\b", re.I)),
    ("contrat", re.compile(r"\b(contract|partnership|deal with|agreement with|order from|wins?)\b", re.I)),
    ("cotation", re.compile(r"\b(halt(ed)?|delist|nasdaq notice|s&p 500 inclusion|index inclusion|added to the s&p)\b", re.I)),
]
EXCLUDE = {"rachat"}


def tag(title: str, desc: str = "") -> list[str]:
    text = f"{title} {desc}"
    return [name for name, rx in TAGS if rx.search(text)]


def headlines(sym: str, since: datetime, until: datetime, limit: int = 5) -> list[dict]:
    """Titres publiés entre `since` et `until` (UTC) qui mentionnent le ticker ou la société."""
    try:
        raw = D.http_get(RSS % sym.replace(".", "-"), timeout=15, tries=2)
        root = ET.fromstring(raw)
    except Exception:  # noqa: BLE001 — le scan d'actualité ne doit jamais bloquer le bot
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = (it.findtext("description") or "").strip()
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(timezone.utc)
        except Exception:  # noqa: BLE001
            continue
        if not (since <= ts <= until):
            continue
        out.append({"t": ts.isoformat(timespec="minutes"), "title": title[:160],
                    "link": (it.findtext("link") or "")[:300], "tags": tag(title, desc)})
    out.sort(key=lambda x: x["t"], reverse=True)
    return out[:limit]


def verdict(items: list[dict]) -> tuple[bool, list[str]]:
    """(exclure ?, thèmes vus)."""
    seen = sorted({t for it in items for t in it["tags"]})
    return bool(EXCLUDE & set(seen)), seen
