# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

"""anime-sama.to search source (streaming → yt-dlp).

Contrairement aux autres sources (qui pointent vers des hébergeurs de fichiers
résolus par JDownloader), anime-sama est un site de streaming. La vraie source
des liens est un fichier ``episodes.js`` par saison/langue qui contient des
tableaux ``var eps1 = [...]`` (un par lecteur), indexés par numéro d'épisode.
L'iframe ``#playerDF`` de la page est remplie côté client par du JavaScript à
partir de ces tableaux : il ne faut donc PAS scraper l'iframe mais parser
``episodes.js`` directement.

Les releases produites ici sont téléchargées via yt-dlp (voir
``quasarr/downloads/ytdlp_worker.py``), pas via JDownloader.
"""

import html
import random
import re
import time
import unicodedata
from base64 import urlsafe_b64encode
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from quasarr.providers.imdb_metadata import (
    get_localized_title,
    get_romaji_title,
    get_season_episode_counts,
    get_year,
)
from quasarr.providers.tvdb_metadata import (
    get_absolute_number as tvdb_absolute_number,
    get_season_absolute_numbers as tvdb_season_absolute_numbers,
)
from quasarr.providers.players import register_player, is_player_enabled
from quasarr.providers.log import info, debug, error, log_event

hostname = "am"

# Langues anime-sama, par ordre de préférence : on privilégie le VOSTFR, et on
# retombe sur la VF si une série/saison n'existe qu'en VF (cas fréquent des
# animes Netflix, ex. Aggretsuko). Pour du strict VOSTFR, mettre ["vostfr"].
LANGUAGES = ["vostfr", "vf"]

# Tag de langue injecté dans le titre de release (Sonarr reconnaît "FRENCH").
_LANGUAGE_TAGS = {"vostfr": "VOSTFR", "vf": "FRENCH"}

# Hébergeurs d'embed à exclure d'office (ne marchent pas / pas supportés par
# yt-dlp). On stocke des sous-chaînes de domaine. "anime-sama" est exclu pour
# ne jamais traiter la page elle-même comme un embed. Ajoute ici les hôtes que
# tu constates défaillants (ex. "smoothpre", "vudeo", ...).
AM_BLOCKED_HOSTS = {"anime-sama"}

# Tailles estimées (anime-sama ne fournit aucune taille de fichier ; Radarr /
# Sonarr exigent une valeur pour accepter la release et juger la qualité).
EPISODE_SIZE_MB = 450
FILM_SIZE_MB = 1500

# Suffixe qualité/release synthétique (anime-sama n'expose pas la résolution).
# La langue est préfixée dynamiquement selon le résultat (VOSTFR / FRENCH).
RELEASE_QUALITY = "1080p.WEB.x264-ANIMESAMA"

# Jitter anti-blocage appliqué avant chaque chargement HTTP anime-sama. Les
# requêtes restent indépendantes : une recherche peut continuer pendant que le
# worker yt-dlp télécharge un autre épisode.
MIN_REQUEST_DELAY = 0.8
MAX_REQUEST_DELAY = 5.0

_NUM_RE = re.compile(r"(\d+)")


def _am_request(method, url, **kwargs):
    delay = random.uniform(MIN_REQUEST_DELAY, MAX_REQUEST_DELAY)
    debug(f"{hostname.upper()} waiting {delay:.2f}s before loading {url}")
    time.sleep(delay)
    return requests.request(method, url, **kwargs)


def _user_agent(shared_state):
    return shared_state.values.get("user_agent",
                                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/120.0.0.0 Safari/537.36")


def _slugify(text):
    """Titre → slug anime-sama (minuscules, sans accents, '-' au lieu des espaces)."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFD", text)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower()
    # TMDB renvoie souvent le signe multiplication "×" là où le slug attend un
    # "x" (ex. "Hunter × Hunter" → hunter-x-hunter, pas hunter-hunter).
    normalized = normalized.replace("×", "x")
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    return normalized.strip("-")


def _dotted_title(text):
    """Titre lisible → forme pointée pour un nom de release (ASCII, points)."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFD", text)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.replace("×", "x")
    normalized = normalized.replace("&", "and")
    normalized = re.sub(r"[^A-Za-z0-9]+", ".", normalized)
    return normalized.strip(".")


