# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

"""Maison Energy search source."""

import html
import re
import time
import unicodedata
from base64 import urlsafe_b64encode
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from quasarr.providers.imdb_metadata import get_localized_title
from quasarr.providers.log import info, debug
from quasarr.providers.shared_state import normalize_localized_season_episode_tags

hostname = "zt"

SUPPORTED_MIRRORS = {
    "rapidgator",
    "1fichier",
    "turbobit",
    "uploady",
    "dailyuploads",
}
UNSUPPORTED_HOSTERS = {"nitroflare"}
STREAM_QUERY_TOKENS = {"a1", "b1", "h1"}


def _normalize_hoster_name(host: str) -> str:
    host = (host or "").strip().lower()
    cleaned = (
        host.replace(" ", "")
            .replace("-", "")
            .replace("_", "")
            .replace(".", "")
    )
    if cleaned.startswith("rapidgator"):
        return "rapidgator"
    if cleaned in {"ddownload", "ddl", "ddlto", "ddownlaod", "ddown"}:
        return "ddownload"
    if cleaned.startswith("1fichier"):
        return "1fichier"
    if "nitro" in cleaned:
        return "nitroflare"
    if "turbobit" in cleaned:
        return "turbobit"
    if "uploady" in cleaned:
        return "uploady"
    if "dailyuploads" in cleaned or "dailyupload" in cleaned:
        return "dailyuploads"
    return host


def _episode_numbers_from_text(text: str):
    numbers = set()
    if not text:
        return numbers

    pattern = re.compile(r"(?i)(?:é?pisode|ep)\s*(\d{1,3})(?:\s*[-à]\s*(\d{1,3}))?")
    for match in pattern.finditer(text):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        for value in range(start, end + 1):
            numbers.add(value)

    if not numbers:
        for raw in re.findall(r"\d{1,3}", text):
            try:
                numbers.add(int(raw))
            except ValueError:
                continue

    return numbers


def _append_host_to_title(title: str, host: str) -> str:
    if not title:
        return title

    host_component = re.sub(r"[^A-Za-z0-9]+", "", host or "")
    if not host_component:
        return title

    if re.search(rf"(?i){re.escape(host_component)}", title):
        return title

    return f"{title}.{host_component.capitalize()}"


def _append_language_to_title(title: str, language: str) -> str:
    if not title or not language:
        return title

    normalized = language.strip().lower()
    if normalized != "french":
        return title

    if re.search(r"(?i)\.french(?:\.|$)", title):
        return title

    return f"{title}.French"


def _extract_supported_mirrors(detail_soup):
    """
    Parcourt les blocs <div class="postinfo"> et extrait les mirrors supportés.
    Structure typique répétée :
        <b><div>HostName</div></b>
        <b><a href="...&rl=a2">Télécharger</a></b><br><br>
    On ne retient que les liens de téléchargement (rl=a2).
    """
    supported = set()

    for post in detail_soup.select("div.postinfo"):
        # On cherche les <b> qui contiennent un <div> (le nom d'hébergeur)
        for host_b in post.select("b"):
            host_div = host_b.find("div")
            if not host_div:
                continue

            host = _normalize_hoster_name(host_div.get_text(strip=True))
            if host in UNSUPPORTED_HOSTERS:
                continue

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


