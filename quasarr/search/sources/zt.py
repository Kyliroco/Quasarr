# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

"""Maison Energy search source."""

import html
import re
import time
import unicodedata
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


def _extract_year_from_highlight(soup):
    """Return the release year advertised in the highlighted filename block."""
    if not soup:
        return ""

    matches = []
    year_pattern = re.compile(r"(?:19|20)\d{2}")
    for highlight in soup.find_all("font", {"color": "red"}):
        text = highlight.get_text(" ", strip=True)
        if not text:
            continue
        matches.extend(match.group(0) for match in year_pattern.finditer(text))

    if matches:
        return matches[-1]
    return ""


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
def _extract_detail_title(soup):
    if not soup:
        return None

    title_tag = soup.find("h1")
    if title_tag:
        text = title_tag.get_text(strip=True)
        if text:
            return text

    font_red = soup.find("font", {"color": "red"})
    if font_red:
        text = font_red.get_text(strip=True)
        if text:
            return text
    return None


def _extract_quality_language_tokens(soup):
    if not soup:
        return []

    def candidate_quality_texts():
        for tag in soup.find_all(["div", "span", "p", "strong"]):
            text = tag.get_text(" ", strip=True)
            if not text:
                continue
            lowered = text.lower()
            if not (lowered.startswith("qualité") or lowered.startswith("qualite")):
                continue
            suffix = lowered[7:]  # characters following "qualité"
            if suffix and suffix[0].isalpha():
                # Skip plurals such as "qualités" or other words that continue with letters.
                continue
            if "egalement" in lowered or "également" in lowered:
                continue
            yield text

    selected_text = ""
    for candidate in candidate_quality_texts():
        selected_text = candidate
        if "|" in candidate or len(candidate) <= 64:
            break

    if not selected_text:
        return []

    match = re.search(r"qualit(?:é|e)\s*[:\-]?\s*(.+)", selected_text, re.IGNORECASE)
    remainder = match.group(1).strip() if match else ""
    if not remainder:
        return []

    parts = [part.strip(" -") for part in remainder.split("|")]
    tokens = []
    for part in parts:
        cleaned = re.sub(r"[()\[\]]", "", part).strip()
        if not cleaned:
            continue
        cleaned = re.sub(r"\s+", ".", cleaned)
        if cleaned:
            tokens.append(cleaned)

    if len(tokens) <= 1:
        language_text = None
        lang_pattern = re.compile(r"langue", re.IGNORECASE)
        lang_tag = soup.find(string=lang_pattern)
        if lang_tag:
            container = lang_tag.find_parent(["div", "span", "p", "strong"])
            if not container:
                container = lang_tag.parent
            if container:
                language_text = container.get_text(" ", strip=True)
        if language_text:
            match_lang = re.search(r"langue\s*[:\-]?\s*(.+)", language_text, re.IGNORECASE)
            if match_lang:
                lang_remainder = match_lang.group(1).strip()
                if lang_remainder:
                    cleaned_lang = re.sub(r"[()\[\]]", "", lang_remainder)
                    cleaned_lang = re.sub(r"\s+", ".", cleaned_lang.strip())
                    if cleaned_lang:
                        tokens.append(cleaned_lang)

    return tokens