def _release_title(dotted, year, tag, release_suffix, host_tag):
    """Assemble un nom de release pointé, année incluse pour lever l'ambiguïté.

    ``Island`` + 2018 + ``S01E01`` -> ``Island.2018.S01E01.<suffix>.<host>``.
    L'année placée juste après le titre force Sonarr/Radarr à lire un CleanTitle
    « island2018 » : il n'existe aucune clé de scene mapping à ce nom, donc
    l'alias d'une autre œuvre homonyme (ex. drama TVDB 397727) ne peut plus
    détourner la release vers la mauvaise série. ``tag`` est le SxxExx (None pour
    un film). Voir ``imdb_metadata.get_year`` pour le détail du problème.
    """
    parts = [dotted]
    if year:
        parts.append(str(year))
    if tag:
        parts.append(tag)
    parts.append(release_suffix)
    parts.append(host_tag)
    return ".".join(parts)


def _host_of(url):
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _host_tag(url):
    """Nom lisible de l'hébergeur (lecteur) pour distinguer les releases dans
    Sonarr : sendvid.com → 'Sendvid', video.sibnet.ru → 'Sibnet'."""
    host = re.sub(r"[^A-Za-z0-9.]", "", _host_of(url))
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 2:
        label = parts[-2]  # second-level domain (avant le TLD)
    elif parts:
        label = parts[0]
    else:
        label = "player"
    return label.capitalize() or "Player"


def _update_hostname(shared_state, current_host, final_url):
    """anime-sama change régulièrement de TLD : suit les redirections."""
    try:
        final_host = urlparse(final_url).netloc.lower()
    except Exception:
        return current_host
    if final_host and current_host and final_host != current_host:
        info(f"{hostname.upper()} redirect detected. Updating hostname to '{final_host}'.")
        shared_state.values["config"]("Hostnames").save(hostname.lower(), final_host)
        return final_host
    return current_host


def _parse_episodes_js(text):
    """Parse ``episodes.js`` → {nom_lecteur: [url, ...]} (index = épisode-1)."""
    result = {}
    for match in re.finditer(r"var\s+(eps\w+)\s*=\s*\[(.*?)\]", text, re.S):
        name = match.group(1)
        items = re.findall(r"'([^']*)'|\"([^\"]*)\"", match.group(2))
        # Les entrées vides sont des positions d'épisode sans lien pour ce
        # lecteur. Les supprimer décalerait tous les épisodes suivants.
        urls = [(a or b).strip() for a, b in items]
        if any(urls):
            result[name] = urls
    return result


def _strict_episode_number(label):
    """Retourne X uniquement si le libellé complet est exactement ``EPISODE X``."""
    match = re.fullmatch(r"EPISODE\s+([1-9]\d*)", str(label or "").strip(), re.I)
    return int(match.group(1)) if match else None


def _parse_episode_index_map(page_text, total_items):
    """Mappe le numéro affiché vers l'index des tableaux ``eps*``.

    Anime-Sama construit la liste avec ``creerListe``, ``newSP`` et
    ``finirListe``. Les ``newSP`` consomment bien un index vidéo mais ne sont
    jamais considérés comme des épisodes, même si leur texte contient un nombre.
    """
    # Les pages contiennent des blocs d'exemple commentés avec de faux
    # resetListe/creerListe/finirListe. Ils ne doivent jamais remplacer la
    # configuration réellement exécutée (cas Assassination Classroom).
    executable_text = re.sub(r"<!--.*?-->", "", page_text or "", flags=re.S)
    executable_text = re.sub(r"/\*.*?\*/", "", executable_text, flags=re.S)
    resets = list(re.finditer(r"resetListe\s*\(\s*\)\s*;?", executable_text, re.I))
    if not resets or total_items <= 0:
        return {}
    # Le dernier reset est la configuration finale de la page. Les appels plus
    # haut sont le comportement générique remplacé ensuite par la liste spéciale.
    script = executable_text[resets[-1].end():]
    call_re = re.compile(
        r"creerListe\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)"
        r"|finirListe\s*\(\s*(\d+)\s*\)"
        r"|newSP\s*\(\s*(['\"])(.*?)\4\s*\)",
        re.I | re.S,
    )
    labels = []
    for call in call_re.finditer(script):
        if len(labels) >= total_items:
            break
        if call.group(1) is not None:
            start, end = int(call.group(1)), int(call.group(2))
            if end >= start:
                labels.extend(f"EPISODE {number}" for number in range(start, end + 1))
        elif call.group(3) is not None:
            number = int(call.group(3))
            while len(labels) < total_items:
                labels.append(f"EPISODE {number}")
                number += 1
        else:
            # Un spécial occupe une position, mais son libellé ne doit surtout
            # pas pouvoir satisfaire la regex stricte EPISODE X.
            labels.append(f"SPECIAL {call.group(5) or ''}")

    labels = labels[:total_items]
    result = {}
    for index, label in enumerate(labels):
        episode = _strict_episode_number(label)
        if episode is not None and episode not in result:
            result[episode] = index
    return result


