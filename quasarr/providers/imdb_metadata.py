# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import html
import json
import re
from datetime import datetime, timedelta
from json import loads
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from quasarr.providers.log import info, debug


def get_poster_link(shared_state, imdb_id):
    poster_link = None
    if imdb_id:
        headers = {'User-Agent': shared_state.values["user_agent"]}
        request = requests.get(f"https://www.imdb.com/title/{imdb_id}/", headers=headers, timeout=10).text
        debug(request)
        soup = BeautifulSoup(request, "html.parser")
        try:
            poster_set = soup.find('div', class_='ipc-poster').div.img[
                "srcset"]  # contains links to posters in ascending resolution
            poster_links = [x for x in poster_set.split(" ") if
                            len(x) > 10]  # extract all poster links ignoring resolution info
            poster_link = poster_links[-1]  # get the highest resolution poster
        except:
            pass

    if not poster_link:
        debug(f"Could not get poster title for {imdb_id} from IMDb")

    return poster_link


def get_localized_title(shared_state, imdb_id, language='de',original_title=False):
    localized_title = None
    titre_original =None
    headers = {
        'Accept-Language': language,
        'User-Agent': shared_state.values["user_agent"]
    }

    try:
        response = requests.get(f"https://www.imdb.com/title/{imdb_id}/", headers=headers, timeout=10)
    except Exception as e:
        info(f"Error loading IMDb metadata for {imdb_id}: {e}")
        return localized_title, None
    debug(f"IMDb response status for {imdb_id}: {response.status_code}")
    if response.status_code >= 300:
        info(f"IMDb returned HTTP {response.status_code} for {imdb_id}")
        return None, None
    soup = None
    if original_title:
        match = re.search(r">Titre original\s*:?(.+?)</div>", response.text, re.DOTALL)
        if match:
            titre_original = match.group(1).strip()
        if not titre_original:
            soup = BeautifulSoup(response.text, "html.parser")
            aka_item = soup.find(
                "li",
                {"data-testid": "title-details-akas"},
            )
            if aka_item:
                aka_text = aka_item.find(
                    "span",
                    class_="ipc-metadata-list-item__list-content-item",
                )
                if aka_text:
                    titre_original = aka_text.get_text(strip=True)
    title_tag = re.search(r'<title>(.*?)</title>', response.text)
    if title_tag:
        raw_title = html.unescape(title_tag.group(1))
        debug(f"IMDb raw title tag for {imdb_id}: {raw_title!r}")
        # IMDb formats: "Title (TV Series ...) - IMDb" or "Title | IMDb" or "Title - IMDb"
        localized_title = re.split(r'\s*(?:\(|\||\s+-\s+IMDb)', raw_title)[0].strip() or None
    else:
        debug(f"IMDb: no <title> tag found for {imdb_id} (body length: {len(response.text)})")

    if not localized_title:
        debug(f"Could not get localized title for {imdb_id} in {language} from IMDb")
    # info(localized_title)
    # localized_title = html.unescape(localized_title)
    # info(localized_title)
    # localized_title = re.sub(r"[^a-zA-Z0-9äöüÄÖÜß&-']", ' ', localized_title).strip()
    # info(localized_title)
    # localized_title = localized_title.replace(" - ", "-")
    # info(localized_title)
    # localized_title = re.sub(r'\s{2,}', ' ', localized_title)
    # info(localized_title)
    debug(f"IMDb title for {imdb_id}: localized={localized_title!r} original={titre_original!r}")
    return localized_title, titre_original

def get_type(shared_state, imdb_id, language='de'):
    headers = {
        'Accept-Language': language,
        'User-Agent': shared_state.values["user_agent"]
    }

    try:
        response = requests.get(f"https://www.imdb.com/title/{imdb_id}/", headers=headers, timeout=10)
        info(response.text)
    except Exception as e:
        info(f"Error loading IMDb metadata for {imdb_id}: {e}")
        return []
    debug(f"IMDb response status for {imdb_id}: {response.status_code}")
    if response.status_code >= 300:
        info(f"IMDb returned HTTP {response.status_code} for {imdb_id}")
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    # 1) Chercher le bloc JSON-LD principal et parser `genre`
    ld_tags = soup.find_all("script", type="application/ld+json")
    debug(f"IMDb JSON-LD tags found for {imdb_id}: {len(ld_tags)}, body length: {len(response.text)}")
    genres = []
    for tag in ld_tags:
        try:
            data = json.loads(tag.string or tag.text or "")
        except Exception:
            continue

        # Plusieurs possibilités : dict seul, ou liste de dicts
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            typ = obj.get("@type", "")
            debug(f"IMDb JSON-LD @type for {imdb_id}: {typ!r}")
            # IMDb met souvent TVSeries/Movie ici
            if ("TVSeries" in typ) or ("Movie" in typ) or ("TVEpisode" in typ):
                g = obj.get("genre")
                if not g:
                    continue
                if isinstance(g, str):
                    genres.extend([x.strip() for x in g.split(",") if x.strip()])
                elif isinstance(g, list):
                    genres.extend([str(x).strip() for x in g if str(x).strip()])

    # Dédup + ordre conservé
    seen = set()
    genres_unique = [g for g in genres if not (g in seen or seen.add(g))]
    if genres_unique:
        debug(f"IMDb genres for {imdb_id}: {genres_unique}")
        return genres_unique

    # 2) Fallback : essayer de lire depuis <meta property="og:title"> (ex: "... | Animation, Action, Aventure")
    og_title = soup.find("meta", {"property": "og:title"})
    if og_title and og_title.get("content"):
        part = og_title["content"].split("|")[-1]  # " Animation, Action, Aventure"
        alt = [x.strip() for x in part.split(",") if x.strip()]
        if alt:
            debug(f"IMDb genres for {imdb_id} (fallback): {alt}")
            return alt

    info(f"Could not extract genres from IMDb for {imdb_id}", source="imdb")
    return []

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
        soup = BeautifulSoup(results.text, "html.parser")
        props = soup.find("script", text=re.compile("props"))
        details = loads(props.string)
        search_results = details['props']['pageProps']['titleResults']['results']

        if len(search_results) > 0:
            for result in search_results:
                if shared_state.search_string_in_sanitized_title(title, f"{result['titleNameText']}"):
                    imdb_id = result['id']
                    break
    else:
        debug(f"Request on IMDb failed: {results.status_code}")

    recently_searched[title] = {
        "imdb_id": imdb_id,
        "timestamp": datetime.now()
    }
    shared_state.update(context, recently_searched)

    if not imdb_id:
        debug(f"No IMDb-ID found for {title}")

    return imdb_id
