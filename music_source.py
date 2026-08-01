"""
Recupero brani da due fonti:
- Spotify (Client Credentials Flow) per playlist / metadati.
  NB: da policy recenti Spotify richiede che l'account proprietario
  dell'app abbia un abbonamento Premium per poter leggere dati via API;
  senza Premium le richieste falliscono con 403.
- Deezer (API pubblica, nessuna autenticazione richiesta) per playlist
  Deezer dirette, per il fallback audio quando manca il preview Spotify,
  e per la modalità "casuale" (chart Deezer), che quindi funziona sempre
  anche senza Spotify Premium.
"""

import os
import random
import re

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


def extract_deezer_playlist_id(source: str) -> str:
    """Estrae l'ID numerico da un link tipo
    https://www.deezer.com/it/playlist/1282495565 (o varianti con altre lingue/query string)."""
    match = re.search(r"playlist/(\d+)", source)
    if match:
        return match.group(1)
    return source.strip()


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


def fetch_deezer_chart_tracks(limit: int = 50) -> list[dict]:
    """Pesca canzoni dalla classifica globale Deezer (chart), utile per la
    modalità 'casuale': nessuna autenticazione richiesta, sempre disponibile."""
    tracks: list[dict] = []
    url = "https://api.deezer.com/chart/0/tracks"

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


def fetch_tracks_from_source(source: str, spotify_provider: "SpotifyProvider | None" = None) -> list[dict]:
    """Dispatcher: riconosce se il link è Deezer o Spotify e usa la fonte giusta."""
    if is_deezer_source(source):
        return fetch_deezer_playlist_tracks(source)

    if is_spotify_source(source) or spotify_provider is not None:
        if spotify_provider is None:
            raise RuntimeError(
                "Playlist Spotify richiesta ma le credenziali Spotify non sono configurate."
            )
        return spotify_provider.fetch_tracks(source)

    raise RuntimeError(
        "Non riconosco questo link: incolla un link Spotify o Deezer valido."
    )


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