def _ordered_player_arrays(eps_map):
    """Tableaux de lecteurs triés (eps1, eps2, … puis les non numérotés)."""
    def sort_key(name):
        m = _NUM_RE.search(name)
        return (0, int(m.group(1))) if m else (1, name)

    return [eps_map[name] for name in sorted(eps_map.keys(), key=sort_key)]


def _candidates_for_index(eps_map, index):
    """Liste ordonnée des embeds pour un index d'épisode, hôtes bloqués retirés."""
    candidates = []
    for urls in _ordered_player_arrays(eps_map):
        if 0 <= index < len(urls):
            url = urls[index]
            host = _host_of(url)
            if not url or any(blocked in host for blocked in AM_BLOCKED_HOSTS):
                continue
            if url not in candidates:
                candidates.append(url)
    return candidates


def _fetch_season_declarations(shared_state, am, slug, headers):
    """Récupère les saisons déclarées sur la fiche (panneauAnime). None si 404."""
    url = f"https://{am}/catalogue/{slug}/"
    try:
        response = _am_request("GET", url, headers=headers, timeout=10)
    except Exception as exc:
        debug(f"{hostname.upper()} failed to load catalogue page {url}: {exc}")
        return None, am
    am = _update_hostname(shared_state, am, response.url)
    if response.status_code != 200:
        return None, am
    declarations = re.findall(r'panneauAnime\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)', response.text)
    declarations = [(label, path) for label, path in declarations
                    if label.lower() != "nom" and path.lower() != "url"]
    return declarations, am


def _normalize_for_match(text):
    if not text:
        return ""
    normalized = unicodedata.normalize("NFD", text)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn").lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return normalized.strip()


def _similarity(query, candidate):
    """Score grossier 0-100 entre deux titres (exact > inclusion > Jaccard)."""
    a = _normalize_for_match(query)
    b = _normalize_for_match(candidate)
    if not a or not b:
        return 0
    if a == b:
        return 100
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0
    jaccard = len(ta & tb) / len(ta | tb)
    if ta <= tb or tb <= ta:  # l'un est entièrement contenu dans l'autre
        return 80 + int(20 * jaccard)
    return int(100 * jaccard)


def _parse_search_results(text):
    """fetch.php → [(slug, [titre, alias...]), ...]."""
    results = []
    for match in re.finditer(
        r'<a\s+href="[^"]*?/catalogue/([a-z0-9\-]+)/?"[^>]*?>(.*?)</a>', text, re.S
    ):
        slug = match.group(1)
        inner = match.group(2)
        titles = []
        title_match = re.search(r"<h3[^>]*>(.*?)</h3>", inner, re.S)
        if title_match:
            titles.append(html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip())
        sub_match = re.search(r"<p[^>]*>(.*?)</p>", inner, re.S)
        if sub_match:
            sub = html.unescape(re.sub(r"<[^>]+>", "", sub_match.group(1)))
            titles.extend(part.strip() for part in sub.split(",") if part.strip())
        if not titles:
            titles = [slug.replace("-", " ")]
        results.append((slug, titles))
    return results


def _search_slug(shared_state, am, query, headers):
    """Fallback : recherche anime-sama (fetch.php) → slug le mieux scoré."""
    try:
        response = _am_request(
            "POST",
            f"https://{am}/template-php/defaut/fetch.php",
            data={"query": query}, headers=headers, timeout=10,
        )
    except Exception as exc:
        debug(f"{hostname.upper()} search fallback failed for {query!r}: {exc}")
        return None
    if response.status_code != 200:
        return None

    candidates = _parse_search_results(response.text)
    if not candidates:
        return None

    scored = []
    for slug, titles in candidates:
        score = max((_similarity(query, title) for title in titles), default=0)
        # tri : meilleur score, puis slug le plus court (forme canonique)
        scored.append((score, -len(slug), slug))
    scored.sort(reverse=True)
    debug(f"{hostname.upper()} search for {query!r} → {scored[0][2]} (score={scored[0][0]})")
    return scored[0][2]


