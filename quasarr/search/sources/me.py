# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

"""Maison Energy search source."""

import html
import time
from base64 import urlsafe_b64encode
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from quasarr.providers.imdb_metadata import get_localized_title
from quasarr.providers.log import info, debug

hostname = "me"


def _update_hostname(shared_state, current_host, final_url):
    try:
        parsed = urlparse(final_url)
        final_host = parsed.netloc.lower()
    except Exception:
        return current_host

    if final_host and current_host and final_host != current_host:
        info(f"{hostname.upper()} redirect detected. Updating hostname to '{final_host}'.")
        shared_state.values["config"]("Hostnames").save(hostname.lower(), final_host)
        return final_host
    return current_host


def _parse_results(shared_state,
                   soup,
                   base_url,
                   password,
                   request_from,
                   mirror,
                   search_string=None,
                   season=None,
                   episode=None,
                   imdb_id=None):
    releases = []
    cards = soup.select("div.cover_global")

    debug(
        f"{hostname.upper()} parsing {len(cards)} cards from {base_url} "
        f"(requester={request_from}, mirror={mirror})"
    )

    for card in cards:
        try:
            title_link = card.select_one("div.cover_infos_title a")
            if not title_link:
                debug(f"{hostname.upper()} skipping card without title link on {base_url}")
                continue

            title = title_link.get_text(strip=True)
            if not title:
                debug(f"{hostname.upper()} skipping card with empty title on {base_url}")
                continue

            if search_string is not None:
                if not shared_state.is_valid_release(title,
                                                     request_from,
                                                     search_string,
                                                     season,
                                                     episode):
                    debug(
                        f"{hostname.upper()} filtered title '{title}' "
                        f"for requester={request_from}, search='{search_string}'"
                    )
                    continue

            if "lazylibrarian" in request_from.lower():
                title = shared_state.normalize_magazine_title(title)

            quality = ""
            quality_tag = card.select_one("span.detail_release")
            if quality_tag:
                quality = quality_tag.get_text(" ", strip=True)

            href = title_link.get("href", "").strip()
            if not href:
                debug(f"{hostname.upper()} skipping '{title}' because no href was found")
                continue

            source = urljoin(base_url, href)

            time_tag = card.find("time")
            published = time_tag.get_text(strip=True) if time_tag else ""

            mb = 0
            release_imdb_id = imdb_id

            final_title = title
            if quality:
                final_title = f"{title} - {quality}"

            payload = urlsafe_b64encode(
                f"{final_title}|{source}|{mirror}|{mb}|{password}|{release_imdb_id}".encode("utf-8")
            ).decode("utf-8")

            link = f"{shared_state.values['internal_address']}/download/?payload={payload}"

            debug(
                f"{hostname.upper()} prepared release '{final_title}' with source {source}"
            )

            releases.append({
                "details": {
                    "title": final_title,
                    "hostname": hostname,
                    "imdb_id": release_imdb_id,
                    "link": link,
                    "mirror": mirror,
                    "size": 0,
                    "date": published,
                    "source": source,
                },
                "type": "protected",
            })
        except Exception as exc:
            debug(f"Error parsing {hostname.upper()} card: {exc}")
            continue

    debug(f"{hostname.upper()} generated {len(releases)} releases from {base_url}")
    return releases


def _get_category(request_from):
    rf = (request_from or "").lower()
    if "radarr" in rf:
        return "films"
    if "postman" in rf:
        return "films"
    if "sonarr" in rf:
        if "anime" in rf or "animé" in rf or "manga" in rf:
            return "mangas"
        return "series"
    return None


def me_feed(shared_state, start_time, request_from, mirror=None):
    releases = []
    category = _get_category(request_from)
    if not category:
        debug(f"Skipping {hostname.upper()} feed for unsupported requester '{request_from}'.")
        return releases

    config = shared_state.values["config"]("Hostnames")
    me = config.get(hostname)
    if not me:
        info(f"{hostname.upper()} host missing in configuration. Feed aborted for requester '{request_from}'.")
        return releases

    password = me
    url = f"https://{me}/?p={category}"
    headers = {"User-Agent": shared_state.values["user_agent"]}

    info(
        f"{hostname.upper()} feed request for category '{category}' "
        f"(mirror={mirror}) using host '{me}'"
    )

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        me = _update_hostname(shared_state, me, response.url)
        password = me
        soup = BeautifulSoup(response.text, "html.parser")
        releases = _parse_results(shared_state, soup, response.url, password, request_from, mirror)
    except Exception as exc:
        message = f"Error loading {hostname.upper()} feed: {exc}"
        info(message)
        raise RuntimeError(message) from exc

    debug(f"Time taken: {time.time() - start_time:.2f}s ({hostname})")
    return releases


def me_search(shared_state,
              start_time,
              request_from,
              search_string,
              mirror=None,
              season=None,
              episode=None):
    releases = []
    category = _get_category(request_from)
    if not category:
        debug(f"Skipping {hostname.upper()} search for unsupported requester '{request_from}'.")
        return releases

    config = shared_state.values["config"]("Hostnames")
    me = config.get(hostname)
    if not me:
        info(f"{hostname.upper()} host missing in configuration. Search aborted for '{search_string}'.")
        return releases

    password = me

    imdb_id = shared_state.is_imdb_id(search_string)
    if imdb_id:
        localized = get_localized_title(shared_state, imdb_id, 'fr')
        if not localized:
            info(f"Could not extract title from IMDb-ID {imdb_id}")
            return releases
        search_string = html.unescape(localized)

    q = quote_plus(search_string)
    url = f"https://{me}/?p={category}&search={q}"
    headers = {"User-Agent": shared_state.values["user_agent"]}

    info(
        f"{hostname.upper()} search request for '{search_string}' "
        f"(category={category}, mirror={mirror}) using host '{me}'"
    )

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        me = _update_hostname(shared_state, me, response.url)
        password = me
        soup = BeautifulSoup(response.text, "html.parser")
        releases = _parse_results(shared_state,
                                  soup,
                                  response.url,
                                  password,
                                  request_from,
                                  mirror,
                                  search_string=search_string,
                                  season=season,
                                  episode=episode,
                                  imdb_id=imdb_id)
    except Exception as exc:
        message = f"Error loading {hostname.upper()} search: {exc}"
        info(message)
        raise RuntimeError(message) from exc

    debug(f"Time taken: {time.time() - start_time:.2f}s ({hostname})")
    return releases
