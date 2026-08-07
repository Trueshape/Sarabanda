"""
Track fetching, Deezer-only: the public Deezer API needs no authentication
and is the one source that's reliably reachable, used for "random" mode
(chart, optionally filtered by genre/category), special categories
(decades, Anime, Film, Games, etc. via playlist search), and artist-only
quizzes (top tracks + album deep cuts).
"""

import random
import re
import time

import requests
from rapidfuzz import fuzz

# "Special" categories that don't map to a direct Deezer genre: resolved by
# searching for a relevant public Deezer playlist via the playlist search API
# (no fragile hardcoded IDs).
SPECIAL_CATEGORIES = {
    "decade_80s": {"label": "80s", "query": "80s hits"},
    "decade_90s": {"label": "90s", "query": "90s hits"},
    "decade_00s": {"label": "2000s", "query": "2000s hits"},
    "decade_10s": {"label": "2010s", "query": "2010s hits"},
    "decade_20s": {"label": "2020s", "query": "2020s hits"},
    "recent_hits": {"label": "Recent Hits", "query": "Hot Hits"},
    "trending_now": {"label": "Trending Now", "query": "Trending Now"},
    "jpop": {"label": "J-Pop", "query": "J-Pop"},
    "jrock": {"label": "J-Rock", "query": "J-Rock"},
    "kpop": {"label": "K-Pop", "query": "K-Pop"},
    "anime": {"label": "Anime", "query": "Anime"},
    # Deezer only exposes these as a single merged genre ("Film/Games"), so
    # they're split here into two real, separate quiz categories instead.
    "film": {"label": "Film", "query": "Movie Soundtracks"},
    "games": {"label": "Games", "query": "Video Game Music"},
}

# Deezer genre names that bundle Film and Games into one entry (varies by
# API locale, e.g. "Film/Games", "Film/Jeux Vidéo"). Excluded from the plain
# genre list since they're covered as two separate SPECIAL_CATEGORIES above.
# The plain "genre" slots in the `category` autocomplete are curated: only
# these show up (in this exact set), instead of whatever Deezer's full
# genre list happens to contain. Matched against the live Deezer names at
# request time (normalized, so hyphen/slash variants still match) rather
# than by hardcoded genre IDs, which can shift between API regions/updates.
GENRE_ALLOWLIST = [
    "Classical",
    "Pop",
    "Rap/Hip Hop",
    "Dance",
    "Rock",
    "Alternative",
    "Metal",
    "Electro",
    "Jazz",
]


def _normalize_genre_name(name: str) -> frozenset:
    return frozenset(name.lower().replace("-", " ").replace("/", " ").split())


_ALLOWED_GENRE_NORMALIZED = {_normalize_genre_name(g) for g in GENRE_ALLOWLIST}


def _is_allowed_genre(genre_name: str) -> bool:
    return _normalize_genre_name(genre_name) in _ALLOWED_GENRE_NORMALIZED


def extract_deezer_playlist_id(source: str) -> str:
    """Extracts the numeric ID from a link like
    https://www.deezer.com/en/playlist/1282495565 (or variants with other
    languages/query strings)."""
    match = re.search(r"playlist/(\d+)", source)
    if match:
        return match.group(1)
    return source.strip()


def fetch_deezer_playlist_tracks(playlist_url_or_id: str, limit: int = 300) -> list[dict]:
    """Reads a public Deezer playlist via the public API (no auth required).
    The mp3 preview is already included directly in the response, so no
    fallback lookup is needed."""
    playlist_id = extract_deezer_playlist_id(playlist_url_or_id)
    tracks: list[dict] = []
    url = f"https://api.deezer.com/playlist/{playlist_id}/tracks"

    while url and len(tracks) < limit:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()

        if "error" in payload:
            raise RuntimeError(payload["error"].get("message", "Deezer API error"))

        for item in payload.get("data", []):
            name = item.get("title")
            artist_name = (item.get("artist") or {}).get("name")
            preview_url = item.get("preview")
            if name and artist_name:
                tracks.append(
                    {"name": name, "artists": [artist_name], "preview_url": preview_url}
                )
            if len(tracks) >= limit:
                break

        url = payload.get("next")

    return tracks


