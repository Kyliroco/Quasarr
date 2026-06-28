# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import os
import re
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

from quasarr.providers.log import info, debug

# ---------------------------------------------------------------------------
# TMDB genre ID → English name (stable IDs, rarely change)
# ---------------------------------------------------------------------------
_TMDB_GENRES = {
    16: "Animation",
    28: "Action",
    12: "Adventure",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    14: "Fantasy",
    36: "History",
    27: "Horror",
    10402: "Music",
    9648: "Mystery",
    10749: "Romance",
    878: "Science Fiction",
    53: "Thriller",
    10752: "War",
    37: "Western",
    10759: "Action & Adventure",
    10762: "Kids",
    10763: "News",
    10764: "Reality",
    10765: "Sci-Fi & Fantasy",
    10766: "Soap",
    10767: "Talk",
    10768: "War & Politics",
}

# Language code → TMDB locale (e.g. 'fr' → 'fr-FR')
_LANG_TO_LOCALE = {
    'fr': 'fr-FR',
    'de': 'de-DE',
    'en': 'en-US',
    'es': 'es-ES',
    'it': 'it-IT',
    'pt': 'pt-PT',
    'ja': 'ja-JP',
}


def _tmdb_token():
    # Clé TMDB lue depuis l'environnement (comme API_KEY 2captcha / TVDB_API_KEY).
    return os.getenv("TMDB_API_KEY", "")


# Cache process-local des résolutions TMDB /find (les métadonnées sont stables).
# Évite de refaire le même /find pour fr, en, romaji, is_anime, get_type... lors
# d'une même recherche. On ne met en cache que les succès (pas les échecs réseau).
_FIND_CACHE = {}


def _tmdb_find(imdb_id, language='fr-FR'):
    """Call TMDB /find/{imdb_id} and return (result_dict, media_type) or (None, None)."""
    cache_key = (imdb_id, language)
    cached = _FIND_CACHE.get(cache_key)
    if cached is not None:
        return cached

    token = _tmdb_token()
    if not token:
        info("TMDB token not configured — set it in config", source="tmdb")
        return None, None

    headers = {'Authorization': f'Bearer {token}'}
    url = f'https://api.themoviedb.org/3/find/{imdb_id}?external_source=imdb_id&language={language}'
    debug(f"TMDB request: {url}", source="tmdb")

    try:
        r = requests.get(url, headers=headers, timeout=10)
    except Exception as e:
        info(f"TMDB request failed for {imdb_id}: {e}", source="tmdb")
        return None, None

    info(f"TMDB response for {imdb_id}: HTTP {r.status_code}", source="tmdb")
    if r.status_code != 200:
        info(f"TMDB returned HTTP {r.status_code} for {imdb_id}", source="tmdb")
        return None, None

    data = r.json()
    tv = data.get('tv_results', [])
    movies = data.get('movie_results', [])

    if tv:
        debug(f"TMDB: tv result for {imdb_id}: {tv[0]}", source="tmdb")
        result = (tv[0], 'tv')
        _FIND_CACHE[cache_key] = result
        return result
    if movies:
        debug(f"TMDB: movie result for {imdb_id}: {movies[0]}", source="tmdb")
        result = (movies[0], 'movie')
        _FIND_CACHE[cache_key] = result
        return result

    info(f"TMDB: no results found for {imdb_id}", source="tmdb")
    return None, None


def get_poster_link(shared_state, imdb_id):
    if not imdb_id:
        return None

    result, _ = _tmdb_find(imdb_id)
    poster_path = (result or {}).get('poster_path')
    if poster_path:
        return f'https://image.tmdb.org/t/p/w500{poster_path}'

    debug(f"Could not get poster for {imdb_id} from TMDB", source="tmdb")
    return None


def get_localized_title(shared_state, imdb_id, language='de', original_title=False):
    locale = _LANG_TO_LOCALE.get(language, f'{language}-{language.upper()}')
    result, media_type = _tmdb_find(imdb_id, language=locale)

    if not result:
        info(f"Could not extract title from IMDb-ID {imdb_id}", source="tmdb")
        return None, None

    if media_type == 'tv':
        localized_title = result.get('name') or result.get('original_name')
        orig = result.get('original_name')
    else:
        localized_title = result.get('title') or result.get('original_title')
        orig = result.get('original_title')

    titre_original = None
    if original_title and orig and orig != localized_title:
        titre_original = orig

    info(f"TMDB title for {imdb_id}: localized={localized_title!r} original={titre_original!r}", source="tmdb")
    return localized_title, titre_original


