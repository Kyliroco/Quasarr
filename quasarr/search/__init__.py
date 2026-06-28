# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from quasarr.providers.imdb_metadata import is_anime
from quasarr.providers.log import info, debug, error
from quasarr.search.sources.al import al_feed, al_search
from quasarr.search.sources.am import am_feed, am_search
from quasarr.search.sources.by import by_feed, by_search
from quasarr.search.sources.dd import dd_search, dd_feed
from quasarr.search.sources.dt import dt_feed, dt_search
from quasarr.search.sources.dw import dw_feed, dw_search
from quasarr.search.sources.fx import fx_feed, fx_search
from quasarr.search.sources.mb import mb_feed, mb_search
from quasarr.search.sources.nx import nx_feed, nx_search
from quasarr.search.sources.sf import sf_feed, sf_search
from quasarr.search.sources.sl import sl_feed, sl_search
from quasarr.search.sources.wd import wd_feed, wd_search
from quasarr.search.sources.zt import zt_feed, zt_search


def get_search_results(shared_state, request_from, imdb_id="", search_phrase="", mirror=None, season="", episode=""):
    results = []

    if imdb_id and not imdb_id.startswith('tt'):
        imdb_id = f'tt{imdb_id}'

    docs_search = "lazylibrarian" in request_from.lower()

    al = shared_state.values["config"]("Hostnames").get("al")
    am = shared_state.values["config"]("Hostnames").get("am")
    by = shared_state.values["config"]("Hostnames").get("by")
    dd = shared_state.values["config"]("Hostnames").get("dd")
    dt = shared_state.values["config"]("Hostnames").get("dt")
    dw = shared_state.values["config"]("Hostnames").get("dw")
    fx = shared_state.values["config"]("Hostnames").get("fx")
    mb = shared_state.values["config"]("Hostnames").get("mb")
    nx = shared_state.values["config"]("Hostnames").get("nx")
    sf = shared_state.values["config"]("Hostnames").get("sf")
    sl = shared_state.values["config"]("Hostnames").get("sl")
    wd = shared_state.values["config"]("Hostnames").get("wd")
    zt = shared_state.values["config"]("Hostnames").get("zt")

    start_time = time.time()

    functions = []

    # Radarr/Sonarr use imdb_id for searches
    imdb_map = [
        (al, al_search),
        (am, am_search),
        (by, by_search),
        (dd, dd_search),
        (dt, dt_search),
        (dw, dw_search),
        (fx, fx_search),
        (mb, mb_search),
        (nx, nx_search),
        (sf, sf_search),
        (sl, sl_search),
        (wd, wd_search),
        (zt, zt_search),
    ]

    # LazyLibrarian uses search_phrase for searches
    phrase_map = [
        (by, by_search),
        (dt, dt_search),
        (nx, nx_search),
        (sl, sl_search),
        (wd, wd_search),
    ]

    # Feed searches omit imdb_id and search_phrase
    feed_map = [
        (al, al_feed),
        (am, am_feed),
        (by, by_feed),
        (dd, dd_feed),
        (dt, dt_feed),
        (dw, dw_feed),
        (fx, fx_feed),
        (mb, mb_feed),
        (nx, nx_feed),
        (sf, sf_feed),
        (sl, sl_feed),
        (wd, wd_feed),
        (zt, zt_feed),
    ]

    # anime-sama (am) ne sert que pour les animes ; pour un anime on le préfère à
    # zt (zt n'est alors qu'un secours, lancé après si anime-sama ne renvoie rien).
    anime = False
    if imdb_id and am:
        anime = is_anime(shared_state, imdb_id)

    if imdb_id:  # only Radarr/Sonarr are using imdb_id
        args, kwargs = (
            (shared_state, start_time, request_from, imdb_id),
            {'mirror': mirror, 'season': season, 'episode': episode}
        )
        for flag, func in imdb_map:
            if not flag:
                continue
            if func is am_search and not anime:
                continue  # anime-sama : animes uniquement
            if func is zt_search and anime:
                continue  # anime : zt seulement en secours (géré après le run)
            functions.append(lambda f=func, a=args, kw=kwargs: f(*a, **kw))

    elif search_phrase and docs_search:  # only LazyLibrarian is allowed to use search_phrase
        args, kwargs = (
            (shared_state, start_time, request_from, search_phrase),
            {'mirror': mirror, 'season': season, 'episode': episode}
        )
        for flag, func in phrase_map:
            if flag:
                functions.append(lambda f=func, a=args, kw=kwargs: f(*a, **kw))

    elif search_phrase:
        debug(
            f"Search phrase '{search_phrase}' is not supported for {request_from}. Only LazyLibrarian can use search phrases.")

    else:
        args, kwargs = (
            (shared_state, start_time, request_from),
            {'mirror': mirror}
        )
        for flag, func in feed_map:
            if flag:
                functions.append(lambda f=func, a=args, kw=kwargs: f(*a, **kw))

    if imdb_id:
        stype = f'IMDb-ID "{imdb_id}"'
    elif search_phrase:
        stype = f'Search-Phrase "{search_phrase}"'
    else:
        stype = "feed search"

    debug(f'Starting {len(functions)} search functions for {stype}... This may take some time.')

    with ThreadPoolExecutor() as executor:
        futures = [executor.submit(func) for func in functions]
        for future in as_completed(futures):
            try:
                result = future.result()
                results.extend(result)
            except Exception as e:
                tb = traceback.extract_tb(e.__traceback__)
                location = f"{tb[-1].filename}:{tb[-1].lineno}" if tb else "unknown location"
                error(f"An error occurred at {location}: {e}", source="search")

    # Secours zt : si c'est un anime et qu'anime-sama n'a renvoyé aucun résultat,
    # on retombe sur zt (qui n'a pas été lancé dans le run parallèle ci-dessus).
    if imdb_id and anime and zt:
        am_found = any(r.get("details", {}).get("hostname") == "am" for r in results)
        if not am_found:
            debug("anime-sama returned no results — falling back to zt", source="search")
            try:
                results.extend(zt_search(
                    shared_state, start_time, request_from, imdb_id,
                    mirror=mirror, season=season, episode=episode,
                ))
            except Exception as e:
                error(f"zt fallback failed: {e}", source="search")

    elapsed_time = time.time() - start_time
    info(f"Providing {len(results)} releases to {request_from} for {stype}. Time taken: {elapsed_time:.2f} seconds")

    return results
