"""Player-name normalization so ranking sources join to Sleeper players.

Copied from Fantasy Bot's name_matching.py: sources disagree on suffixes,
periods, and apostrophes ("D.J. Moore" vs "DJ Moore", "Patrick Mahomes II").
"""
from __future__ import annotations

import re
import unicodedata

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")
_MULTI_SPACE_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = ascii_name.lower()
    no_periods = lowered.replace(".", "").replace("'", "").replace("’", "")
    no_punct = _NON_ALNUM_RE.sub(" ", no_periods)
    tokens = [t for t in _MULTI_SPACE_RE.split(no_punct.strip()) if t]
    tokens = [t for t in tokens if t not in _SUFFIXES]
    return " ".join(tokens)