def get_romaji_title(shared_state, imdb_id):
    """Titre romaji (japonais en alphabet latin) via TMDB alternative_titles.

    ``original_name`` de TMDB est en script japonais (進撃の巨人) : inutilisable
    pour un slug anime-sama. Le romaji (ex. "Shingeki no Kyojin") se trouve dans
    les titres alternatifs. Retourne None si introuvable.
    """
    result, media_type = _tmdb_find(imdb_id)
    if not result:
        return None
    tmdb_id = result.get('id')
    token = _tmdb_token()
    if not tmdb_id or not token:
        return None

    kind = 'tv' if media_type == 'tv' else 'movie'
    url = f'https://api.themoviedb.org/3/{kind}/{tmdb_id}/alternative_titles'
    headers = {'Authorization': f'Bearer {token}'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
    except Exception as e:
        debug(f"TMDB alternative_titles failed for {imdb_id}: {e}", source="tmdb")
        return None
    if r.status_code != 200:
        return None

    # /tv utilise la clé "results", /movie la clé "titles"
    items = r.json().get('results') or r.json().get('titles') or []
    for item in items:
        if 'romaji' in (item.get('type') or '').lower():
            title = item.get('title')
            if title:
                debug(f"TMDB romaji for {imdb_id}: {title!r}", source="tmdb")
                return title
    return None


def get_type(shared_state, imdb_id, language='fr'):
    locale = _LANG_TO_LOCALE.get(language, f'{language}-{language.upper()}')
    result, _ = _tmdb_find(imdb_id, language=locale)

    if not result:
        info(f"Could not extract genres from IMDb for {imdb_id}", source="tmdb")
        return []

    genre_ids = result.get('genre_ids', [])
    genres = [_TMDB_GENRES[gid] for gid in genre_ids if gid in _TMDB_GENRES]

    info(f"TMDB genres for {imdb_id}: {genres}", source="tmdb")
    return genres


def is_anime(shared_state, imdb_id):
    """True si TMDB classe le titre comme animation d'origine japonaise.

    Sert à router la recherche : un anime passe par anime-sama, un dessin animé
    occidental (Pixar, etc.) ou tout autre contenu reste sur zt.
    """
    result, _media_type = _tmdb_find(imdb_id)
    if not result:
        return False

    if 16 not in result.get('genre_ids', []):  # 16 = Animation
        return False

    original_language = (result.get('original_language') or '').lower()
    origin_country = result.get('origin_country') or []
    is_jp = original_language == 'ja' or 'JP' in origin_country

    debug(f"is_anime({imdb_id}) -> {is_jp} "
          f"(lang={original_language!r}, country={origin_country})", source="tmdb")
    return is_jp


def get_season_episode_counts(shared_state, imdb_id):
    """{numero_saison: nb_episodes} depuis TMDB /tv/{id} (saison 0 ignorée).

    Sert à convertir une numérotation saisonnière (S2E5) en numéro absolu
    quand anime-sama range tout dans un seul dossier. {} si indisponible.
    """
    result, media_type = _tmdb_find(imdb_id)
    if not result or media_type != 'tv':
        return {}
    tmdb_id = result.get('id')
    token = _tmdb_token()
    if not tmdb_id or not token:
        return {}

    cache_key = ('seasons', tmdb_id)
    cached = _FIND_CACHE.get(cache_key)
    if cached is not None:
        return cached

    url = f'https://api.themoviedb.org/3/tv/{tmdb_id}'
    headers = {'Authorization': f'Bearer {token}'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
    except Exception as e:
        debug(f"TMDB /tv details failed for {imdb_id}: {e}", source="tmdb")
        return {}
    if r.status_code != 200:
        return {}

    counts = {}
    for season in r.json().get('seasons', []):
        num = season.get('season_number')
        cnt = season.get('episode_count')
        if isinstance(num, int) and isinstance(cnt, int) and num >= 1:  # ignore les specials (saison 0)
            counts[num] = cnt
    _FIND_CACHE[cache_key] = counts
    debug(f"TMDB season counts for {imdb_id}: {counts}", source="tmdb")
    return counts


def get_clean_title(title):
    try:
        extracted_title = re.findall(r"(.*?)(?:.(?!19|20)\d{2}|\.German|.GERMAN|\.\d{3,4}p|\.S(?:\d{1,3}))", title)[0]
        leftover_tags_removed = re.sub(
            r'(|.UNRATED.*|.Unrated.*|.Uncut.*|.UNCUT.*)(|.Directors.Cut.*|.Final.Cut.*|.DC.*|.REMASTERED.*|.EXTENDED.*|.Extended.*|.Theatrical.*|.THEATRICAL.*)',
            "", extracted_title)
        clean_title = leftover_tags_removed.replace(".", " ").strip().replace(" ", "+")
    except:
        clean_title = title
    return clean_title


def get_imdb_id_from_title(shared_state, title, language="de"):
    imdb_id = None

    if re.search(r"S\d{1,3}(E\d{1,3})?", title, re.IGNORECASE):
        ttype = "tv"
    else:
        ttype = "ft"

    title = get_clean_title(title)

    threshold = 60 * 60 * 48  # 48 hours
    context = "recents_imdb"
    recently_searched = shared_state.get_recently_searched(shared_state, context, threshold)
    if title in recently_searched:
        title_item = recently_searched[title]
        if title_item["timestamp"] > datetime.now() - timedelta(seconds=threshold):
            return title_item["imdb_id"]

    headers = {
        'Accept-Language': language,
        'User-Agent': shared_state.values["user_agent"]
    }

    results = requests.get(f"https://www.imdb.com/find/?q={quote(title)}&s=tt&ttype={ttype}&ref_=fn_{ttype}",
                           headers=headers, timeout=10)

    if results.status_code == 200:
        from bs4 import BeautifulSoup
        from json import loads
        soup = BeautifulSoup(results.text, "html.parser")
        props = soup.find("script", text=re.compile("props"))
        if props:
            try:
                details = loads(props.string)
                search_results = details['props']['pageProps']['titleResults']['results']
                if len(search_results) > 0:
                    for result in search_results:
                        if shared_state.search_string_in_sanitized_title(title, f"{result['titleNameText']}"):
                            imdb_id = result['id']
                            break
            except Exception as e:
                debug(f"IMDb search parse error: {e}", source="tmdb")
    else:
        debug(f"IMDb search request failed: {results.status_code}", source="tmdb")

    recently_searched[title] = {
        "imdb_id": imdb_id,
        "timestamp": datetime.now()
    }
    shared_state.update(context, recently_searched)

    if not imdb_id:
        debug(f"No IMDb-ID found for {title}", source="tmdb")

    return imdb_id
