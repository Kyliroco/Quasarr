# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

"""Maison Energy search source."""

import html
import re
import time
from base64 import urlsafe_b64encode
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from quasarr.providers.imdb_metadata import get_localized_title
from quasarr.providers.log import info, debug

hostname = "zt"

def _extract_supported_mirrors(detail_soup):
    """
    Parcourt les blocs <div class="postinfo"> et extrait les mirrors supportés.
    Structure typique répétée :
        <b><div>HostName</div></b>
        <b><a href="...&rl=a2">Télécharger</a></b><br><br>
    On ne retient que les liens de téléchargement (rl=a2).
    """
    supported = set()

    def normalize_host(h: str) -> str:
        h = (h or "").strip().lower()
        # uniformiser quelques variantes/domains
        h_norm = (
            h.replace(" ", "")
             .replace("-", "")
             .replace("_", "")
             .replace(".", "")
        )
        if h_norm.startswith("rapidgator"):
            return "rapidgator"
        if h_norm in {"ddownload", "ddl", "ddlto", "ddownlaod", "ddown"}:
            return "ddownload"
        if h_norm.startswith("1fichier"):
            return "1fichier"
        if "nitro" in h_norm:
            return "nitroflare"
        if "turbobit" in h_norm:
            return "turbobit"
        if "uploady" in h_norm:
            return "uploady"
        if "dailyuploads" in h_norm or "dailyupload" in h_norm:
            return "dailyuploads"
        return h.lower()

    for post in detail_soup.select("div.postinfo"):
        # On cherche les <b> qui contiennent un <div> (le nom d'hébergeur)
        for host_b in post.select("b"):
            host_div = host_b.find("div")
            if not host_div:
                continue

            host = normalize_host(host_div.get_text(strip=True))

            # Avancer jusqu'au prochain sibling <b> porteur du <a href=...>
            sib = host_b.next_sibling
            while sib and (getattr(sib, "name", None) is None or getattr(sib, "name", None) == "br"):
                sib = sib.next_sibling
            if not getattr(sib, "name", None) == "b":
                # fallback : au cas où le DOM soit un peu plus bruité
                sib = host_b.find_next("b")
            if not sib:
                continue

            a = sib.find("a", href=True)
            if not a:
                continue

            href = a["href"]
            # On ne garde que les liens de téléchargement (rl=a2), pas le streaming (rl=a1)
            if "rl=a2" not in href:
                continue

            # Filtrage final via SUPPORTED_MIRRORS (défini en haut du module)
            if host in SUPPORTED_MIRRORS:
                supported.add(host)

    return list(supported)

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


