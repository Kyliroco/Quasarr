# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

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
    import os
    token = os.environ.get('TMDB_TOKEN', '')
    if not token:
        from quasarr.storage.config import Config
        token = Config('TMDB').get('token') or ''
    return token


def _tmdb_find(imdb_id, language='fr-FR'):
    """Call TMDB /find/{imdb_id} and return (result_dict, media_type) or (None, None)."""
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
        return tv[0], 'tv'
    if movies:
        debug(f"TMDB: movie result for {imdb_id}: {movies[0]}", source="tmdb")
        return movies[0], 'movie'

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