def _fetch_detail_metadata(shared_state, source_url, headers, current_host):
    updated_host = current_host
    production_year = ""
    size_mb = 0
    detail_title = None
    quality_tokens = []

    if not source_url:
        return updated_host, production_year, size_mb, detail_title, quality_tokens

    try:
        response = requests.get(source_url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        debug(f"{hostname.upper()} failed to load detail page {source_url}: {exc}")
        return updated_host, production_year, size_mb, detail_title, quality_tokens

    updated_host = _update_hostname(shared_state, current_host, response.url)

    try:
        detail_soup = BeautifulSoup(response.text, "html.parser")
        text = detail_soup.get_text(" ", strip=True)
        title = _extract_detail_title(detail_soup)
        highlighted_year = _extract_year_from_highlight(detail_soup)
        production_year = highlighted_year or _extract_production_year(text)
        size_mb = _extract_size_mb(shared_state, text) or 0
        quality_tokens = _extract_quality_language_tokens(detail_soup)
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

    return updated_host, production_year, size_mb, detail_title, quality_tokens


def _strip_parenthetical_content(text):
    if not text:
        return text

    # Remove any parenthetical segments and collapse leftover whitespace.
    stripped = re.sub(r"\s*\([^)]*\)", "", text)
    return re.sub(r"\s+", " ", stripped).strip()


def _normalize_title(title):
    if not title:
        return title

    normalized = title.replace(" - ", "-")
    normalized = normalized.replace(" ", ".")
    normalized = normalized.replace("(", "").replace(")", "")
    return normalized


def _normalize_quality_token(token):
    if not token:
        return token

    if re.fullmatch(r"hdrip", token, re.IGNORECASE):
        return "HDTV 720p"

    if re.search(r"4k", token, re.IGNORECASE):
        spaced = re.sub(r"[._-]+", " ", token)
        spaced = re.sub(
            r"4k([A-Za-z0-9]+)",
            lambda match: f"2160p {match.group(1)}",
            spaced,
            flags=re.IGNORECASE,
        )
        spaced = re.sub(
            r"([A-Za-z0-9]+)4k",
            lambda match: f"{match.group(1)} 2160p",
            spaced,
            flags=re.IGNORECASE,
        )
        spaced = re.sub(r"\b4k\b", "2160p", spaced, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+", " ", spaced).strip()
        if normalized:
            return normalized
    return token


def _contains_year_token(text, year):
    if not text or not year:
        return False

    pattern = rf"(?<!\d){re.escape(year)}(?!\d)"
    return re.search(pattern, text) is not None


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


def _extract_year_from_tokens(tokens):
    if not tokens:
        return ""

    for token in reversed(tokens):
        if re.fullmatch(r"19\d{2}|20\d{2}", token):
            return token
    return ""


def _append_component(components, seen, component):
    if not component:
        return

    normalized = _normalize_title(component)
    if not normalized:
        return

    key = normalized.lower()
    if key in seen:
        return

    seen.add(key)
    components.append(normalized)


def _build_final_title(title_source,
                       listing_title,
                       release_year,
                       detail_quality_tokens,
                       quality_text):
    components = []
    seen_components = set()

    primary_title = title_source or listing_title or ""
    normalized_primary = _normalize_title(primary_title) if primary_title else ""

    if normalized_primary:
        _append_component(components, seen_components, primary_title)

    if release_year and not _contains_year_token(normalized_primary, release_year):
        _append_component(components, seen_components, release_year)

    quality_components = list(detail_quality_tokens or [])
    if not quality_components and quality_text:
        quality_components = _tokenize_title(quality_text)

    for token in quality_components:
        normalized_quality = _normalize_quality_token(token)
        _append_component(components, seen_components, normalized_quality)

    if not components:
        fallback_title = primary_title or listing_title or quality_text or hostname
        _append_component(components, seen_components, fallback_title)

    return ".".join(filter(None, components))


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
            detail_quality_tokens = []
            listing_title = title

            if headers is not None:
                if source not in metadata_cache:
                    metadata_cache[source] = _fetch_detail_metadata(
                        shared_state,
                        source,
                        headers,
                        current_host,
                    )
                (
                    updated_host,
                    detail_year,
                    detail_size_mb,
                    detail_title,
                    detail_quality_tokens,
                ) = metadata_cache[source]
                if updated_host:
                    current_host = updated_host
                if detail_title:
                    title = detail_title

            mb = detail_size_mb or 0
            release_imdb_id = imdb_id

            listing_tokens = _tokenize_title(listing_title)
            release_year = _extract_year_from_tokens(listing_tokens)
            if not release_year and quality:
                release_year = _extract_year_from_tokens(_tokenize_title(quality))
            if not release_year:
                release_year = detail_year

            title_source = title or listing_title or ""
            final_title = _build_final_title(
                title_source,
                listing_title,
                release_year,
                detail_quality_tokens,
                quality,
            )
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

            stripped_title_source = _strip_parenthetical_content(title_source)
            if stripped_title_source and stripped_title_source != title_source:
                stripped_final_title = _build_final_title(
                    stripped_title_source,
                    listing_title,
                    release_year,
                    detail_quality_tokens,
                    quality,
                )

                if stripped_final_title and stripped_final_title != final_title:
                    stripped_payload = urlsafe_b64encode(
                        f"{stripped_final_title}|{source}|{mirror}|{mb}|{release_imdb_id}".encode("utf-8")
                    ).decode("utf-8")

                    stripped_link = (
                        f"{shared_state.values['internal_address']}/download/?payload={stripped_payload}"
                    )

                    releases.append({
                        "details": {
                            "title": stripped_final_title,
                            "hostname": hostname,
                            "imdb_id": release_imdb_id,
                            "link": stripped_link,
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

        debug(
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
        debug(localized)
    def _strip_diacritics(text):
        if not text:
            return text
        normalized = unicodedata.normalize("NFD", text)
        return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")

    seen_links = set()
    aggregated_releases = []

    def perform_query(raw_query):
        nonlocal zt

        # The Zone-Téléchargement search form limits inputs to 32 characters.
        # Apply the same limit *before* percent-encoding so multibyte characters
        # (e.g. "ê" → "%C3%A8") still count as a single character like in the UI.
        limited_search = (raw_query or "")[:32]
        q = quote_plus(limited_search)

        for category in categories:
            for i in range(1, 4):
                url = f"https://{zt}/?p={category}&search={q}&page={i}"
                headers = {"User-Agent": shared_state.values["user_agent"]}

                debug(
                    f"{hostname.upper()} search request for '{raw_query}' "
                    f"(category={category}, mirror={mirror}) using host '{zt}'"
                )

                try:
                    response = requests.get(url, headers=headers, timeout=10)
                    response.raise_for_status()
                    zt = _update_hostname(shared_state, zt, response.url)
                    current_host = zt
                    soup = BeautifulSoup(response.text, "html.parser")
                    found = _parse_results(
                        shared_state,
                        soup,
                        response.url,
                        request_from,
                        mirror,
                        headers,
                        current_host=current_host,
                        search_string=raw_query,
                        season=season,
                        episode=episode,
                        imdb_id=imdb_id,
                    )
                    for release in found:
                        link = release.get("details", {}).get("link")
                        if link:
                            if link in seen_links:
                                continue
                            seen_links.add(link)
                        aggregated_releases.append(release)
                except Exception as exc:
                    message = f"Error loading {hostname.upper()} search: {exc}"
                    info(message)
                    raise RuntimeError(message) from exc

    perform_query(search_string)

    accentless = _strip_diacritics(search_string)
    if accentless and accentless != search_string:
        perform_query(accentless)

    debug(f"Time taken: {time.time() - start_time:.2f}s ({hostname})")
    return aggregated_releases
