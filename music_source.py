"""
Recupero brani da tre fonti:
- Spotify (Client Credentials Flow) per playlist / brani singoli / metadati.
  NB: da policy recenti Spotify richiede che l'account proprietario
  dell'app abbia un abbonamento Premium per poter leggere dati via API;
  senza Premium le richieste falliscono con 403.
- Deezer (API pubblica, nessuna autenticazione richiesta) per playlist e
  brani singoli Deezer, per il fallback audio quando manca il preview
  Spotify, per la modalità "casuale" (chart Deezer, anche filtrata per
  categoria/genere musicale).
- iTunes/Apple (API pubblica Lookup, nessuna autenticazione richiesta) per
  brani singoli iTunes/Apple Music.
"""

import os
import random
import re
import time

import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# Pool di playlist editoriali pubbliche di Spotify, usate per pescare
# canzoni "casuali" quando l'utente non fornisce una playlist specifica.
# Sono ID stabili di playlist ufficiali Spotify, coprono generi/epoche diverse
# per dare varietà. Se in futuro uno di questi ID non fosse più valido,
# viene semplicemente ignorato (try/except per playlist).
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
                "SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET mancanti nel file .env"
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
        """Ritorna una lista di dict: {name, artists: [...], preview_url}."""
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
        """Legge un singolo brano Spotify. Ritorna un dict {name, artists, preview_url}."""
        track_id = self.extract_track_id(track_url_or_id)
        t = self.sp.track(track_id)
        name = t.get("name")
        artists = [a["name"] for a in t.get("artists", []) if a.get("name")]
        preview_url = t.get("preview_url")
        if not name or not artists:
            raise RuntimeError("Brano Spotify non valido o non trovato.")
        return {"name": name, "artists": artists, "preview_url": preview_url}

    def fetch_random_tracks(self, limit: int = 30, source_playlists: int = 3) -> list[dict]:
        """Pesca canzoni casuali da un pool di playlist editoriali Spotify
        (varie epoche/generi), utile quando l'utente non specifica una
        playlist propria. Ritorna una lista mescolata di brani unici."""
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
    """Estrae l'ID brano da un link Apple Music/iTunes. Gestisce sia il
    formato con query string ?i=ID (canzone dentro un album) sia il formato
    con l'ID come ultimo segmento del path (link diretto alla canzone)."""
    match = re.search(r"[?&]i=(\d+)", source)
    if match:
        return match.group(1)
    match = re.search(r"/(\d+)(?:\?|$)", source)
    if match:
        return match.group(1)
    return source.strip()


def fetch_itunes_single_track(track_url_or_id: str) -> dict:
    """Legge un singolo brano iTunes/Apple Music tramite la Lookup API
    pubblica (nessuna autenticazione richiesta)."""
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
        raise RuntimeError("Brano iTunes non trovato.")

    item = results[0]
    name = item.get("trackName")
    artist_name = item.get("artistName")
    preview_url = item.get("previewUrl")
    if not name or not artist_name:
        raise RuntimeError("Brano iTunes non valido.")
    return {"name": name, "artists": [artist_name], "preview_url": preview_url}


def fetch_itunes_tracks_by_term(term: str, limit: int = 25, country: str = "IT") -> list[dict]:
    """Cerca brani su iTunes tramite un termine libero (es. il nome di un
    genere: 'Pop', 'Rock', ecc.). NB: non è un vero filtro di genere come
    quello di Deezer — la Search API di iTunes non lo supporta in modo
    affidabile — ma un'approssimazione utile per aggiungere varietà quando
    si sceglie una categoria musicale."""
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
    """True se il link punta a un singolo brano (non a una playlist)."""
    return "track/" in source.lower()


def extract_deezer_playlist_id(source: str) -> str:
    """Estrae l'ID numerico da un link tipo
    https://www.deezer.com/it/playlist/1282495565 (o varianti con altre lingue/query string)."""
    match = re.search(r"playlist/(\d+)", source)
    if match:
        return match.group(1)
    return source.strip()


def extract_deezer_track_id(source: str) -> str:
    """Estrae l'ID numerico da un link tipo
    https://www.deezer.com/it/track/123456789."""
    match = re.search(r"track/(\d+)", source)
    if match:
        return match.group(1)
    return source.strip()


