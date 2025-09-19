# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from quasarr.providers.log import info, debug

hostname = "me"
IGNORED_HOSTS = {
    "imdb.com",
    "www.imdb.com",
    "themoviedb.org",
    "www.themoviedb.org",
    "youtube.com",
    "www.youtube.com",
    "twitter.com",
    "www.twitter.com",
    "facebook.com",
    "www.facebook.com",
}


def _update_hostname(shared_state, current_host, final_url):
    try:
        parsed = urlparse(final_url)
        final_host = parsed.netloc.lower()
    except Exception:
        return current_host

    if final_host and current_host and final_host != current_host:
        info(f"{hostname.upper()} redirect detected while resolving download page. Updating hostname to '{final_host}'.")
        shared_state.values["config"]("Hostnames").save(hostname.lower(), final_host)
        return final_host
    return current_host


def _extract_imdb_id(soup):
    link = soup.find("a", href=re.compile(r"imdb\.com/title/(tt\d+)", re.IGNORECASE))
    if not link:
        return None
    match = re.search(r"(tt\d+)", link.get("href", ""))
    return match.group(1) if match else None


def _iter_candidate_links(soup):
    selectors = [
        '[id*="download"]',
        '[class*="download"]',
        '[id*="ddl"]',
        '[class*="ddl"]',
        '[id*="telecharg"]',
        '[class*="telecharg"]',
        '[id*="lien"]',
        '[class*="lien"]',
        'div.card-body',
        'div.postinfo',
    ]

    seen = set()

    for selector in selectors:
        for node in soup.select(selector):
            if node not in seen:
                seen.add(node)
                yield node

    if not seen:
        yield soup


def get_me_download_links(shared_state, url, mirror, title):
    config = shared_state.values["config"]("Hostnames")
    me = config.get(hostname)
    headers = {"User-Agent": shared_state.values["user_agent"]}

    info(
        f"{hostname.upper()} fetching download page for '{title}' "
        f"(mirror={mirror}) at {url}"
    )

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        message = (
            f"{hostname.upper()} site has been updated. Grabbing download links for {title} "
            f"not possible: {exc}"
        )
        info(message)
        raise RuntimeError(message) from exc

    me = _update_hostname(shared_state, me, response.url)
    soup = BeautifulSoup(response.text, "html.parser")

    imdb_id = _extract_imdb_id(soup)

    links = []
    visited = set()

    for container in _iter_candidate_links(soup):
        header = container.find_previous("h2")
        if header and "streaming" in header.get_text(strip=True).lower():
            debug(
                f"{hostname.upper()} skipping streaming container for '{title}' on {response.url}"
            )
            continue

        for a_tag in container.find_all("a", href=True):
            href = a_tag.get("href", "").strip()
            if not href or href.startswith("javascript:"):
                debug(
                    f"{hostname.upper()} ignoring non-link anchor '{href}' for '{title}'"
                )
                continue

            absolute = urljoin(response.url, href)
            parsed = urlparse(absolute)
            scheme = parsed.scheme.lower()
            if scheme not in {"http", "https"}:
                debug(
                    f"{hostname.upper()} ignoring unsupported scheme '{scheme}' "
                    f"for link {absolute}"
                )
                continue

            netloc = parsed.netloc.lower()
            if not netloc:
                debug(f"{hostname.upper()} ignoring link without netloc: {absolute}")
                continue

            if netloc.endswith(me):
                debug(
                    f"{hostname.upper()} skipping internal redirect link {absolute} for '{title}'"
                )
                continue

            if netloc in IGNORED_HOSTS:
                debug(f"{hostname.upper()} ignoring known non-hoster domain {netloc}")
                continue

            link_text = a_tag.get_text(" ", strip=True).lower()
            if "regarder" in link_text:
                debug(
                    f"{hostname.upper()} skipping streaming text link {absolute} for '{title}'"
                )
                continue

            query = parse_qs(parsed.query)
            rl_values = [v.lower() for values in query.values() for v in values]
            if any(value in {"a1", "h1"} for value in rl_values):
                debug(f"{hostname.upper()} skipping streaming query link {absolute}")
                continue

            hoster_name = None
            prev_div = a_tag.find_previous("div")
            while prev_div:
                if prev_div.find_parent("div", class_="postinfo") == container:
                    hoster_name = prev_div.get_text(strip=True)
                    break
                prev_div = prev_div.find_previous("div")

            if not hoster_name:
                hoster_parts = netloc.split(".")
                if len(hoster_parts) >= 2:
                    hoster_name = hoster_parts[-2]
                else:
                    hoster_name = netloc

            base_hoster = hoster_name

            if base_hoster.lower() in {me.lower(), "dl-protect"}:
                debug(f"{hostname.upper()} ignoring protected redirect hoster {base_hoster}")
                continue

            if mirror and mirror.lower() not in base_hoster.lower():
                debug(
                    f"{hostname.upper()} skipping hoster '{base_hoster}' because it "
                    f"does not match requested mirror '{mirror}'"
                )
                continue

            if absolute in visited:
                debug(f"{hostname.upper()} already collected link {absolute}")
                continue

            visited.add(absolute)
            anchor_text = a_tag.get_text(" ", strip=True)
            if anchor_text:
                anchor_text = " ".join(anchor_text.split())
            display_name = base_hoster
            if anchor_text and re.search(r"\bepisode\b", anchor_text.lower()):
                display_name = f"{base_hoster} - {anchor_text}"

            debug(
                f"{hostname.upper()} accepted download link {absolute} with label '{display_name}'"
            )
            links.append([absolute, display_name])

    if not links:
        info(f"{hostname.upper()} site returned no recognizable download links for {title}.")
    else:
        info(
            f"{hostname.upper()} extracted {len(links)} download links for '{title}' "
            f"(imdb_id={imdb_id or 'unknown'})"
        )

    return {
        "links": links,
        "imdb_id": imdb_id,
    }
