# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

"""Lecture seule de Radarr/Sonarr pour la page « manquants introuvables ».

Objectif : lister les titres que Radarr/Sonarr réclament (monitorés, absents du
disque) et qui ne figurent **pas** dans la blocklist. Un titre blocklisté a été
trouvé au moins une fois puis écarté volontairement ; un titre absent de la
blocklist n'a, lui, jamais été trouvé — c'est typiquement qu'il n'existe pas sur
le site. C'est cette seconde liste qui est utile à surveiller.

Ce module ne fait que des requêtes GET : rien n'est modifié dans Radarr/Sonarr.
"""

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus, urlparse

import requests

from quasarr.providers.log import debug
from quasarr.storage.config import Config

REQUEST_TIMEOUT = 20
PAGE_SIZE = 1000
METADATA_WORKERS = 8


class ArrError(Exception):
    """Radarr/Sonarr injoignable ou réponse inexploitable."""


def normalize_base_url(url):
    """Rend utilisable une URL saisie à la main.

    Sans schéma, requests refuse l'URL ("No connection adapters were found"),
    et on colle volontiers un "/api" ou "/api/v3" en copiant depuis Radarr.
    """
    url = (url or '').strip().rstrip('/')
    if not url:
        return ''
    if '://' not in url:
        url = f"http://{url}"
    for suffix in ('/api/v3', '/api'):
        if url.endswith(suffix):
            url = url[:-len(suffix)]
    return url.rstrip('/')


def get_arr_config(kind):
    """Retourne (url, api_key) pour 'Radarr' ou 'Sonarr'; ('', '') si non configuré."""
    section = Config(kind)
    url = normalize_base_url(section.get('url'))
    api_key = (section.get('api_key') or '').strip()
    return url, api_key


def is_configured(kind):
    url, api_key = get_arr_config(kind)
    return bool(url and api_key)


def _reachability_hint(url, exc):
    """Piste de résolution pour les deux pièges classiques en conteneur."""
    message = str(exc).lower()
    host = urlparse(url).hostname or ''

    if host in ('127.0.0.1', 'localhost', '::1'):
        return (" — Astuce : dans un conteneur Docker, 127.0.0.1 désigne le "
                "conteneur Quasarr lui-même, pas la machine hôte. Utilisez "
                "l'IP LAN de l'hôte (ex. http://192.168.1.30:PORT), ou "
                "host.docker.internal, ou le nom du conteneur si Radarr/Sonarr "
                "partage le même réseau Docker.")

    if 'name does not resolve' in message or 'nameresolutionerror' in message \
            or 'nodename nor servname' in message or 'getaddrinfo' in message:
        return (f" — Astuce : le conteneur Quasarr ne sait pas résoudre "
                f"'{host}'. Les noms MagicDNS Tailscale (*.ts.net) n'y sont pas "
                f"résolus par défaut : utilisez une IP, ou déclarez le DNS "
                f"Tailscale (100.100.100.100) dans le conteneur.")

    return ""