def _extract_production_year(text):
    if not text:
        return ""

    match = re.search(r"ann[eé]e\s+de\s+production[^0-9]*(\d{4})", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def _extract_size_mb(shared_state, text):
    if not text:
        return 0

    size_patterns = [
        re.compile(r"taille\s+(?:du\s+fichier|d['’]un\s+episode)[^0-9~≈]*([~≈]?\s*[\d]+(?:[.,]\d+)?)\s*([kmgto])o",
                   re.IGNORECASE),
        re.compile(r"([\d]+(?:[.,]\d+)?)\s*([kmgto])o", re.IGNORECASE),
    ]

    for pattern in size_patterns:
        match = pattern.search(text)
        if not match:
            continue

        raw_value, unit_letter = match.groups()
        cleaned = raw_value.replace("~", "").replace("≈", "").strip()
        cleaned = cleaned.replace(",", ".")
        try:
            size = float(cleaned)
        except ValueError:
            continue

        unit_map = {"K": "KB", "M": "MB", "G": "GB", "T": "TB", "O": "B"}
        unit = unit_map.get(unit_letter.upper())
        if not unit:
            continue

        try:
            size_mb = shared_state.convert_to_mb({"size": str(size), "sizeunit": unit})
            return size_mb
        except Exception:
            continue

    return 0
def _extract_title(soup):
    font_red = soup.find("font", {"color": "red"})
    titre = font_red.get_text(strip=True) if font_red else None
    return titre

def _fetch_detail_metadata(shared_state, source_url, headers, current_host):
    updated_host = current_host
    production_year = ""
    size_mb = 0
    detail_title = None

    if not source_url:
        return updated_host, production_year, size_mb, detail_title

    try:
        response = requests.get(source_url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        debug(f"{hostname.upper()} failed to load detail page {source_url}: {exc}")
        return updated_host, production_year, size_mb, detail_title

    updated_host = _update_hostname(shared_state, current_host, response.url)

    try:
        detail_soup = BeautifulSoup(response.text, "html.parser")
        text = detail_soup.get_text(" ", strip=True)
        title = _extract_title(detail_soup)
        production_year = _extract_production_year(text)
        size_mb = _extract_size_mb(shared_state, text) or 0
        if production_year:
            debug(
                f"{hostname.upper()} extracted production year '{production_year}' from {response.url}"
            )
        if size_mb:
            debug(
                f"{hostname.upper()} extracted size {size_mb} MB from {response.url}"
            )
        if title:
            detail_title = title
    except Exception as exc:
        debug(f"{hostname.upper()} failed to parse detail page {response.url}: {exc}")

    return updated_host, production_year, size_mb, detail_title


def _normalize_title(title):
    if not title:
        return title

    normalized = title.replace(" - ", "-")
    normalized = normalized.replace(" ", ".")
    normalized = normalized.replace("(", "").replace(")", "")
    return normalized


def _contains_year_token(text, year):
    if not text or not year:
        return False

    pattern = rf"(?<!\d){re.escape(year)}(?!\d)"
    return re.search(pattern, text) is not None


LANGUAGE_MARKERS = {
    "french",
    "truefrench",
    "multi",
    "vostfr",
    "vff",
    "vf",
    "vo",
    "english",
    "eng",
    "subfrench",
}

QUALITY_MARKERS = {
    "dvdrip",
    "bdrip",
    "hdrip",
    "brrip",
    "bluray",
    "hdtv",
    "webrip",
    "webdl",
    "web-dl",
    "hddvd",
    "uhd",
    "hds",
    "cam",
    "ts",
    "hdtc",
    "hdlight",
    "light",
    "remux",
}

CODEC_MARKERS = {
    "xvid",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "av1",
    "divx",
}

CONTAINER_MARKERS = {
    "avi",
    "mkv",
    "mp4",
    "mov",
}

AUDIO_MARKERS = {
    "aac",
    "ac3",
    "dts",
    "truehd",
    "atmos",
    "flac",
}

RESOLUTION_PATTERN = re.compile(r"^(?:\d{3,4}p|4k|8k|2160p|1080p|720p|480p)$", re.IGNORECASE)
BIT_DEPTH_PATTERN = re.compile(r"^(?:10bit|8bit)$", re.IGNORECASE)


def _tokenize_title(text):
    if not text:
        return []

    tokens = re.split(r"[\s._\-]+", text)
    cleaned = []
    for token in tokens:
        stripped = token.strip().strip("()[]{}")
        if stripped:
            cleaned.append(stripped)
    return cleaned


def _quality_insert_index(tokens):
    for idx, token in enumerate(tokens):
        lower = token.lower()
        if (
            lower in LANGUAGE_MARKERS
            or lower in QUALITY_MARKERS
            or lower in CODEC_MARKERS
            or lower in CONTAINER_MARKERS
            or lower in AUDIO_MARKERS
            or RESOLUTION_PATTERN.match(token)
            or BIT_DEPTH_PATTERN.match(token)
            or any(marker in lower for marker in ("4k", "2160p", "1080p", "720p", "480p", "hdr", "light"))
        ):
            return idx
    return len(tokens)


def _merge_quality_tokens(base_tokens, quality_text):
    quality_tokens = _tokenize_title(quality_text)
    if not quality_tokens:
        return base_tokens

    existing = {token.lower() for token in base_tokens}
    merged = list(base_tokens)
    for token in quality_tokens:
        if token.lower() not in existing:
            merged.append(token)
            existing.add(token.lower())
    return merged


def _ensure_year_position(tokens, detail_year, raw_tokens):
    if not tokens or not detail_year:
        return tokens

    target = detail_year.strip()
    try:
        year_index = next(idx for idx, token in enumerate(tokens) if token == target)
    except StopIteration:
        return tokens

    raw_tokens = raw_tokens or []
    if raw_tokens:
        name_boundary = _quality_insert_index(raw_tokens)
        if name_boundary == 0:
            name_boundary = len(raw_tokens)
    else:
        name_boundary = 0

    insert_at = min(name_boundary, len(tokens)) if name_boundary else 0

    if insert_at == 0 and raw_tokens:
        insert_at = len(raw_tokens)

    if insert_at >= len(tokens):
        insert_at = len(tokens)

    if year_index == insert_at or (insert_at > 0 and year_index == insert_at - 1):
        return tokens

    tokens = list(tokens)
    year_token = tokens.pop(year_index)
    if year_index < insert_at:
        insert_at -= 1
    tokens.insert(insert_at, year_token)
    return tokens


def _parse_results(shared_state,
                   soup,
                   base_url,
                   request_from,
                   mirror,
                   headers,
                   current_host,
                   search_string=None,
                   season=None,
                   episode=None,
                   imdb_id=None):
    releases = []
    category_id = _get_newznab_category_id(request_from)
    cards = soup.select("div.cover_global")

    debug(
        f"{hostname.upper()} parsing {len(cards)} cards from {base_url} "
        f"(requester={request_from}, mirror={mirror})"
    )

    metadata_cache = {}

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
            from datetime import datetime, timezone, timedelta

            mois_fr = {
                "janvier": "01", "février": "02", "mars": "03", "avril": "04",
                "mai": "05", "juin": "06", "juillet": "07", "août": "08",
                "septembre": "09", "octobre": "10", "novembre": "11", "décembre": "12"
            }

            def parse_date_fr(date_str, heure="14:34:00", offset_hours=1):
                jour, mois, annee = date_str.split(" ")
                mois_num = mois_fr[mois.lower()]
                
                dt = datetime.fromisoformat(f"{annee}-{mois_num}-{jour}T{heure}")
                tz = timezone(timedelta(hours=offset_hours))
                dt = dt.replace(tzinfo=tz)
                return dt.isoformat()

            detail_year = ""
            detail_size_mb = 0
            detail_title = None
            release_host = current_host

            if headers is not None:
                if source not in metadata_cache:
                    metadata_cache[source] = _fetch_detail_metadata(
                        shared_state,
                        source,
                        headers,
                        current_host,
                    )
                updated_host, detail_year, detail_size_mb, detail_title = metadata_cache[source]
                if updated_host:
                    current_host = updated_host
                    release_host = updated_host
                if detail_title:
                    title = detail_title

            mb = detail_size_mb or 0
            release_imdb_id = imdb_id

            raw_tokens = _tokenize_title(title) if title else []
            if not raw_tokens:
                raw_tokens = _tokenize_title(quality)

            final_tokens = list(raw_tokens)
            combined_for_year = list(raw_tokens)
            if quality:
                combined_for_year.extend(_tokenize_title(quality))

            if detail_year and raw_tokens and not _contains_year_token(" ".join(combined_for_year), detail_year):
                insert_at = _quality_insert_index(raw_tokens)
                final_tokens = raw_tokens[:insert_at] + [detail_year] + raw_tokens[insert_at:]

            if quality:
                final_tokens = _merge_quality_tokens(final_tokens, quality)

            if detail_year:
                final_tokens = _ensure_year_position(final_tokens, detail_year, raw_tokens)

            if not final_tokens and title:
                final_tokens = _tokenize_title(title)

            fallback_title = title or quality or ""
            final_title = _normalize_title(" ".join(final_tokens)) if final_tokens else _normalize_title(fallback_title)
            if not final_title:
                final_title = _normalize_title(fallback_title or "zt")
            size_bytes = mb * 1024 * 1024 if mb else 0

            payload = urlsafe_b64encode(
                f"{final_title}|{source}|{mirror}|{mb}|{release_imdb_id}".encode("utf-8")
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
                    "size": size_bytes,
                    "date": parse_date_fr(published),
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
        return ["films","autres-videos"]
    if "postman" in rf:
        return ["films"]
    if "sonarr" in rf:
        if "anime" in rf or "animé" in rf or "manga" in rf:
            return ["mangas"]
        return ["series"]
    return None


def _get_newznab_category_id(request_from):
    rf = (request_from or "").lower()
    if "sonarr" in rf:
        return "5000"
    if "lazylibrarian" in rf:
        return "7000"
    if "radarr" in rf or "postman" in rf:
        return "2000"
    return None


def zt_feed(shared_state, start_time, request_from, mirror=None):
    releases = []
    categories = _get_category(request_from)
    if not categories:
        debug(f"Skipping {hostname.upper()} feed for unsupported requester '{request_from}'.")
        return releases

    config = shared_state.values["config"]("Hostnames")
    zt = config.get(hostname)
    if not zt:
        info(f"{hostname.upper()} host missing in configuration. Feed aborted for requester '{request_from}'.")
        return releases
    releases_all = []
    for category in categories:

        url = f"https://{zt}/?p={category}"
        headers = {"User-Agent": shared_state.values["user_agent"]}

        info(
            f"{hostname.upper()} feed request for category '{category}' "
            f"(mirror={mirror}) using host '{zt}'"
        )

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            zt = _update_hostname(shared_state, zt, response.url)
            soup = BeautifulSoup(response.text, "html.parser")
            releases = _parse_results(shared_state,
                                    soup,
                                    response.url,
                                    request_from,
                                    mirror,
                                    headers,zt)
            releases_all.extend(releases)
        except Exception as exc:
            message = f"Error loading {hostname.upper()} feed: {exc}"
            info(message)
            raise RuntimeError(message) from exc

    debug(f"Time taken: {time.time() - start_time:.2f}s ({hostname})")
    return releases_all


def zt_search(shared_state,
              start_time,
              request_from,
              search_string,
              mirror=None,
              season=None,
              episode=None):
    releases = []
    categories = _get_category(request_from)
    if not categories:
        debug(f"Skipping {hostname.upper()} search for unsupported requester '{request_from}'.")
        return releases

    config = shared_state.values["config"]("Hostnames")
    zt = config.get(hostname)
    if not zt:
        info(f"{hostname.upper()} host missing in configuration. Search aborted for '{search_string}'.")
        return releases


    imdb_id = shared_state.is_imdb_id(search_string)
    if imdb_id:
        localized = get_localized_title(shared_state, imdb_id, 'fr')
        if not localized:
            info(f"Could not extract title from IMDb-ID {imdb_id}")
            return releases
        search_string = html.unescape(localized)
        info(localized)
    q = quote_plus(search_string)[:32]
    releases_all = []
    for category in categories:
        for i in range(1,4):
            url = f"https://{zt}/?p={category}&search={q}&page={i}"
            headers = {"User-Agent": shared_state.values["user_agent"]}

            info(
                f"{hostname.upper()} search request for '{search_string}' "
                f"(category={category}, mirror={mirror}) using host '{zt}'"
            )

            try:
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                zt = _update_hostname(shared_state, zt, response.url)
                current_host = zt
                soup = BeautifulSoup(response.text, "html.parser")
                releases = _parse_results(shared_state,
                                        soup,
                                        response.url,
                                        request_from,
                                        mirror,
                                        headers,
                                        current_host= current_host,
                                        search_string=search_string,
                                        season=season,
                                        episode=episode,
                                        imdb_id=imdb_id)
                releases_all.extend(releases)
            except Exception as exc:
                message = f"Error loading {hostname.upper()} search: {exc}"
                info(message)
                raise RuntimeError(message) from exc

    
    debug(f"Time taken: {time.time() - start_time:.2f}s ({hostname})")
    return releases_all