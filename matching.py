"""
Normalization and "lenient" matching for the music quiz.

Automatically handles:
- upper/lowercase and accents
- content in parentheses/brackets (e.g. "(feat. Someone)", "[Radio Edit]")
- noise words: feat, ft, featuring, remix, remaster, version, live, radio edit, explicit
- assorted punctuation
- small typos (fuzzy matching with configurable threshold)
"""

import re
import unicodedata

from rapidfuzz import fuzz

# Patterns to strip before comparison (case-insensitive, applied in order)
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
    """True if 'guess' is close enough to 'target' to be considered correct."""
    g = normalize(guess)
    t = normalize(target)
    if not g or not t:
        return False
    if g == t:
        return True
    # Containment match (useful if someone only types part of the title/artist)
    if len(g) >= 3 and (g in t or t in g):
        return True
    score = fuzz.token_sort_ratio(g, t)
    return score >= threshold


def any_artist_match(guess: str, artists: list[str], threshold: int = 82) -> bool:
    """True if 'guess' matches any of the artists (main or featured)."""
    return any(is_close_match(guess, artist, threshold) for artist in artists)
