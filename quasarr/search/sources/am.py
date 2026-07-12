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
import threading
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
    get_total_absolute_numbers as tvdb_total_absolute_numbers,
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

    # Uniquement les dossiers « saisonN » réellement numérotés (parties
    # « saisonN-M » incluses). Le ``startswith("saison")`` naïf attrapait aussi
    # les hors-séries type « saison1hs » (Fairy Tail : 100 Years Quest) : leur
    # présence faisait croire à plusieurs saisons et cassait le repli absolu
    # ci-dessous. On s'aligne sur le motif de ``_numbered_season_paths``.
    season_re = re.compile(rf"saison\d+(?:-\d+)?/{re.escape(lang)}", re.I)
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


def _preferred_series_language(declarations):
    """Langue à utiliser pour une série quand aucun ``saisonN`` exact n'existe.

    Renvoie la première langue (ordre de préférence) qui possède au moins un
    dossier d'épisodes — arcs hors-série inclus, puisqu'ils comptent comme des
    saisons (cf. Demon Slayer). None si la série n'a aucun dossier exploitable.
    """
    for lang in LANGUAGES:
        if _ordered_episode_folders(declarations, lang):
            return lang
    return None


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
    """Retourne les dossiers saison d'une langue triés numériquement.

    Gère les saisons scindées en parties (« Partie 2 ») qu'Anime-Sama nomme
    ``saisonN-M`` : ex. Fire Force expose ``saison3`` puis ``saison3-2`` alors que
    TheTVDB compte une seule saison 3 (25 épisodes). On les ordonne par (N, M)
    pour que le suivi d'overflow enchaîne saison3 -> saison3-2. Les hors-séries
    ``saisonNhs`` (ex. Fairy Tail 100 Years Quest) ne matchent pas et restent
    exclus : ce sont des œuvres distinctes, pas la suite d'une saison.
    """
    paths = []
    pattern = re.compile(rf"^saison(\d+)(?:-(\d+))?/{re.escape(language)}$", re.I)
    for _label, path in declarations or []:
        match = pattern.fullmatch(path)
        if match:
            season = int(match.group(1))
            part = int(match.group(2)) if match.group(2) else 1
            paths.append((season, part, path))
    return [path for _season, _part, path in sorted(paths)]


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


def _subsequent_part_episodes(shared_state, declarations, language, after_path,
                              am, slug, headers, needed):
    """Épisodes des dossiers-parties situés APRÈS ``after_path``.

    Sert à compléter une saison Sonarr éclatée sur plusieurs dossiers anime-sama
    (ex. saison3 puis saison3-2). Retourne au plus ``needed`` entrées
    ``(path, eps_map, indices, ep_local, am)`` dans l'ordre de diffusion.
    """
    if needed <= 0:
        return []
    paths = _numbered_season_paths(declarations, language)
    try:
        start = paths.index(after_path)
    except ValueError:
        return []
    flat = []
    current_am = am
    for path in paths[start + 1:]:
        eps_map, episode_indices, current_am = _fetch_episodes(
            shared_state, current_am, slug, path, headers
        )
        if not eps_map or not episode_indices:
            break
        for local in sorted(episode_indices):
            flat.append((path, eps_map, episode_indices, local, current_am))
            if len(flat) >= needed:
                return flat
    return flat


# Cache mémoire court des dossiers anime-sama : le regroupement absolu relit
# plusieurs fois les mêmes dossiers (contrôle de total + parcours), et chaque
# lecture est jitterée. Clé = (slug, path).
_EPISODES_CACHE = {}
_EPISODES_CACHE_LOCK = threading.Lock()
_EPISODES_CACHE_TTL = 10 * 60


def _fetch_episodes_cached(shared_state, am, slug, path, headers):
    """``_fetch_episodes`` avec cache mémoire court (voir ``_EPISODES_CACHE``)."""
    key = (slug, path)
    now = time.time()
    with _EPISODES_CACHE_LOCK:
        hit = _EPISODES_CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    result = _fetch_episodes(shared_state, am, slug, path, headers)
    if result[0] and result[1]:  # eps_map et indices non vides
        with _EPISODES_CACHE_LOCK:
            _EPISODES_CACHE[key] = (now + _EPISODES_CACHE_TTL, result)
    return result


