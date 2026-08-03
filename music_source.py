"""
Track fetching from three sources:
- Spotify (Client Credentials Flow) for playlists / single tracks / metadata.
  NOTE: due to a recent Spotify policy change, the account that owns the app
  needs an active Premium subscription to read data via the API; without
  Premium, requests fail with a 403.
- Deezer (public API, no authentication required) for Deezer playlists and
  single tracks, for the audio fallback when Spotify has no preview, and
  for "random" mode (Deezer chart, optionally filtered by genre/category).
- iTunes/Apple (public Lookup API, no authentication required) for single
  iTunes/Apple Music tracks.
"""

import os
import random
import re
import time

import requests
import spotipy
from rapidfuzz import fuzz
from spotipy.oauth2 import SpotifyClientCredentials

# "Special" categories that don't map to a direct Deezer genre: resolved by
# searching for a relevant public Deezer playlist via the playlist search API
# (no fragile hardcoded IDs).
SPECIAL_CATEGORIES = {
    "decade_70s": {"label": "70s", "query": "70s hits"},
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
}

# Pool of public Spotify editorial playlists, used to pick "random" songs
# when the user doesn't provide their own playlist. These are stable
# official Spotify playlist IDs covering different genres/eras for variety.
# If one of these IDs stops being valid in the future, it's simply skipped
# (try/except per playlist).
RANDOM_POOL_PLAYLISTS = [
    "37i9dQZF1DXcBWIGoYBM5M",  # Today's Top Hits
    "37i9dQZEVXbMDoHDwVN2tF",  # Top 50 Global
    "37i9dQZF1DX5Ejj0EkURtP",  # All Out 2010s
    "37i9dQZF1DX4UtSsGT1Sbe",  # All Out 80s
    "37i9dQZF1DXbTxeAdrVG2l",  # All Out 90s
    "37i9dQZF1DX0XUsuxWHRQd",  # RapCaviar
    "37i9dQZF1DWXRqgorJj26U",  # Rock Classics
    "37i9dQZF1DX10zKzsJ2jva",  # Viva Latino
]


class SpotifyProvider:
    def __init__(self):
        client_id = os.getenv("SPOTIFY_CLIENT_ID")
        client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise RuntimeError(
                "SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET missing from the .env file"
            )
        auth_manager = SpotifyClientCredentials(
            client_id=client_id, client_secret=client_secret
        )
        self.sp = spotipy.Spotify(client_credentials_manager=auth_manager)

    @staticmethod
    def extract_playlist_id(playlist_url_or_id: str) -> str:
        if "playlist/" in playlist_url_or_id:
            return playlist_url_or_id.split("playlist/")[1].split("?")[0]
        return playlist_url_or_id.strip()

    def fetch_tracks(self, playlist_url_or_id: str, limit: int = 300) -> list[dict]:
        """Returns a list of dicts: {name, artists: [...], preview_url}."""
        playlist_id = self.extract_playlist_id(playlist_url_or_id)
        tracks = []
        results = self.sp.playlist_items(playlist_id, additional_types=["track"])

        while results:
            for item in results.get("items", []):
                t = item.get("track")
                if not t or t.get("is_local"):
                    continue
                name = t.get("name")
                artists = [a["name"] for a in t.get("artists", []) if a.get("name")]
                preview_url = t.get("preview_url")
                if name and artists:
                    tracks.append(
                        {"name": name, "artists": artists, "preview_url": preview_url}
                    )
                if len(tracks) >= limit:
                    break

            if len(tracks) >= limit or not results.get("next"):
                break
            results = self.sp.next(results)

        return tracks

    @staticmethod
    def extract_track_id(track_url_or_id: str) -> str:
        if "track/" in track_url_or_id:
            return track_url_or_id.split("track/")[1].split("?")[0]
        return track_url_or_id.strip()

    def fetch_single_track(self, track_url_or_id: str) -> dict:
        """Reads a single Spotify track. Returns {name, artists, preview_url}."""
        track_id = self.extract_track_id(track_url_or_id)
        t = self.sp.track(track_id)
        name = t.get("name")
        artists = [a["name"] for a in t.get("artists", []) if a.get("name")]
        preview_url = t.get("preview_url")
        if not name or not artists:
            raise RuntimeError("Invalid or not-found Spotify track.")
        return {"name": name, "artists": artists, "preview_url": preview_url}

    def fetch_random_tracks(self, limit: int = 30, source_playlists: int = 3) -> list[dict]:
        """Picks random songs from a pool of Spotify editorial playlists
        (various eras/genres), useful when the user doesn't specify their
        own playlist. Returns a shuffled list of unique tracks."""
        chosen_playlists = random.sample(
            RANDOM_POOL_PLAYLISTS, k=min(source_playlists, len(RANDOM_POOL_PLAYLISTS))
        )

        pool: list[dict] = []
        seen = set()
        for playlist_id in chosen_playlists:
            try:
                playlist_tracks = self.fetch_tracks(playlist_id, limit=100)
            except Exception:
                continue
            for t in playlist_tracks:
                key = (t["name"].lower(), t["artists"][0].lower())
                if key not in seen:
                    seen.add(key)
                    pool.append(t)

        random.shuffle(pool)
        return pool[:limit] if limit else pool


