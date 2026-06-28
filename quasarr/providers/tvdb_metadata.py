# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

"""TheTVDB v4 — mapping (saison, épisode) → numéro absolu.

Sonarr numérote les épisodes d'après TheTVDB. Pour traduire une demande
saisonnière (S2E5) vers l'index absolu utilisé par anime-sama, on lit donc
directement l'``absoluteNumber`` de TheTVDB (au lieu de l'estimer via TMDB),
ce qui élimine les décalages dus aux divergences de découpage entre bases.
"""

import threading
import time

import requests

from quasarr.providers.log import info, debug

_BASE = "https://api4.thetvdb.com/v4"
_TOKEN_TTL = 20 * 24 * 3600  # le JWT TheTVDB dure ~1 mois ; on rafraîchit avant

_lock = threading.Lock()
_token = {"value": None, "ts": 0.0}
_series_id_cache = {}    # imdb_id -> tvdb series id (ou None)
_episode_map_cache = {}  # series_id -> {(season, episode): absoluteNumber}


def _tvdb_key():
    from quasarr.storage.config import Config
    return Config('TVDB').get('apikey') or ''


def _get_token(force=False):
    with _lock:
        now = time.time()
        if not force and _token["value"] and (now - _token["ts"] < _TOKEN_TTL):
            return _token["value"]
        key = _tvdb_key()
        if not key:
            debug("TVDB API key not configured", source="tvdb")
            return None
        try:
            r = requests.post(f"{_BASE}/login", json={"apikey": key}, timeout=20)
        except Exception as e:
            info(f"TVDB login failed: {e}", source="tvdb")
            return None
        if r.status_code != 200:
            info(f"TVDB login returned HTTP {r.status_code}", source="tvdb")
            return None
        token = r.json().get("data", {}).get("token")
        _token["value"] = token
        _token["ts"] = now
        debug("TVDB login successful", source="tvdb")
        return token


def _request(path):
    """GET authentifié, avec relogin automatique sur 401."""
    token = _get_token()
    if not token:
        return None
    for attempt in range(2):
        headers = {"Authorization": f"Bearer {token}"}
        try:
            r = requests.get(f"{_BASE}{path}", headers=headers, timeout=20)
        except Exception as e:
            debug(f"TVDB request failed {path}: {e}", source="tvdb")
            return None
        if r.status_code == 401 and attempt == 0:
            token = _get_token(force=True)
            if not token:
                return None
            continue
        if r.status_code != 200:
            debug(f"TVDB HTTP {r.status_code} for {path}", source="tvdb")
            return None
        return r.json()
    return None


def _series_id(imdb_id):
    if imdb_id in _series_id_cache:
        return _series_id_cache[imdb_id]
    series_id = None
    data = _request(f"/search/remoteid/{imdb_id}")
    if data:
        for item in data.get("data", []):
            series = item.get("series")
            if series and series.get("id"):
                series_id = series["id"]
                break
    _series_id_cache[imdb_id] = series_id
    if not series_id:
        debug(f"TVDB: no series found for {imdb_id}", source="tvdb")
    return series_id


def _episode_map(series_id):
    """{(seasonNumber, number): absoluteNumber} pour l'ordre officiel (aired)."""
    if series_id in _episode_map_cache:
        return _episode_map_cache[series_id]
    mapping = {}
    page = 0
    while page < 50:  # garde-fou (jusqu'à ~25 000 épisodes)
        data = _request(f"/series/{series_id}/episodes/official?page={page}")
        if not data:
            break
        for ep in data.get("data", {}).get("episodes", []):
            season = ep.get("seasonNumber")
            number = ep.get("number")
            absolute = ep.get("absoluteNumber")
            if isinstance(season, int) and isinstance(number, int) and absolute:
                mapping[(season, number)] = absolute
        if not data.get("links", {}).get("next"):
            break
        page += 1
    _episode_map_cache[series_id] = mapping
    debug(f"TVDB: cached {len(mapping)} episode mappings for series {series_id}", source="tvdb")
    return mapping


def get_absolute_number(shared_state, imdb_id, season, episode):
    """Numéro absolu pour (saison, épisode), ou None si indisponible."""
    try:
        season, episode = int(season), int(episode)
    except (TypeError, ValueError):
        return None
    series_id = _series_id(imdb_id)
    if not series_id:
        return None
    return _episode_map(series_id).get((season, episode))


def get_season_absolute_numbers(shared_state, imdb_id, season):
    """[(épisode, absolu), ...] triés pour une saison entière (vide si indispo)."""
    try:
        season = int(season)
    except (TypeError, ValueError):
        return []
    series_id = _series_id(imdb_id)
    if not series_id:
        return []
    pairs = [(ep, absolute) for (s, ep), absolute in _episode_map(series_id).items() if s == season]
    pairs.sort()
    return pairs