_EPISODE_ARC_LABEL_RE = re.compile(r"\s*episode\s*-", re.I)


def _is_episode_arc_label(label):
    """Vrai si le libellé est « Épisode - ... » : un arc rangé en hors-série mais
    qui est en réalité une saison à part entière (Demon Slayer : « Épisode - Train
    de l'infini »). Les autres hors-série (« 100 Years Quest ... ») ne matchent
    pas et restent exclus. Insensible à la casse et aux accents.
    """
    normalized = unicodedata.normalize("NFD", label or "")
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    return bool(_EPISODE_ARC_LABEL_RE.match(normalized))


def _ordered_episode_folders(declarations, language):
    """Dossiers-saisons dans l'ordre de déclaration (= ordre de diffusion).

    Inclut les dossiers numérotés ``saisonN`` / ``saisonN-M`` ET les arcs rangés
    en hors-série ``saisonNhs`` DONT le libellé est « Épisode - ... » (ils sont
    des saisons normales, ex. Demon Slayer). Les autres hors-série
    (« 100 Years Quest »), films, OAV, etc. restent exclus.
    """
    numbered = re.compile(rf"saison\d+(?:-\d+)?/{re.escape(language)}", re.I)
    hors_serie = re.compile(rf"saison\d+hs/{re.escape(language)}", re.I)
    ordered, seen = [], set()
    for label, path in declarations or []:
        if path in seen:
            continue
        if numbered.fullmatch(path) or (hors_serie.fullmatch(path) and _is_episode_arc_label(label)):
            seen.add(path)
            ordered.append(path)
    return ordered


def _series_total_episodes(shared_state, imdb_id):
    """Total d'épisodes attendu (TheTVDB d'abord, somme des saisons TMDB sinon).

    Contrôle du regroupement absolu : on ne retient la concaténation des dossiers
    anime-sama que si son total colle avec ce nombre.
    """
    total = tvdb_total_absolute_numbers(shared_state, imdb_id)
    if total:
        return total
    counts = get_season_episode_counts(shared_state, imdb_id)
    if counts:
        return sum(counts.values()) or None
    return None


def _build_absolute_concat(shared_state, folder_paths, preloaded, am, slug, headers):
    """Concatène les dossiers donnés -> ``[(path, eps_map, indices, local, am), ...]``.

    La position i (0-based) correspond au numéro absolu i+1. Retourne ``[]`` si
    un dossier est vide ou inaccessible (concaténation non fiable).
    """
    concat = []
    current_am = am
    for path in folder_paths:
        if preloaded and path == preloaded[0]:
            eps_map, episode_indices = preloaded[1], preloaded[2]
        else:
            eps_map, episode_indices, current_am = _fetch_episodes_cached(
                shared_state, current_am, slug, path, headers
            )
        if not eps_map or not episode_indices:
            return []
        for local in sorted(episode_indices):
            concat.append((path, eps_map, episode_indices, local, current_am))
    return concat