_genre_cache: dict = {"data": None, "fetched_at": 0}
_GENRE_CACHE_TTL_SECONDS = 3600  # 1 hour, genres rarely change


def fetch_deezer_genres() -> list[dict]:
    """Returns the curated list of music genres (see GENRE_ALLOWLIST) with
    their real Deezer IDs: [{'id': 132, 'name': 'Pop'}, ...]. Result cached
    for one hour."""
    now = time.time()
    if _genre_cache["data"] is not None and (now - _genre_cache["fetched_at"]) < _GENRE_CACHE_TTL_SECONDS:
        return _genre_cache["data"]

    resp = requests.get("https://api.deezer.com/genre", timeout=10)
    resp.raise_for_status()
    payload = resp.json()

    genres = [
        {"id": g["id"], "name": g["name"]}
        for g in payload.get("data", [])
        if g.get("id") and g.get("name") and _is_allowed_genre(g["name"])
    ]
    _genre_cache["data"] = genres
    _genre_cache["fetched_at"] = now
    return genres


def fetch_deezer_chart_tracks(limit: int = 50, genre_id: int = 0) -> list[dict]:
    """Fetches songs from the Deezer chart, used for "random" mode: no
    authentication required, always available. genre_id=0 (default) = global
    chart, no category filter."""
    tracks: list[dict] = []
    url = f"https://api.deezer.com/chart/{genre_id}/tracks"

    while url and len(tracks) < limit:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()

        for item in payload.get("data", []):
            name = item.get("title")
            artist_name = (item.get("artist") or {}).get("name")
            preview_url = item.get("preview")
            if name and artist_name:
                tracks.append(
                    {"name": name, "artists": [artist_name], "preview_url": preview_url}
                )
            if len(tracks) >= limit:
                break

        url = payload.get("next")

    random.shuffle(tracks)
    return tracks