def fetch_deezer_single_track(track_url_or_id: str) -> dict:
    """Legge un singolo brano Deezer tramite l'API pubblica (nessuna auth richiesta)."""
    track_id = extract_deezer_track_id(track_url_or_id)
    resp = requests.get(f"https://api.deezer.com/track/{track_id}", timeout=10)
    resp.raise_for_status()
    payload = resp.json()

    if "error" in payload:
        raise RuntimeError(payload["error"].get("message", "Errore API Deezer"))

    name = payload.get("title")
    artist_name = (payload.get("artist") or {}).get("name")
    preview_url = payload.get("preview")
    if not name or not artist_name:
        raise RuntimeError("Brano Deezer non valido o non trovato.")
    return {"name": name, "artists": [artist_name], "preview_url": preview_url}


def fetch_deezer_playlist_tracks(playlist_url_or_id: str, limit: int = 300) -> list[dict]:
    """Legge una playlist Deezer pubblica tramite l'API pubblica (nessuna auth
    richiesta). Il preview mp3 è già incluso direttamente nella risposta,
    quindi non serve nessuna ricerca di fallback."""
    playlist_id = extract_deezer_playlist_id(playlist_url_or_id)
    tracks: list[dict] = []
    url = f"https://api.deezer.com/playlist/{playlist_id}/tracks"

    while url and len(tracks) < limit:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()

        if "error" in payload:
            raise RuntimeError(payload["error"].get("message", "Errore API Deezer"))

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
_GENRE_CACHE_TTL_SECONDS = 3600  # 1 ora, i generi cambiano raramente


def fetch_deezer_genres() -> list[dict]:
    """Ritorna la lista reale delle categorie musicali disponibili su Deezer:
    [{'id': 132, 'name': 'Pop'}, ...]. Risultato in cache per un'ora."""
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
    """Pesca canzoni dalla classifica Deezer (chart), utile per la modalità
    'casuale': nessuna autenticazione richiesta, sempre disponibile.
    genre_id=0 (default) = classifica globale, senza filtro di categoria."""
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
    """Combina la classifica Deezer filtrata per genere (fonte primaria e
    affidabile) con una ricerca iTunes per lo stesso nome di categoria
    (fonte secondaria, di supporto, per aggiungere varietà). Deduplica per
    titolo+artista e mescola il risultato finale."""
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


def _fetch_single_link(source: str, spotify_provider: "SpotifyProvider | None") -> list[dict]:
    """Risolve un singolo link (playlist o brano) nella lista di tracce corrispondente."""
    if is_deezer_source(source):
        if is_track_url(source):
            return [fetch_deezer_single_track(source)]
        return fetch_deezer_playlist_tracks(source)

    if is_itunes_source(source):
        # iTunes/Apple Music: al momento supportiamo solo il brano singolo
        # (non esiste un modo affidabile per leggere playlist utente da iTunes
        # tramite API pubblica non autenticata).
        return [fetch_itunes_single_track(source)]

    if is_spotify_source(source) or spotify_provider is not None:
        if spotify_provider is None:
            raise RuntimeError(
                "Link Spotify richiesto ma le credenziali Spotify non sono configurate."
            )
        if is_track_url(source):
            return [spotify_provider.fetch_single_track(source)]
        return spotify_provider.fetch_tracks(source)

    raise RuntimeError(
        "Non riconosco questo link: incolla un link Spotify, Deezer o iTunes/Apple Music valido."
    )


def fetch_tracks_from_source(source: str, spotify_provider: "SpotifyProvider | None" = None) -> list[dict]:
    """Dispatcher: riconosce se il link è Deezer o Spotify, playlist o brano singolo,
    e supporta più link separati da virgola (utile per costruire un mini-quiz
    con brani scelti a mano senza creare una playlist apposita)."""
    links = [part.strip() for part in source.split(",") if part.strip()]
    if not links:
        raise RuntimeError("Nessun link valido fornito.")

    all_tracks: list[dict] = []
    errors: list[str] = []
    for link in links:
        try:
            all_tracks.extend(_fetch_single_link(link, spotify_provider))
        except Exception as e:
            errors.append(f"{link}: {e}")

    if not all_tracks:
        raise RuntimeError("; ".join(errors) if errors else "Nessun brano trovato.")

    return all_tracks


def deezer_preview_for(title: str, artist: str) -> str | None:
    """Cerca il brano su Deezer e ritorna l'URL del preview mp3 (30s), o None."""
    session_timeout = 8

    # Tentativo 1: query strutturata
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

    # Tentativo 2: query libera, più permissiva
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
    """Ritorna un URL mp3 riproducibile per il brano, usando Spotify se disponibile
    e Deezer come fallback."""
    if track.get("preview_url"):
        return track["preview_url"]
    return deezer_preview_for(track["name"], track["artists"][0])