def _absolute_grouping_plan(shared_state, imdb_id, season_num, episode_num,
                            declarations, language, season_path, eps_map, episode_indices,
                            am, slug, headers):
    """Méthode principale : regrouper les épisodes anime-sama par numéro absolu.

    On concatène les dossiers-saisons dans l'ordre de diffusion — arcs
    « Épisode - ... » rangés en hors-série inclus, car ce sont des saisons
    normales : chez anime-sama l'arc du Train de l'infini (Demon Slayer) est un
    hors-série alors que TheTVDB en fait la saison 2, ce qui décale toutes les
    suivantes. La concaténation n'est retenue que si son total colle avec
    TheTVDB (contrôle). La position i = numéro absolu i+1 ; l'épisode Sonarr
    demandé (ou toute la saison) est converti en absolu via TheTVDB (TMDB en
    secours) puis lu à sa position. Retourne ``None`` si le total ne correspond
    pas ou si la conversion échoue : l'appelant bascule sur la méthode
    saison-relative.
    """
    expected_total = _series_total_episodes(shared_state, imdb_id)
    if not expected_total:
        return None
    folders = _ordered_episode_folders(declarations, language)
    preloaded = (season_path, eps_map, episode_indices)
    concat = _build_absolute_concat(shared_state, folders, preloaded, am, slug, headers)
    if not concat or len(concat) != expected_total:
        debug(f"{hostname.upper()} absolute grouping skipped for {slug}: anime-sama="
              f"{len(concat)} vs metadata={expected_total}")
        return None
    debug(f"{hostname.upper()} absolute grouping for {slug}: {len(concat)} episodes "
          f"across {len(folders)} folder(s)")

    def locate(absolute):
        if absolute and 1 <= absolute <= len(concat):
            return concat[absolute - 1]
        return None

    if episode_num is not None:
        entry = locate(_absolute_episode(shared_state, imdb_id, season_num, episode_num))
        if not entry:
            return None
        path, e_map, e_idx, local, e_am = entry
        return [(episode_num, path, e_map, e_idx, local, e_am)]

    located = []
    for ep, absolute in _absolute_season_plan(shared_state, imdb_id, season_num):
        entry = locate(absolute)
        if entry:
            path, e_map, e_idx, local, e_am = entry
            located.append((ep, path, e_map, e_idx, local, e_am))
    return located or None


def _plan_located_episodes(shared_state, imdb_id, season_num, episode_num, aligned,
                           declarations, language, season_path, eps_map, episode_indices,
                           am, slug, headers):
    """Localise chaque épisode Sonarr dans son dossier anime-sama.

    Retourne ``[(ep_sonarr, path, eps_map, indices, ep_local, am), ...]``.

    Méthode principale : regroupement par numéro absolu (concaténation de tous
    les dossiers, validée par le total TheTVDB) — voir ``_absolute_grouping_plan``.
    Si aucun regroupement ne colle (structure inattendue), on bascule sur le
    repli saison-relatif ``_season_relative_plan``.
    """
    absolute = _absolute_grouping_plan(
        shared_state, imdb_id, season_num, episode_num,
        declarations, language, season_path, eps_map, episode_indices,
        am, slug, headers,
    )
    if absolute:
        return absolute
    return _season_relative_plan(
        shared_state, imdb_id, season_num, episode_num, aligned,
        declarations, language, season_path, eps_map, episode_indices,
        am, slug, headers,
    )


def _season_relative_plan(shared_state, imdb_id, season_num, episode_num, aligned,
                          declarations, language, season_path, eps_map, episode_indices,
                          am, slug, headers):
    """Repli saison-relatif quand le regroupement absolu ne s'applique pas.

    - Aligné + épisode présent dans le dossier : mapping direct.
    - Aligné + épisode au-delà du dossier : suivi des parties suivantes
      (saison3 -> saison3-2), aussi bien pour un épisode que pour la saison
      entière.
    - Non aligné (dossier unique en absolu, ex. Fairy Tail) : conversion
      S/E -> numéro absolu via TheTVDB (TMDB en secours), lue dans ce dossier.
    """
    if aligned:
        if episode_num is not None:
            if episode_num in episode_indices:
                return [(episode_num, season_path, eps_map, episode_indices, episode_num, am)]
            overflow = _find_overflow_episode_source(
                shared_state, imdb_id, season_num, episode_num,
                declarations, language, season_path,
                eps_map, episode_indices, am, slug, headers,
            )
            if not overflow:
                return []
            path, o_eps, o_idx, local, o_am = overflow
            return [(episode_num, path, o_eps, o_idx, local, o_am)]

        # Saison entière : le dossier sélectionné démarre la saison ; on la
        # complète avec les parties suivantes si TheTVDB annonce plus d'épisodes
        # que ce dossier n'en contient (cas saison éclatée).
        selected = sorted(episode_indices)
        located = [(e, season_path, eps_map, episode_indices, e, am) for e in selected]
        plan = _absolute_season_plan(shared_state, imdb_id, season_num)
        if plan and len(plan) > len(selected):
            extra = _subsequent_part_episodes(
                shared_state, declarations, language, season_path,
                am, slug, headers, len(plan) - len(selected),
            )
            for offset, (path, e_map, e_idx, local, e_am) in enumerate(extra):
                located.append((len(selected) + 1 + offset, path, e_map, e_idx, local, e_am))
        return located

    # Non aligné : dossier unique en numérotation absolue.
    if episode_num is not None:
        anime_ep = _absolute_episode(shared_state, imdb_id, season_num, episode_num)
        if not anime_ep:
            return []
        return [(episode_num, season_path, eps_map, episode_indices, anime_ep, am)]
    return [(ep, season_path, eps_map, episode_indices, absolute, am)
            for ep, absolute in _absolute_season_plan(shared_state, imdb_id, season_num)]