def _resolve_slug(shared_state, am, names, headers):
    """Trouve le slug + les saisons : slugification directe, puis recherche."""
    tried = set()

    # 1) slug = titre slugifié (l'hypothèse de base)
    for name in names:
        slug = _slugify(name)
        if not slug or slug in tried:
            continue
        tried.add(slug)
        declarations, am = _fetch_season_declarations(shared_state, am, slug, headers)
        if declarations is not None:
            debug(f"{hostname.upper()} resolved slug '{slug}' directly from '{name}'")
            return slug, declarations, am

    # 2) fallback : recherche interne anime-sama
    for name in names:
        slug = _search_slug(shared_state, am, name, headers)
        if not slug or slug in tried:
            continue
        tried.add(slug)
        declarations, am = _fetch_season_declarations(shared_state, am, slug, headers)
        if declarations is not None:
            debug(f"{hostname.upper()} resolved slug '{slug}' via search for '{name}'")
            return slug, declarations, am

    return None, None, am


def _season_path_for_language(declarations, is_movie, season_num, lang):
    """Chemin anime-sama pour une langue donnée, ou None si absent."""
    matching = [path for _label, path in declarations
                if path.lower().endswith(f"/{lang}")]
    if not matching:
        return None

    if is_movie:
        for path in matching:
            if path.lower().startswith("film"):
                return path
        return None

    # Uniquement les dossiers « saisonN » réellement numérotés. Le
    # ``startswith("saison")`` naïf attrapait aussi les hors-séries type
    # « saison1hs » (Fairy Tail : 100 Years Quest) : leur présence faisait
    # croire à plusieurs saisons et cassait le repli absolu ci-dessous. On
    # s'aligne sur le motif strict de ``_numbered_season_paths``.
    season_re = re.compile(rf"saison\d+/{re.escape(lang)}", re.I)
    saisons = [p for p in matching if season_re.fullmatch(p)]
    if not saisons:
        return None

    if season_num is not None:
        want = f"saison{season_num}/{lang}"
        for path in saisons:
            if path.lower() == want:
                return path
        # Saison demandée absente : repli "absolu" seulement s'il n'y a qu'un
        # seul dossier saison (cas Fairy Tail : tout en saison1, la conversion
        # S/E → numéro absolu prend le relais). Sinon on laisse tomber cette
        # langue (une autre langue a peut-être la bonne saison).
        return saisons[0] if len(saisons) == 1 else None

    return saisons[0]


def _select_season_path(declarations, is_movie, season_num):
    """Mappe la demande vers (chemin, langue) selon l'ordre de préférence."""
    for lang in LANGUAGES:
        path = _season_path_for_language(declarations, is_movie, season_num, lang)
        if path:
            return path, lang
    return None, None


def _fetch_episodes(shared_state, am, slug, season_path, headers):
    url = f"https://{am}/catalogue/{slug}/{season_path}/episodes.js"
    try:
        response = _am_request("GET", url, headers=headers, timeout=10)
    except Exception as exc:
        debug(f"{hostname.upper()} failed to load {url}: {exc}")
        return {}, {}, am
    am = _update_hostname(shared_state, am, response.url)
    if response.status_code != 200:
        debug(f"{hostname.upper()} episodes.js {url} returned HTTP {response.status_code}")
        return {}, {}, am
    eps_map = _parse_episodes_js(response.text)
    if not eps_map:
        return {}, {}, am

    page_url = f"https://{am}/catalogue/{slug}/{season_path}/"
    try:
        page_response = _am_request("GET", page_url, headers=headers, timeout=10)
        am = _update_hostname(shared_state, am, page_response.url)
    except Exception as exc:
        debug(f"{hostname.upper()} failed to load episode labels {page_url}: {exc}")
        return eps_map, {}, am
    if page_response.status_code != 200:
        debug(f"{hostname.upper()} episode labels {page_url} returned HTTP {page_response.status_code}")
        return eps_map, {}, am

    longest = max(len(urls) for urls in eps_map.values())
    episode_indices = _parse_episode_index_map(page_response.text, longest)
    return eps_map, episode_indices, am