def _collect_download_entries(detail_soup, base_url):
    entries = []
    seen_urls = set()
    current_host = None
    skip_current_host = False

    for block in detail_soup.select("div.postinfo"):
        for bold in block.find_all("b"):
            host_div = bold.find("div")
            if host_div:
                host_name = _normalize_hoster_name(host_div.get_text(strip=True))
                if not host_name:
                    current_host = None
                    skip_current_host = False
                    continue

                if host_name in UNSUPPORTED_HOSTERS or host_name not in SUPPORTED_MIRRORS:
                    current_host = None
                    skip_current_host = True
                    continue

                current_host = host_name
                skip_current_host = False
                continue

            anchor = bold.find("a", href=True)
            if not anchor:
                continue

            if skip_current_host:
                continue

            href = urljoin(base_url, anchor["href"])
            if href in seen_urls:
                continue

            parsed = urlparse(href)
            if parsed.scheme.lower() not in {"http", "https"}:
                continue

            rl_tokens = {value.lower() for values in parse_qs(parsed.query).values() for value in values}
            if rl_tokens & STREAM_QUERY_TOKENS:
                continue

            host_for_entry = current_host
            if not host_for_entry:
                netloc = parsed.netloc.lower()
                host_for_entry = _normalize_hoster_name(
                    netloc.split(".")[-2] if "." in netloc else netloc
                )

            if (
                not host_for_entry
                or host_for_entry in UNSUPPORTED_HOSTERS
                or host_for_entry not in SUPPORTED_MIRRORS
            ):
                continue

            display = anchor.get_text(" ", strip=True)
            episodes = frozenset(_episode_numbers_from_text(display))

            entries.append({
                "url": href,
                "host": host_for_entry,
                "display": display,
                "episodes": episodes,
            })
            seen_urls.add(href)

    return entries


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
    available_episodes = set()
    download_entries = []

    if not source_url:
        return (
            updated_host,
            production_year,
            size_mb,
            detail_title,
            quality_tokens,
            available_episodes,
            download_entries,
        )

    try:
        response = requests.get(source_url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        debug(f"{hostname.upper()} failed to load detail page {source_url}: {exc}")
        return (
            updated_host,
            production_year,
            size_mb,
            detail_title,
            quality_tokens,
            available_episodes,
            download_entries,
        )

    updated_host = _update_hostname(shared_state, current_host, response.url)

    try:
        detail_soup = BeautifulSoup(response.text, "html.parser")
        text = detail_soup.get_text(" ", strip=True)
        title = _extract_detail_title(detail_soup)
        highlighted_year = _extract_year_from_highlight(detail_soup)
        production_year = highlighted_year or _extract_production_year(text)
        size_mb = _extract_size_mb(shared_state, text) or 0
        quality_tokens = _extract_quality_language_tokens(detail_soup)
        download_entries = _collect_download_entries(detail_soup, response.url)
        episode_pattern = re.compile(
            r"(?i)\b(?:ep(?:isode)?)\s*(\d{1,3})(?:\s*[-à]\s*(\d{1,3}))?"
        )
        for link in detail_soup.select("div.postinfo b a"):
            link_text = link.get_text(" ", strip=True)
            if not link_text:
                continue
            for start_str, end_str in episode_pattern.findall(link_text):
                try:
                    start_ep = int(start_str)
                except ValueError:
                    continue
                if end_str:
                    try:
                        end_ep = int(end_str)
                    except ValueError:
                        end_ep = start_ep
                    for ep_num in range(start_ep, end_ep + 1):
                        available_episodes.add(ep_num)
                else:
                    available_episodes.add(start_ep)
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

    return (
        updated_host,
        production_year,
        size_mb,
        detail_title,
        quality_tokens,
        available_episodes,
        download_entries,
    )


def _strip_parenthetical_content(text):
    if not text:
        return text

    # Remove any parenthetical segments and collapse leftover whitespace.
    stripped = re.sub(r"\s*\([^)]*\)", "", text)
    return re.sub(r"\s+", " ", stripped).strip()


def _normalize_title(title):
    if not title:
        return title

    normalized = normalize_localized_season_episode_tags(title)
    normalized = normalized.replace(" - ", "-")
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


_RESOLUTION_TOKEN_PATTERN = re.compile(r"(?i)(?:\b[1-9]\d{2,3}p\b|\b4k\b|\buhd\b)")


def _normalize_series_quality_hint(text):
    if not text:
        return ""

    cleaned = re.sub(r"[\[\]()]", " ", str(text))
    cleaned = re.sub(r"[._-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def _coerce_series_quality_tokens(is_series_request, quality_text, detail_tokens):
    if not is_series_request:
        return quality_text, list(detail_tokens or [])

    tokens = list(detail_tokens or [])
    hints = tokens[:]
    if quality_text:
        hints.append(quality_text)

    if not hints:
        return quality_text, tokens

    for hint in hints:
        if _RESOLUTION_TOKEN_PATTERN.search(str(hint or "")):
            return quality_text, tokens

    normalized_hints = []
    for hint in hints:
        normalized_hint = _normalize_series_quality_hint(hint)
        if normalized_hint:
            normalized_hints.append(normalized_hint)
    normalized_set = set(normalized_hints)

    resolution = None
    if (
        {"vf hd", "vfhd"} & normalized_set
        or ("vf" in normalized_set and "hd" in normalized_set)
    ):
        resolution = "720p"
    elif (
        {"vostfr hd", "vostfrhd"} & normalized_set
        or ("vostfr" in normalized_set and "hd" in normalized_set)
    ):
        resolution = "720p"
    elif "vf" in normalized_set:
        resolution = "480p"
    elif "vostfr" in normalized_set:
        resolution = "480p"

    if not resolution:
        return quality_text, tokens

    if not any(_RESOLUTION_TOKEN_PATTERN.search(str(token or "")) for token in tokens):
        tokens.append(resolution)

    if quality_text:
        if not _RESOLUTION_TOKEN_PATTERN.search(quality_text):
            quality_text = f"{quality_text} {resolution}".strip()
    else:
        quality_text = resolution

    return quality_text, tokens


def _derive_language(hints):
    tokens = set()
    for hint in hints or ():
        if not hint:
            continue
        text = str(hint).strip()
        if not text:
            continue
        lowered = text.lower()
        tokens.add(lowered)
        for part in re.split(r"[^a-z0-9]+", lowered):
            if part:
                tokens.add(part)

    if any("multi" in token for token in tokens):
        return "Multi"

    if any(
        token in {"vost", "vostfr", "vo"}
        or token.startswith("vost")
        or token.startswith("vo")
        or "vostfr" in token
        for token in tokens
    ):
        return "English"

    if any(
        token in {"vf", "french", "truefrench"}
        or token.startswith("vf")
        or "french" in token
        for token in tokens
    ):
        return "French"

    return None


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


_EPISODE_TAG_REGEX = re.compile(r"(?i)S\d{1,3}E\d{1,3}")


def _ensure_episode_tag(title, season, episode):
    if not title:
        return title

    try:
        season_num = int(season) if season is not None else None
        episode_num = int(episode) if episode is not None else None
    except (TypeError, ValueError):
        return title

    if season_num is None or episode_num is None:
        return title

    if _EPISODE_TAG_REGEX.search(title):
        return title

    season_tag = f"S{season_num:02d}"
    episode_tag = f"E{episode_num:02d}"

    def _inject_episode(match):
        return f"{match.group(0)}{episode_tag}"

    season_pattern = re.compile(rf"(?i){re.escape(season_tag)}")
    if season_pattern.search(title):
        return season_pattern.sub(_inject_episode, title, count=1)

    return f"{title}.{season_tag}{episode_tag}"


def _attach_episode_fragment(url, episode):
    try:
        episode_num = int(episode)
    except (TypeError, ValueError):
        return url

    parsed = urlparse(url)
    fragments = []
    if parsed.fragment:
        fragments = [
            frag for frag in parsed.fragment.split("&")
            if frag and not frag.startswith("episode=")
        ]
    fragments.append(f"episode={episode_num}")
    updated_fragment = "&".join(fragments)
    return urlunparse(parsed._replace(fragment=updated_fragment))


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
    request_lower = (request_from or "").lower()
    request_is_sonarr = "sonarr" in request_lower
    cards = soup.select("div.cover_global")

    debug(
        f"{hostname.upper()} parsing {len(cards)} cards from {base_url} "
        f"(requester={request_from}, mirror={mirror})"
    )

    metadata_cache = {}

    try:
        requested_season_num = int(season) if season is not None else None
    except (TypeError, ValueError):
        requested_season_num = None

    try:
        requested_episode_num = int(episode) if episode is not None else None
    except (TypeError, ValueError):
        requested_episode_num = None

    for card in cards:
        try:
            title_link = card.select_one("div.cover_infos_title a")
            if not title_link:
                debug(f"{hostname.upper()} skipping card without title link on {base_url}")
                continue
            raw_title = title_link.get_text(strip=True)
            if not raw_title:
                debug(f"{hostname.upper()} skipping card with empty title on {base_url}")
                continue

            require_episode_verification = False
            if search_string is not None:
                if not shared_state.is_valid_release(raw_title,
                                                     request_from,
                                                     search_string,
                                                     season,
                                                     episode):
                    if (
                        request_is_sonarr
                        and season is not None
                        and episode is not None
                        and shared_state.is_valid_release(
                            raw_title,
                            request_from,
                            search_string,
                            season,
                            None,
                        )
                    ):
                        require_episode_verification = True
                    else:
                        debug(
                            f"{hostname.upper()} filtered title '{raw_title}' "
                            f"for requester={request_from}, search='{search_string}'"
                        )
                        continue

            if "lazylibrarian" in request_lower:
                title = shared_state.normalize_magazine_title(raw_title)
            else:
                title = shared_state.normalize_localized_season_episode_tags(raw_title)

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
            detail_available_episodes = set()
            detail_download_entries = []

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
                    detail_available_episodes,
                    detail_download_entries,
                ) = metadata_cache[source]
                if updated_host:
                    current_host = updated_host
                if detail_title:
                    if "lazylibrarian" not in request_lower:
                        detail_title = shared_state.normalize_localized_season_episode_tags(detail_title)
                    title = detail_title

            available_episode_numbers = set(detail_available_episodes)
            for entry in detail_download_entries:
                available_episode_numbers.update(entry.get("episodes", ()))

            target_episode = None
            if require_episode_verification:
                try:
                    requested_episode = int(episode)
                except (TypeError, ValueError):
                    requested_episode = None
                if (
                    requested_episode is None
                    or (
                        available_episode_numbers
                        and requested_episode not in available_episode_numbers
                    )
                ):
                    debug(
                        f"{hostname.upper()} skipping '{title}' because episode "
                        f"{episode} not found in detail page"
                    )
                    continue
                target_episode = requested_episode
            elif request_is_sonarr and requested_episode_num is not None:
                target_episode = requested_episode_num
                if (
                    available_episode_numbers
                    and target_episode not in available_episode_numbers
                ):
                    debug(
                        f"{hostname.upper()} skipping '{title}' because episode "
                        f"{episode} not provided by card"
                    )
                    continue

            if not detail_download_entries:
                debug(
                    f"{hostname.upper()} detail page provided no download entries for '{title}'"
                )
                continue

            mb = detail_size_mb or 0
            release_imdb_id = imdb_id

            listing_tokens = _tokenize_title(listing_title)
            release_year = _extract_year_from_tokens(listing_tokens)
            if not release_year and quality:
                release_year = _extract_year_from_tokens(_tokenize_title(quality))
            if not release_year:
                release_year = detail_year

            quality, detail_quality_tokens = _coerce_series_quality_tokens(
                request_is_sonarr,
                quality,
                detail_quality_tokens,
            )

            title_source = title or listing_title or ""
            language_hints = list(detail_quality_tokens or [])
            if quality:
                language_hints.append(quality)
            language_hints.append(listing_title)
            if title_source and title_source != listing_title:
                language_hints.append(title_source)
            if detail_title and detail_title not in {title_source, listing_title}:
                language_hints.append(detail_title)
            if raw_title and raw_title not in {title_source, listing_title}:
                language_hints.append(raw_title)

            release_language = _derive_language(language_hints)

            final_title_base = _build_final_title(
                title_source,
                listing_title,
                release_year,
                detail_quality_tokens,
                quality,
            )

            title_language_tag = release_language
            final_title_base = _append_language_to_title(
                final_title_base, title_language_tag
            )
            if request_is_sonarr and requested_episode_num is not None:
                final_title_base = _ensure_episode_tag(
                    final_title_base, requested_season_num, requested_episode_num
                )

            stripped_title_source = _strip_parenthetical_content(title_source)
            stripped_final_title_base = None
            if stripped_title_source and stripped_title_source != title_source:
                stripped_final_title_base = _build_final_title(
                    stripped_title_source,
                    listing_title,
                    release_year,
                    detail_quality_tokens,
                    quality,
                )
                stripped_final_title_base = _append_language_to_title(
                    stripped_final_title_base, title_language_tag
                )
                if request_is_sonarr and requested_episode_num is not None:
                    stripped_final_title_base = _ensure_episode_tag(
                        stripped_final_title_base, requested_season_num, requested_episode_num
                    )

            size_bytes = mb * 1024 * 1024 if mb else 0
            release_date = parse_date_fr(published)

            non_rapidgator_entries = [
                entry for entry in detail_download_entries if entry.get("host") != "rapidgator"
            ]
            rapidgator_entries = [
                entry for entry in detail_download_entries if entry.get("host") == "rapidgator"
            ]
            ordered_entries = non_rapidgator_entries + rapidgator_entries

            added_entry = False
            for entry in ordered_entries:
                entry_url = entry.get("url")
                if not entry_url:
                    continue
                entry_host = entry.get("host", "")
                if mirror and mirror.lower() not in entry_host.lower():
                    continue

                entry_episodes = set(entry.get("episodes", ()))
                if target_episode is not None:
                    if not entry_episodes:
                        debug(
                            f"{hostname.upper()} skipping unlabeled link {entry_url} "
                            f"while targeting episode {target_episode}"
                        )
                        continue
                    if target_episode not in entry_episodes:
                        debug(
                            f"{hostname.upper()} skipping '{entry_url}' because it covers episodes "
                            f"{sorted(entry_episodes)}"
                        )
                        continue
                    if len(entry_episodes) > 1:
                        debug(
                            f"{hostname.upper()} skipping pack link {entry_url} covering episodes "
                            f"{sorted(entry_episodes)}"
                        )
                        continue

                entry_episode_for_payload = target_episode
                if (
                    entry_episode_for_payload is None
                    and len(entry_episodes) == 1
                ):
                    entry_episode_for_payload = next(iter(entry_episodes))

                entry_final_title_base = final_title_base
                entry_stripped_title_base = stripped_final_title_base
                if (
                    request_is_sonarr
                    and requested_season_num is not None
                    and entry_episode_for_payload is not None
                ):
                    entry_final_title_base = _ensure_episode_tag(
                        entry_final_title_base,
                        requested_season_num,
                        entry_episode_for_payload,
                    )
                    if entry_stripped_title_base:
                        entry_stripped_title_base = _ensure_episode_tag(
                            entry_stripped_title_base,
                            requested_season_num,
                            entry_episode_for_payload,
                        )

                entry_payload_source = _attach_episode_fragment(
                    entry_url, entry_episode_for_payload
                )
                entry_mirror = entry_host or mirror
                if entry_mirror is None:
                    entry_mirror = "None"

                entry_final_title = _append_host_to_title(
                    entry_final_title_base, entry_host
                )

                payload = urlsafe_b64encode(
                    f"{entry_final_title}|{entry_payload_source}|{entry_mirror}|{mb}|{release_imdb_id}".encode("utf-8")
                ).decode("utf-8")

                link = f"{shared_state.values['internal_address']}/download/?payload={payload}"

                debug(
                    f"{hostname.upper()} prepared release '{entry_final_title}' with source {entry_payload_source}"
                )

                details = {
                    "title": entry_final_title,
                    "hostname": hostname,
                    "imdb_id": release_imdb_id,
                    "link": link,
                    "mirror": entry_mirror,
                    "size": size_bytes,
                    "date": release_date,
                    "source": entry_payload_source,
                }
                if release_language:
                    details["language"] = release_language

                releases.append({
                    "details": details,
                    "type": "protected",
                })
                added_entry = True

                if (
                    entry_stripped_title_base
                    and entry_stripped_title_base != entry_final_title_base
                ):
                    stripped_entry_title = _append_host_to_title(
                        entry_stripped_title_base, entry_host
                    )
                    if stripped_entry_title and stripped_entry_title != entry_final_title:
                        stripped_payload = urlsafe_b64encode(
                            f"{stripped_entry_title}|{entry_payload_source}|{entry_mirror}|{mb}|{release_imdb_id}".encode("utf-8")
                        ).decode("utf-8")

                        stripped_link = (
                            f"{shared_state.values['internal_address']}/download/?payload={stripped_payload}"
                        )

                        stripped_details = {
                            "title": stripped_entry_title,
                            "hostname": hostname,
                            "imdb_id": release_imdb_id,
                            "link": stripped_link,
                            "mirror": entry_mirror,
                            "size": size_bytes,
                            "date": release_date,
                            "source": entry_payload_source,
                        }
                        if release_language:
                            stripped_details["language"] = release_language

                        releases.append({
                            "details": stripped_details,
                            "type": "protected",
                        })

            if not added_entry:
                debug(
                    f"{hostname.upper()} no eligible download entries remained for '{title}'"
                )
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
        if season:
            localized = get_localized_title(shared_state, imdb_id, 'fr')
            original = None
        else:
            localized, original = get_localized_title(shared_state, imdb_id, 'fr',True)
        if not localized:
            info(f"Could not extract title from IMDb-ID {imdb_id}")
            return releases
        search_string = html.unescape(localized)
        if original:
            search_original= html.unescape(original)
        else:
            search_original = None
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
            page = 1
            found_any_release = False

            while True and page<10:
                url = f"https://{zt}/?p={category}&search={q}&page={page}"
                headers = {"User-Agent": shared_state.values["user_agent"]}

                debug(
                    f"{hostname.upper()} search request for '{raw_query}' "
                    f"(category={category}, page={page}, mirror={mirror}) using host '{zt}'"
                )

                try:
                    response = requests.get(url, headers=headers, timeout=10)
                    response.raise_for_status()
                    zt = _update_hostname(shared_state, zt, response.url)
                    current_host = zt
                    soup = BeautifulSoup(response.text, "html.parser")
                    cards = soup.select("div.cover_global")
                    debug(f"len cards : len(cards)")
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
                    debug(f"found : {found}")
                    matched_on_page = 0
                    for release in found:
                        link = release.get("details", {}).get("link")
                        debug(f"link :{link}")
                        if link:
                            if link in seen_links:
                                continue
                            seen_links.add(link)
                        aggregated_releases.append(release)
                        matched_on_page += 1

                    if matched_on_page:
                        found_any_release = True
                        diff_page=3

                    if not cards:
                        break

                    if matched_on_page == 0 and found_any_release:
                        if diff_page == 0:
                            break
                        else:
                            diff_page-= 1

                    page += 1
                except Exception as exc:
                    message = f"Error loading {hostname.upper()} search: {exc}"
                    info(message)
                    raise RuntimeError(message) from exc

    perform_query(search_string)
    accentless = _strip_diacritics(search_string)
    if accentless and accentless != search_string:
        perform_query(accentless)
    if search_original and search_original != search_string:
        perform_query(search_original)
        search_original_accentless = _strip_diacritics(search_original)
        if search_original_accentless and search_original_accentless != search_original:
            perform_query(search_original_accentless)
    debug(f"Time taken: {time.time() - start_time:.2f}s ({hostname})")
    return aggregated_releases