def _kai_folders(declarations, language):
    """{numéro_saga: chemin} des dossiers « Kai » d'une langue.

    Le Kai est un remontage condensé (génériques retirés, arcs en longs
    « films »). « kai » = saga 1, « kaiN » = saga N. Vide si la série n'a pas de
    version Kai.
    """
    pattern = re.compile(rf"kai(\d*)/{re.escape(language)}", re.I)
    result = {}
    for _label, path in declarations or []:
        match = pattern.fullmatch(path)
        if match:
            number = int(match.group(1)) if match.group(1) else 1
            result.setdefault(number, path)
    return result


def _kai_located_episodes(shared_state, imdb_id, season_num, episode_num,
                          kai_map, am, slug, headers):
    """Localise les films Kai pour une requête Sonarr → (handled, located).

    Deux dispositions :
    - Par saga (One Piece : kai, kai2, … kaiN) : le dossier kaiN sert la saison N,
      ses films deviennent les épisodes 1, 2, 3… Au-delà du nombre de films →
      rien. Une saison SANS dossier kai n'est pas gérée ici (``handled=False``) :
      l'appelant repasse alors sur les épisodes normaux.
    - Kai unique (Fairy Tail : un seul dossier « kai ») : les films sont posés en
      séquence à travers les saisons via les comptes d'épisodes TheTVDB/TMDB.
      Au-delà du nombre de films → rien, et jamais d'épisode normal
      (``handled=True`` toujours).

    ``located`` = ``[(ep_sonarr, path, eps_map, indices, ep_local, am), ...]``.
    """
    single_kai = set(kai_map) == {1}

    if single_kai:
        kai_path = kai_map[1]
        eps_map, indices, am = _fetch_episodes_cached(shared_state, am, slug, kai_path, headers)
        if not indices:
            return True, []
        films = sorted(indices)  # numéros affichés des films dans l'ordre
        total = len(films)
        offset, season_len = _tmdb_offset(shared_state, imdb_id, season_num)
        if offset is None:
            return True, []
        located = []
        if episode_num is not None:
            position = offset + episode_num
            if 1 <= position <= total:
                located.append((episode_num, kai_path, eps_map, indices, films[position - 1], am))
        else:
            for ep in range(1, (season_len or 0) + 1):
                position = offset + ep
                if position > total:
                    break
                located.append((ep, kai_path, eps_map, indices, films[position - 1], am))
        return True, located

    # Par saga : la saison demandée doit avoir son propre dossier kai.
    kai_path = kai_map.get(season_num)
    if not kai_path:
        return False, []  # saison sans kai → l'appelant sert les épisodes normaux
    eps_map, indices, am = _fetch_episodes_cached(shared_state, am, slug, kai_path, headers)
    if not indices:
        return True, []
    if episode_num is not None:
        if episode_num in indices:
            return True, [(episode_num, kai_path, eps_map, indices, episode_num, am)]
        return True, []
    return True, [(ep, kai_path, eps_map, indices, ep, am) for ep in sorted(indices)]