def fetch_deezer_varied_random_tracks(limit: int = 50, extra_charts: int = 3) -> list[dict]:
    """"Random" mode, Deezer-only, with more variety: combines the global
    chart with a few charts from randomly-picked genres, instead of always
    returning the same global top list. Everything comes from Deezer's
    public API (no auth needed, always available)."""
    combined: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(items: list[dict]):
        for t in items:
            key = (t["name"].lower(), t["artists"][0].lower())
            if key not in seen:
                seen.add(key)
                combined.append(t)

    try:
        _add(fetch_deezer_chart_tracks(limit=limit, genre_id=0))
    except Exception:
        pass

    try:
        genres = [g for g in fetch_deezer_genres() if g["id"] != 0]
    except Exception:
        genres = []

    if genres:
        chosen_genres = random.sample(genres, k=min(extra_charts, len(genres)))
        for genre in chosen_genres:
            try:
                _add(fetch_deezer_chart_tracks(limit=max(limit // 2, 10), genre_id=genre["id"]))
            except Exception:
                continue

    random.shuffle(combined)
    return combined[:limit] if limit else combined


def fetch_random_tracks_by_category(genre_name: str, genre_id: int, limit: int = 50) -> list[dict]:
    """Builds a varied track pool for a chosen category, using two
    independent Deezer sources so results aren't just "the genre chart every
    time": the Deezer chart filtered by genre, plus a public Deezer playlist
    found by searching for the genre name. Deduplicates by title+artist and
    shuffles the final result. Deezer-only,
    since it's the one source that's always reliably reachable."""
    chart_share = max(int(limit * 0.6), 1)
    playlist_share = max(limit - chart_share, 1)

    combined: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(items: list[dict]):
        for t in items:
            key = (t["name"].lower(), t["artists"][0].lower())
            if key not in seen:
                seen.add(key)
                combined.append(t)

    try:
        _add(fetch_deezer_chart_tracks(limit=chart_share, genre_id=genre_id))
    except Exception:
        pass

    try:
        _add(fetch_deezer_playlist_by_search(genre_name, limit_tracks=playlist_share))
    except Exception:
        pass

    # Small extra variety pass with a slightly different search phrasing, in
    # case the plain genre name mostly surfaced the same playlist as above.
    if len(combined) < limit:
        try:
            _add(fetch_deezer_playlist_by_search(f"{genre_name} hits", limit_tracks=limit - len(combined)))
        except Exception:
            pass

    random.shuffle(combined)
    return combined[:limit] if limit else combined


def fetch_deezer_playlist_by_search(query: str, limit_tracks: int = 50) -> list[dict]:
    """Searches for a relevant public Deezer playlist for the given term and
    reads its tracks. Used for "special" categories (decades, trending,
    J-Pop/J-Rock/K-Pop, anime) that don't map to a direct Deezer genre."""
    resp = requests.get(
        "https://api.deezer.com/search/playlist",
        params={"q": query, "limit": 5},
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()

    results = payload.get("data", [])
    if not results:
        raise RuntimeError(f"No playlist found for '{query}'.")

    best = results[0]
    playlist_id = str(best.get("id"))
    return fetch_deezer_playlist_tracks(playlist_id, limit=limit_tracks)


def fetch_special_category_tracks(key: str, limit: int = 50) -> list[dict]:
    """Resolves a special category (e.g. 'decade_80s', 'kpop', 'anime') into
    its corresponding track list, by searching for a relevant Deezer playlist."""
    info = SPECIAL_CATEGORIES.get(key)
    if not info:
        raise RuntimeError("Unrecognized special category.")
    return fetch_deezer_playlist_by_search(info["query"], limit_tracks=limit)


def search_deezer_artists(query: str, limit: int = 15) -> list[dict]:
    """Searches artists on Deezer. Returns [{'id': ..., 'name': ...}, ...]."""
    resp = requests.get(
        "https://api.deezer.com/search/artist",
        params={"q": query, "limit": limit},
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    return [
        {"id": a["id"], "name": a["name"]}
        for a in payload.get("data", [])
        if a.get("id") and a.get("name")
    ]


def resolve_artist_fuzzy(query: str) -> dict | None:
    """Finds the closest Deezer artist to the typed text, tolerating typos
    and punctuation (e.g. 'Evanescene' -> 'Evanescence'), using Deezer
    search for the candidate pool and rapidfuzz to pick the best match even
    when the text doesn't match exactly."""
    candidates = search_deezer_artists(query, limit=15)
    if not candidates:
        return None
    best = max(candidates, key=lambda c: fuzz.token_sort_ratio(query.lower(), c["name"].lower()))
    return best


def fetch_deezer_artist_top_tracks(artist_id: int, limit: int = 100) -> list[dict]:
    """Reads an artist's most popular tracks on Deezer, paginating through
    Deezer's 'next' links so we're not capped at whatever the API's default
    page size is."""
    tracks: list[dict] = []
    url = f"https://api.deezer.com/artist/{artist_id}/top?limit=50"

    while url and len(tracks) < limit:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()

        for item in payload.get("data", []):
            name = item.get("title")
            artist_name = (item.get("artist") or {}).get("name")
            preview_url = item.get("preview")
            if name and artist_name:
                tracks.append({"name": name, "artists": [artist_name], "preview_url": preview_url})
            if len(tracks) >= limit:
                break

        url = payload.get("next")

    return tracks


def fetch_deezer_artist_albums(artist_id: int, limit: int = 30) -> list[dict]:
    """Reads an artist's albums on Deezer. Skips compilations ('compile'
    record type), since those are multi-artist collections and would let
    other artists' songs sneak into an artist-only quiz."""
    albums: list[dict] = []
    url = f"https://api.deezer.com/artist/{artist_id}/albums?limit=50"

    while url and len(albums) < limit:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()

        for item in payload.get("data", []):
            if item.get("record_type") == "compile":
                continue
            album_id = item.get("id")
            if album_id:
                albums.append({"id": album_id})
            if len(albums) >= limit:
                break

        url = payload.get("next")

    return albums


def fetch_deezer_album_tracks(album_id: int, artist_name: str) -> list[dict]:
    """Reads an album's tracks on Deezer, keeping only the tracks whose main
    artist matches `artist_name` (so a feature-heavy or various-artists
    album doesn't leak other artists' songs into the pool)."""
    resp = requests.get(f"https://api.deezer.com/album/{album_id}/tracks", timeout=10)
    resp.raise_for_status()
    payload = resp.json()

    target = artist_name.strip().lower()
    tracks = []
    for item in payload.get("data", []):
        name = item.get("title")
        track_artist = (item.get("artist") or {}).get("name")
        preview_url = item.get("preview")
        if not name or not track_artist:
            continue
        if track_artist.strip().lower() != target:
            continue
        tracks.append({"name": name, "artists": [track_artist], "preview_url": preview_url})
    return tracks


def fetch_tracks_by_artist_name(query: str, limit: int = 50) -> list[dict]:
    """Resolves the typed artist name (even with typos) and returns a
    varied pool of ONLY that artist's songs (top tracks + deep cuts from
    their studio albums), so that when the user asks for one artist, every
    song played AND every multiple-choice decoy is guaranteed to be by that
    same artist."""
    artist = resolve_artist_fuzzy(query)
    if not artist:
        raise RuntimeError(f"No artist found for '{query}'.")

    target = artist["name"].strip().lower()
    combined: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(items: list[dict]):
        for t in items:
            if t["artists"][0].strip().lower() != target:
                continue
            key = (t["name"].lower(), t["artists"][0].lower())
            if key not in seen:
                seen.add(key)
                combined.append(t)

    try:
        _add(fetch_deezer_artist_top_tracks(artist["id"], limit=max(limit, 100)))
    except Exception:
        pass

    # Pull in deep cuts from their studio albums too, for real variety
    # beyond just the "top tracks" chart, but only once we still need more.
    if len(combined) < limit:
        try:
            albums = fetch_deezer_artist_albums(artist["id"], limit=25)
        except Exception:
            albums = []
        for album in albums:
            if len(combined) >= max(limit * 2, 60):
                break
            try:
                _add(fetch_deezer_album_tracks(album["id"], artist["name"]))
            except Exception:
                continue

    if not combined:
        raise RuntimeError(f"No tracks found for artist '{artist['name']}'.")

    random.shuffle(combined)
    return combined


def deezer_preview_for(title: str, artist: str) -> str | None:
    """Searches Deezer for the track and returns the 30s mp3 preview URL, or
    None. Only accepts a result whose artist actually matches the one we
    asked for (fuzzy, to tolerate minor naming differences) — otherwise a
    same-titled cover/song by a different artist could slip in and break an
    artist-only quiz."""
    session_timeout = 8
    target = artist.strip().lower()

    def _best_matching_preview(data: list[dict]) -> str | None:
        for item in data:
            item_artist = (item.get("artist") or {}).get("name", "")
            if fuzz.token_sort_ratio(target, item_artist.strip().lower()) >= 85:
                preview = item.get("preview")
                if preview:
                    return preview
        return None

    # Attempt 1: structured query
    try:
        resp = requests.get(
            "https://api.deezer.com/search",
            params={"q": f'track:"{title}" artist:"{artist}"'},
            timeout=session_timeout,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        preview = _best_matching_preview(data)
        if preview:
            return preview
    except requests.RequestException:
        pass

    # Attempt 2: looser, free-text query
    try:
        resp = requests.get(
            "https://api.deezer.com/search",
            params={"q": f"{title} {artist}"},
            timeout=session_timeout,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        preview = _best_matching_preview(data)
        if preview:
            return preview
    except requests.RequestException:
        pass

    return None


def resolve_playable_url(track: dict) -> str | None:
    """Returns a playable mp3 URL for the track (Deezer preview, already
    included when the track was fetched, with a Deezer search fallback if
    it's missing)."""
    if track.get("preview_url"):
        return track["preview_url"]
    return deezer_preview_for(track["name"], track["artists"][0])