def _rss_date():
    """Date de publication *stable* pour une release (tronquée au jour).

    Sonarr matche les releases usenet blacklistées en partie sur la date de
    publication (court-circuit « pubDate identique → blacklisté »). Avec un
    ``datetime.now()`` à la seconde, chaque recherche renvoyait une date
    différente : une release échouée puis blacklistée réapparaissait avec une
    « nouvelle » date et n'était pas reconnue, donc Sonarr la re-grabbait aussitôt
    lors du retry automatique. En tronquant au jour, la même release garde la même
    date d'une recherche à l'autre (fenêtre du retry = quelques secondes), ce qui
    laisse la blocklist de Sonarr la rejeter. On reste sur la date du jour pour ne
    jamais tomber sous une éventuelle limite de rétention.
    """
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _build_release(shared_state, title, source, size_mb, imdb_id):
    payload = urlsafe_b64encode(
        f"{title}|{source}|None|{size_mb}|{imdb_id}".encode("utf-8")
    ).decode("utf-8")
    link = f"{shared_state.values['internal_address']}/download/?payload={payload}"
    log_event("payload_built", source="am", title=title, mirror="None",
              payload_decoded=f"{title}|{source}|None|{size_mb}|{imdb_id}")
    return {
        "details": {
            "title": title,
            "hostname": hostname,
            "imdb_id": imdb_id,
            "link": link,
            "mirror": "None",
            "size": int(size_mb) * 1024 * 1024,
            "date": _rss_date(),
            "source": source,
        },
        "type": "ytdlp",
    }


def _title_variants(shared_state, imdb_id):
    """Noms candidats pour le slug et les titres de release.

    Les slugs anime-sama sont en romaji (shingeki-no-kyojin) OU en anglais
    (demon-slayer), jamais en français et jamais en script japonais. On essaie
    donc les titres "originaux" (romaji, anglais) AVANT le français. Le titre
    ``original_name`` de TMDB (japonais) est volontairement ignoré : il est
    inutilisable pour un slug et la recherche anime-sama le rejette.
    """
    localized_fr, _japanese = get_localized_title(shared_state, imdb_id, "fr", True)
    localized_en, _ = get_localized_title(shared_state, imdb_id, "en", True)
    romaji = get_romaji_title(shared_state, imdb_id)

    names = []
    for candidate in (romaji, localized_en, localized_fr):
        if candidate and candidate not in names:
            names.append(candidate)
    return names


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tmdb_offset(shared_state, imdb_id, season_num):
    """Secours TMDB : nb d'épisodes avant la saison N (None si indisponible)."""
    counts = get_season_episode_counts(shared_state, imdb_id)
    if not counts:
        return None, None
    offset = sum(cnt for s, cnt in counts.items() if 1 <= s < season_num)
    return offset, counts.get(season_num)


def _absolute_episode(shared_state, imdb_id, season_num, ep):
    """Numéro absolu d'un (saison, épisode) : TheTVDB d'abord, TMDB en secours."""
    abs_num = tvdb_absolute_number(shared_state, imdb_id, season_num, ep)
    if abs_num:
        return abs_num
    offset, _len = _tmdb_offset(shared_state, imdb_id, season_num)
    if offset is not None:
        debug(f"{hostname.upper()} TVDB miss → TMDB fallback (offset={offset}) "
              f"for S{season_num}E{ep}")
        return offset + ep
    return None


def _absolute_season_plan(shared_state, imdb_id, season_num):
    """[(épisode, absolu), ...] pour une saison entière : TheTVDB d'abord."""
    pairs = tvdb_season_absolute_numbers(shared_state, imdb_id, season_num)
    if pairs:
        return pairs
    offset, season_len = _tmdb_offset(shared_state, imdb_id, season_num)
    if offset is not None and season_len:
        return [(e, offset + e) for e in range(1, season_len + 1)]
    return []


def _numbered_season_paths(declarations, language):
    """Retourne les dossiers saison d'une langue triés numériquement."""
    paths = []
    pattern = re.compile(rf"^saison(\d+)/{re.escape(language)}$", re.I)
    for _label, path in declarations or []:
        match = pattern.fullmatch(path)
        if match:
            paths.append((int(match.group(1)), path))
    return [path for _number, path in sorted(paths)]