def _emit_located_releases(shared_state, located, slug, season_for_tag, names, year,
                           release_suffix, imdb_id):
    """Construit les releases Sonarr depuis une liste localisée.

    Une release par lecteur disponible, titre SxxExx normal (un film Kai passe
    donc pour un épisode normal). ``located`` =
    ``[(ep_sonarr, path, eps_map, indices, ep_local, am), ...]``.
    """
    releases = []
    seen_titles = set()
    for ep, part_path, part_eps, part_idx, ep_local, part_am in located:
        source_index = part_idx.get(ep_local)
        if source_index is None:
            debug(f"{hostname.upper()} ignored non-exact or missing EPISODE {ep_local} "
                  f"for {slug}/{part_path}")
            continue
        candidates = _candidates_for_index(part_eps, source_index)
        if not candidates:
            continue
        base_source = f"https://{part_am}/catalogue/{slug}/{part_path}/"
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
    return releases


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
    if not season_path and not is_movie and season_num is not None:
        # Aucun dossier « saisonN » exact : numérotation anime-sama décalée par un
        # arc hors-série compté comme saison (Demon Slayer : pas de saison5). On
        # ne renonce pas — le regroupement absolu (qui parcourt tous les dossiers)
        # retrouvera le bon. On a juste besoin de la langue.
        language = _preferred_series_language(declarations)
        if language:
            debug(f"{hostname.upper()} no exact saison{season_num} folder for '{slug}'; "
                  f"resolving via absolute grouping (lang={language})")
    if not language:
        debug(f"{hostname.upper()} no {'/'.join(LANGUAGES)} season path for slug "
              f"'{slug}' (movie={is_movie}, season={season})")
        return releases
    language_tag = _LANGUAGE_TAGS.get(language, language.upper())
    release_suffix = f"{language_tag}.{RELEASE_QUALITY}"

    # Dossier sélectionné (s'il existe) : sert de préchargement au regroupement
    # absolu et de base au film / au repli saison-relatif. Sinon on part à vide.
    if season_path:
        debug(f"{hostname.upper()} using {season_path} (lang={language}) for {slug}")
        eps_map, episode_indices, am = _fetch_episodes(
            shared_state, am, slug, season_path, headers
        )
        if not eps_map:
            debug(f"{hostname.upper()} episodes.js empty for {slug}/{season_path}")
            return releases
    else:
        eps_map, episode_indices = {}, {}

    base_source = f"https://{am}/catalogue/{slug}/{season_path}/" if season_path else ""
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

        located = None
        # Mode Kai : si la série a des dossiers « kai », on remplace les épisodes
        # par les remontages condensés en les faisant passer pour des SxxExx
        # normaux (voir _kai_located_episodes). Une saison sans kai (mode par
        # saga, ex. One Piece Elbaf) n'est pas gérée -> on repasse au flux normal.
        kai_map = _kai_folders(declarations, language) if season_num is not None else {}
        if kai_map:
            handled, kai_located = _kai_located_episodes(
                shared_state, imdb_id, season_num, episode_num, kai_map, am, slug, headers,
            )
            if handled:
                located = kai_located
                debug(f"{hostname.upper()} kai mode for {slug} S{season_num}: "
                      f"{len(located)} film(s)")

        if located is None:
            # Flux normal : on traduit (saison/épisode) vers le bon dossier
            # anime-sama (aligné / parties / absolu). _plan_located_episodes
            # renvoie, PAR épisode Sonarr, son dossier/index/hôte propres.
            requested_path = f"saison{season_num}/{language}" if season_num is not None else None
            aligned = (requested_path is None) or (bool(season_path) and season_path.lower() == requested_path)
            located = _plan_located_episodes(
                shared_state, imdb_id, season_num, episode_num, aligned,
                declarations, language, season_path, eps_map, episode_indices,
                am, slug, headers,
            )

        if not located:
            suffix = f"E{episode_num}" if episode_num is not None else ""
            debug(f"{hostname.upper()} could not map S{season_num}{suffix} for {slug}")
            return releases

        releases.extend(_emit_located_releases(
            shared_state, located, slug, season_for_tag, names, year, release_suffix, imdb_id,
        ))

    log_event("search_complete", source="am", level="INFO",
              query=imdb_id, results_count=len(releases),
              time_seconds=round(time.time() - start_time, 2))
    debug(f"{hostname.upper()} generated {len(releases)} releases for {slug}/{season_path}")
    return releases


def am_feed(shared_state, start_time, request_from, mirror=None):
    # anime-sama n'expose pas de flux exploitable façon RSS ; rien à renvoyer.
    return []