def _get(kind, path, params=None):
    url, api_key = get_arr_config(kind)
    if not url or not api_key:
        raise ArrError(f"{kind} n'est pas configuré (URL ou clé API manquante).")

    query = dict(params or {})
    query['apikey'] = api_key
    endpoint = f"{url}/api/v3/{path.lstrip('/')}"
    try:
        response = requests.get(endpoint, params=query, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        raise ArrError(f"{kind} injoignable sur {url} : {exc}{_reachability_hint(url, exc)}") from exc

    if response.status_code == 401:
        raise ArrError(f"{kind} a refusé la clé API (401).")
    if response.status_code != 200:
        raise ArrError(f"{kind} a répondu HTTP {response.status_code} sur /{path}.")

    try:
        return response.json()
    except Exception as exc:
        raise ArrError(f"Réponse illisible de {kind} sur /{path} : {exc}") from exc


def _blocklisted_ids(kind, id_field):
    """Identifiants présents dans la blocklist (donc déjà trouvés puis écartés)."""
    payload = _get(kind, 'blocklist', {'page': 1, 'pageSize': PAGE_SIZE})
    records = payload.get('records', payload if isinstance(payload, list) else [])
    blocked = set()
    for record in records:
        value = record.get(id_field)
        if value is not None:
            blocked.add(value)
        # Sonarr rattache aussi les épisodes concernés à l'entrée de blocklist.
        for episode_id in record.get('episodeIds', []) or []:
            blocked.add(('episode', episode_id))
    return blocked


def get_missing_movies():
    """Films voulus par Radarr, sans fichier et hors blocklist."""
    movies = _get('Radarr', 'movie')
    blocked_movie_ids = _blocklisted_ids('Radarr', 'movieId')

    missing = []
    for movie in movies:
        if not movie.get('monitored'):
            continue
        if movie.get('hasFile'):
            continue
        if movie.get('id') in blocked_movie_ids:
            continue
        # Un film pas encore sorti est introuvable par nature : ce n'est pas un
        # problème d'indexeur, on ne veut pas polluer la liste avec.
        if not movie.get('isAvailable', True):
            continue
        missing.append({
            'arr_title': movie.get('title') or '',
            'original_title': movie.get('originalTitle') or '',
            'year': movie.get('year') or '',
            'imdb_id': movie.get('imdbId') or '',
            'count': None,
        })

    missing.sort(key=lambda item: (item['arr_title'] or '').lower())
    return missing


def get_missing_series():
    """Séries dont Sonarr attend des épisodes, hors blocklist.

    Regroupé par série : une ligne par série avec le nombre d'épisodes
    manquants, plutôt qu'une ligne par épisode (illisible au-delà de quelques
    séries, et la recherche sur le site se fait de toute façon par titre).
    """
    payload = _get('Sonarr', 'wanted/missing', {
        'page': 1,
        'pageSize': PAGE_SIZE,
        'includeSeries': 'true',
        'monitored': 'true',
    })
    records = payload.get('records', [])
    blocked = _blocklisted_ids('Sonarr', 'seriesId')

    by_series = {}
    for episode in records:
        episode_id = episode.get('id')
        if ('episode', episode_id) in blocked:
            continue

        series = episode.get('series') or {}
        series_id = series.get('id') or episode.get('seriesId')
        if series_id is None:
            continue

        entry = by_series.get(series_id)
        if entry is None:
            entry = {
                'arr_title': series.get('title') or '',
                'original_title': '',
                'year': series.get('year') or '',
                'imdb_id': series.get('imdbId') or '',
                'count': 0,
            }
            by_series[series_id] = entry
        entry['count'] += 1

    missing = sorted(by_series.values(), key=lambda item: (item['arr_title'] or '').lower())
    return missing


def enrich_with_search_titles(shared_state, items):
    """Ajoute le titre que Quasarr cherche réellement (TMDB localisé en FR).

    C'est le point clé du diagnostic : si Radarr veut « Barbie in Rock 'N
    Royals » mais que Quasarr cherche « Barbie : Rock et Royales », c'est ce
    second terme qu'il faut confronter au site.
    """
    from quasarr.providers.imdb_metadata import get_localized_title

    def _lookup(item):
        imdb_id = item.get('imdb_id')
        if not imdb_id:
            return item
        try:
            localized, original = get_localized_title(shared_state, imdb_id, 'fr', True)
        except Exception as exc:
            debug(f"TMDB lookup failed for {imdb_id}: {exc}", source="arr")
            return item
        if localized:
            item['search_title'] = localized
        if original:
            item['original_title'] = original
        return item

    if not items:
        return items

    workers = min(METADATA_WORKERS, len(items))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_lookup, items))

    for item in items:
        item.setdefault('search_title', item.get('arr_title', ''))
    return items


def zt_search_url(shared_state, title, category):
    """Lien de recherche direct sur ZT pour vérifier à la main."""
    host = (shared_state.values["config"]("Hostnames").get("zt") or '').strip()
    if not host or not title:
        return ''
    return f"https://{host}/?p={category}&search={quote_plus(title)}"