def _find_overflow_episode_source(shared_state, imdb_id, season_num, episode_num,
                                  declarations, language, selected_path,
                                  selected_eps, selected_indices,
                                  am, slug, headers):
    """Suit les dossiers Anime-Sama quand une saison Sonarr les regroupe.

    Exemple : TheTVDB/IMDb expose Asterisk War en S01E01-24 tandis
    qu'Anime-Sama utilise saison1/E01-12 puis saison2/E01-12.
    """
    metadata_plan = _absolute_season_plan(shared_state, imdb_id, season_num)
    if episode_num not in {episode for episode, _absolute in metadata_plan}:
        return None

    paths = _numbered_season_paths(declarations, language)
    try:
        start = paths.index(selected_path)
    except ValueError:
        return None

    remaining = episode_num
    current_am = am
    for path in paths[start:]:
        if path == selected_path:
            eps_map, episode_indices = selected_eps, selected_indices
        else:
            eps_map, episode_indices, current_am = _fetch_episodes(
                shared_state, current_am, slug, path, headers
            )
        count = len(episode_indices)
        if not eps_map or not count:
            return None  # impossible de calculer un décalage fiable
        if remaining in episode_indices:
            return path, eps_map, episode_indices, remaining, current_am
        remaining -= count
        if remaining <= 0:
            return None
    return None