def is_deezer_source(source: str) -> bool:
    return "deezer.com" in source.lower()


def is_spotify_source(source: str) -> bool:
    return "spotify.com" in source.lower()


def is_itunes_source(source: str) -> bool:
    return "music.apple.com" in source.lower() or "itunes.apple.com" in source.lower()


def extract_itunes_track_id(source: str) -> str:
    """Extracts the track ID from an Apple Music/iTunes link. Handles both
    the query-string format ?i=ID (song inside an album context) and the
    format where the ID is the last path segment (direct song link)."""
    match = re.search(r"[?&]i=(\d+)", source)
    if match:
        return match.group(1)
    match = re.search(r"/(\d+)(?:\?|$)", source)
    if match:
        return match.group(1)
    return source.strip()


def fetch_itunes_single_track(track_url_or_id: str) -> dict:
    """Reads a single iTunes/Apple Music track via the public Lookup API
    (no authentication required)."""
    track_id = extract_itunes_track_id(track_url_or_id)
    resp = requests.get(
        "https://itunes.apple.com/lookup",
        params={"id": track_id, "entity": "song"},
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()

    results = payload.get("results", [])
    if not results:
        raise RuntimeError("iTunes track not found.")

    item = results[0]
    name = item.get("trackName")
    artist_name = item.get("artistName")
    preview_url = item.get("previewUrl")
    if not name or not artist_name:
        raise RuntimeError("Invalid iTunes track.")
    return {"name": name, "artists": [artist_name], "preview_url": preview_url}


def fetch_itunes_tracks_by_term(term: str, limit: int = 25, country: str = "US") -> list[dict]:
    """Searches iTunes with a free-text term (e.g. a genre name: 'Pop',
    'Rock', etc.). NOTE: this is not a true genre filter like Deezer's — the
    iTunes Search API doesn't reliably support that — but it's a useful
    approximation to add variety when a music category is selected."""
    resp = requests.get(
        "https://itunes.apple.com/search",
        params={
            "term": term,
            "entity": "song",
            "limit": min(limit, 200),
            "country": country,
        },
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()

    tracks = []
    for item in payload.get("results", []):
        name = item.get("trackName")
        artist_name = item.get("artistName")
        preview_url = item.get("previewUrl")
        if name and artist_name:
            tracks.append({"name": name, "artists": [artist_name], "preview_url": preview_url})
    return tracks


def is_track_url(source: str) -> bool:
    """True if the link points to a single track (not a playlist)."""
    return "track/" in source.lower()


def extract_deezer_playlist_id(source: str) -> str:
    """Extracts the numeric ID from a link like
    https://www.deezer.com/en/playlist/1282495565 (or variants with other
    languages/query strings)."""
    match = re.search(r"playlist/(\d+)", source)
    if match:
        return match.group(1)
    return source.strip()


def extract_deezer_track_id(source: str) -> str:
    """Extracts the numeric ID from a link like
    https://www.deezer.com/en/track/123456789."""
    match = re.search(r"track/(\d+)", source)
    if match:
        return match.group(1)
    return source.strip()


def fetch_deezer_single_track(track_url_or_id: str) -> dict:
    """Reads a single Deezer track via the public API (no auth required)."""
    track_id = extract_deezer_track_id(track_url_or_id)
    resp = requests.get(f"https://api.deezer.com/track/{track_id}", timeout=10)
    resp.raise_for_status()
    payload = resp.json()

    if "error" in payload:
        raise RuntimeError(payload["error"].get("message", "Deezer API error"))

    name = payload.get("title")
    artist_name = (payload.get("artist") or {}).get("name")
    preview_url = payload.get("preview")
    if not name or not artist_name:
        raise RuntimeError("Invalid or not-found Deezer track.")
    return {"name": name, "artists": [artist_name], "preview_url": preview_url}


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
    """Returns the real list of music categories available on Deezer:
    [{'id': 132, 'name': 'Pop'}, ...]. Result cached for one hour."""
    now = time.time()
    if _genre_cache["data"] is not None and (now - _genre_cache["fetched_at"]) < _GENRE_CACHE_TTL_SECONDS:
        return _genre_cache["data"]

    resp = requests.get("https://api.deezer.com/genre", timeout=10)
    resp.raise_for_status()
    payload = resp.json()

    genres = [
        {"id": g["id"], "name": g["name"]}
        for g in payload.get("data", [])
        if g.get("id") and g.get("name") and g["name"].lower() != "all"
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


def fetch_random_tracks_by_category(genre_name: str, genre_id: int, limit: int = 50) -> list[dict]:
    """Combines the Deezer chart filtered by genre (primary, reliable
    source) with an iTunes search for the same category name (secondary,
    supporting source, for extra variety). Deduplicates by title+artist and
    shuffles the final result."""
    deezer_share = max(int(limit * 0.7), 1)
    itunes_share = max(limit - deezer_share, 1)

    combined: list[dict] = []
    seen: set[tuple[str, str]] = set()

    try:
        deezer_tracks = fetch_deezer_chart_tracks(limit=deezer_share, genre_id=genre_id)
    except Exception:
        deezer_tracks = []
    for t in deezer_tracks:
        key = (t["name"].lower(), t["artists"][0].lower())
        if key not in seen:
            seen.add(key)
            combined.append(t)

    try:
        itunes_tracks = fetch_itunes_tracks_by_term(genre_name, limit=itunes_share)
    except Exception:
        itunes_tracks = []
    for t in itunes_tracks:
        key = (t["name"].lower(), t["artists"][0].lower())
        if key not in seen:
            seen.add(key)
            combined.append(t)

    random.shuffle(combined)
    return combined


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


def fetch_deezer_artist_top_tracks(artist_id: int, limit: int = 50) -> list[dict]:
    """Reads an artist's most popular tracks on Deezer."""
    resp = requests.get(
        f"https://api.deezer.com/artist/{artist_id}/top",
        params={"limit": limit},
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()

    tracks = []
    for item in payload.get("data", []):
        name = item.get("title")
        artist_name = (item.get("artist") or {}).get("name")
        preview_url = item.get("preview")
        if name and artist_name:
            tracks.append({"name": name, "artists": [artist_name], "preview_url": preview_url})
    return tracks


def fetch_tracks_by_artist_name(query: str, limit: int = 50) -> list[dict]:
    """Resolves the typed artist name (even with typos) and returns their
    most popular tracks."""
    artist = resolve_artist_fuzzy(query)
    if not artist:
        raise RuntimeError(f"No artist found for '{query}'.")
    tracks = fetch_deezer_artist_top_tracks(artist["id"], limit=limit)
    if not tracks:
        raise RuntimeError(f"No tracks found for artist '{artist['name']}'.")
    return tracks


def _fetch_single_link(source: str, spotify_provider: "SpotifyProvider | None") -> list[dict]:
    """Resolves a single link (playlist or track) into its corresponding track list."""
    if is_deezer_source(source):
        if is_track_url(source):
            return [fetch_deezer_single_track(source)]
        return fetch_deezer_playlist_tracks(source)

    if is_itunes_source(source):
        # iTunes/Apple Music: currently we only support single tracks
        # (there's no reliable way to read a user playlist from iTunes via
        # a public, unauthenticated API).
        return [fetch_itunes_single_track(source)]

    if is_spotify_source(source) or spotify_provider is not None:
        if spotify_provider is None:
            raise RuntimeError(
                "Spotify link requested but Spotify credentials are not configured."
            )
        if is_track_url(source):
            return [spotify_provider.fetch_single_track(source)]
        return spotify_provider.fetch_tracks(source)

    raise RuntimeError(
        "I don't recognize this link: paste a valid Spotify, Deezer, or iTunes/Apple Music link."
    )


def fetch_tracks_from_source(source: str, spotify_provider: "SpotifyProvider | None" = None) -> list[dict]:
    """Dispatcher: recognizes whether the link is Deezer or Spotify, a
    playlist or a single track, and supports multiple links separated by
    commas (useful for building a custom mini-quiz without creating a
    dedicated playlist)."""
    links = [part.strip() for part in source.split(",") if part.strip()]
    if not links:
        raise RuntimeError("No valid link provided.")

    all_tracks: list[dict] = []
    errors: list[str] = []
    for link in links:
        try:
            all_tracks.extend(_fetch_single_link(link, spotify_provider))
        except Exception as e:
            errors.append(f"{link}: {e}")

    if not all_tracks:
        raise RuntimeError("; ".join(errors) if errors else "No tracks found.")

    return all_tracks


def deezer_preview_for(title: str, artist: str) -> str | None:
    """Searches Deezer for the track and returns the 30s mp3 preview URL, or None."""
    session_timeout = 8

    # Attempt 1: structured query
    try:
        resp = requests.get(
            "https://api.deezer.com/search",
            params={"q": f'track:"{title}" artist:"{artist}"'},
            timeout=session_timeout,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if data:
            preview = data[0].get("preview")
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
        if data:
            preview = data[0].get("preview")
            if preview:
                return preview
    except requests.RequestException:
        pass

    return None


def resolve_playable_url(track: dict) -> str | None:
    """Returns a playable mp3 URL for the track, using Spotify if available
    and Deezer as a fallback."""
    if track.get("preview_url"):
        return track["preview_url"]
    return deezer_preview_for(track["name"], track["artists"][0])
