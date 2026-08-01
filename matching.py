"""
Normalizzazione e matching "permissivo" per il quiz musicale.

Gestisce automaticamente:
- maiuscole/minuscole e accenti
- contenuto tra parentesi/quadre (es. "(feat. Tizio)", "[Radio Edit]")
- parole di rumore: feat, ft, featuring, remix, remaster, version, live, radio edit, explicit
- punteggiatura varia
- piccoli refusi (fuzzy matching con soglia configurabile)
"""

import re
import unicodedata

from rapidfuzz import fuzz

# Pattern da rimuovere prima del confronto (case-insensitive, applicati in ordine)
_NOISE_PATTERNS = [
    r"\(.*?\)",
    r"\[.*?\]",
    r"\bfeat\.?\b.*",
    r"\bft\.?\b.*",
    r"\bfeaturing\b.*",
    r"\bremix\b",
    r"\bremaster(ed)?\b",
    r"\bradio edit\b",
    r"\bexplicit\b",
    r"\blive\b",
    r"\bversion\b",
    r"\bedit\b",
    r"-\s*$",
]

_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), flags=re.IGNORECASE)


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = _strip_accents(text)
    text = _NOISE_RE.sub(" ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_close_match(guess: str, target: str, threshold: int = 82) -> bool:
    """True se 'guess' è abbastanza vicino a 'target' da considerarsi corretto."""
    g = normalize(guess)
    t = normalize(target)
    if not g or not t:
        return False
    if g == t:
        return True
    # Match per contenimento (utile se uno scrive solo parte del titolo/artista)
    if len(g) >= 3 and (g in t or t in g):
        return True
    score = fuzz.token_sort_ratio(g, t)
    return score >= threshold


def any_artist_match(guess: str, artists: list[str], threshold: int = 82) -> bool:
    """True se 'guess' matcha uno qualsiasi degli artisti (principale o featuring)."""
    return any(is_close_match(guess, artist, threshold) for artist in artists)