def am_search(shared_state, start_time, request_from, search_string,
              mirror=None, season=None, episode=None):
    releases = []
    request_lower = (request_from or "").lower()
    is_movie = "radarr" in request_lower
    is_series = "sonarr" in request_lower
    if not is_movie and not is_series:
        debug(f"Skipping {hostname.upper()} search for unsupported requester '{request_from}'.")
        return releases

    imdb_id = shared_state.is_imdb_id(search_string)
    if not imdb_id:
        debug(f"{hostname.upper()} only supports IMDb-ID searches.")
        return releases

    am = shared_state.values["config"]("Hostnames").get(hostname)
    if not am:
        info(f"{hostname.upper()} host missing in configuration. Search aborted.")
        return releases

    log_event("search_request", source="am", level="INFO",
              query=imdb_id, requester=request_from, season=season, episode=episode)

    headers = {"User-Agent": _user_agent(shared_state)}

    names = _title_variants(shared_state, imdb_id)
    if not names:
        info(f"{hostname.upper()} could not resolve a title for {imdb_id}.")
        return releases

    # Millésime injecté dans chaque titre de release : lève l'ambiguïté entre
    # œuvres homonymes et court-circuite un scene mapping Sonarr détourné vers
    # une autre série (cf. get_year / _release_title).
    year = get_year(shared_state, imdb_id)

    slug, declarations, am = _resolve_slug(shared_state, am, names, headers)
    if not slug:
        debug(f"{hostname.upper()} no anime-sama entry found for {names!r}.")
        return releases

    season_num = _coerce_int(season)
    episode_num = _coerce_int(episode)

    season_path, language = _select_season_path(declarations, is_movie, season_num)
    if not season_path:
        debug(f"{hostname.upper()} no {'/'.join(LANGUAGES)} season path for slug "
              f"'{slug}' (movie={is_movie}, season={season})")
        return releases
    language_tag = _LANGUAGE_TAGS.get(language, language.upper())
    release_suffix = f"{language_tag}.{RELEASE_QUALITY}"
    debug(f"{hostname.upper()} using {season_path} (lang={language}) for {slug}")

    eps_map, episode_indices, am = _fetch_episodes(
        shared_state, am, slug, season_path, headers
    )
    if not eps_map:
        debug(f"{hostname.upper()} episodes.js empty for {slug}/{season_path}")
        return releases

    longest = max(len(urls) for urls in eps_map.values())
    base_source = f"https://{am}/catalogue/{slug}/{season_path}/"
    seen_titles = set()  # évite les doublons quand deux variantes donnent le même titre

    if is_movie:
        # Radarr : un seul fichier, mais on propose chaque lecteur disponible.
        candidates = _candidates_for_index(eps_map, 0)
        if not candidates:
            debug(f"{hostname.upper()} no playable film embed for {slug}")
            return releases
        for embed_url in candidates:
            host_tag = _host_tag(embed_url)
            register_player(shared_state, host_tag, slug, 0, 0)  # 0/0 = film
            if not is_player_enabled(shared_state, host_tag):
                continue
            source = f"{base_source}#episode=1&player={host_tag}"
            for name in names:
                dotted = _dotted_title(name)
                if not dotted:
                    continue
                title = _release_title(dotted, year, None, release_suffix, host_tag)
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                releases.append(_build_release(shared_state, title, source, FILM_SIZE_MB, imdb_id))
    else:
        # Sonarr : un épisode précis, ou toute la saison si aucun épisode demandé.
        season_for_tag = season_num if season_num is not None else 1

        # "Aligné" = anime-sama possède le dossier de la saison demandée
        # (ex. Attack on Titan : saison1..4). Sinon anime-sama range tout en
        # absolu dans un seul dossier (ex. Fairy Tail) : on convertit alors S/E
        # → numéro absolu via TheTVDB (la source qu'utilise Sonarr), TMDB en
        # secours. Le titre renvoyé reste en SxxExx (ce que Sonarr attend).
        # episode_plan = [(épisode_Sonarr, numéro_anime_sama), ...]
        requested_path = f"saison{season_num}/{language}" if season_num is not None else None
        aligned = (requested_path is None) or (season_path.lower() == requested_path)
        overflow_episode = None

        if aligned and season_num is not None and episode_num is not None \
                and episode_num not in episode_indices:
            overflow = _find_overflow_episode_source(
                shared_state, imdb_id, season_num, episode_num,
                declarations, language, season_path,
                eps_map, episode_indices, am, slug, headers,
            )
            if overflow:
                season_path, eps_map, episode_indices, overflow_episode, am = overflow
                longest = max(len(urls) for urls in eps_map.values())
                base_source = f"https://{am}/catalogue/{slug}/{season_path}/"
                debug(f"{hostname.upper()} IMDb overflow mapping {slug} "
                      f"S{season_num:02d}E{episode_num:02d} -> "
                      f"{season_path} EPISODE {overflow_episode}")

        if aligned:
            if episode_num is not None:
                episode_plan = [(episode_num, overflow_episode or episode_num)]
            else:
                episode_plan = [(e, e) for e in sorted(episode_indices)]
        else:
            if episode_num is not None:
                anime_ep = _absolute_episode(shared_state, imdb_id, season_num, episode_num)
                episode_plan = [(episode_num, anime_ep)] if anime_ep else []
            else:
                episode_plan = _absolute_season_plan(shared_state, imdb_id, season_num)
            if not episode_plan:
                debug(f"{hostname.upper()} cannot map S{season_num} to absolute for {slug}")
                return releases
            debug(f"{hostname.upper()} absolute mapping {slug} S{season_num}: {episode_plan[:5]}...")

        for ep, anime_ep in episode_plan:
            source_index = episode_indices.get(anime_ep)
            if source_index is None:
                debug(f"{hostname.upper()} ignored non-exact or missing EPISODE {anime_ep} "
                      f"for {slug}/{season_path}")
                continue
            candidates = _candidates_for_index(eps_map, source_index)
            if not candidates:
                continue
            tag = f"S{season_for_tag:02d}E{ep:02d}"
            # Une release par lecteur disponible (Sonarr peut les essayer chacun).
            for embed_url in candidates:
                host_tag = _host_tag(embed_url)
                register_player(shared_state, host_tag, slug, season_for_tag, ep)
                if not is_player_enabled(shared_state, host_tag):
                    continue
                source = f"{base_source}#episode={source_index + 1}&player={host_tag}"
                for name in names:
                    dotted = _dotted_title(name)
                    if not dotted:
                        continue
                    title = _release_title(dotted, year, tag, release_suffix, host_tag)
                    if title in seen_titles:
                        continue
                    seen_titles.add(title)
                    releases.append(_build_release(shared_state, title, source, EPISODE_SIZE_MB, imdb_id))

    log_event("search_complete", source="am", level="INFO",
              query=imdb_id, results_count=len(releases),
              time_seconds=round(time.time() - start_time, 2))
    debug(f"{hostname.upper()} generated {len(releases)} releases for {slug}/{season_path}")
    return releases


def am_feed(shared_state, start_time, request_from, mirror=None):
    # anime-sama n'expose pas de flux exploitable façon RSS ; rien à renvoyer.
    return []
