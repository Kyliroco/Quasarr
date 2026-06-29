# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

"""Résolution des liens anime-sama au moment du grab (pour yt-dlp).

L'URL portée par la release est la page de saison anime-sama avec un fragment
``#episode=N`` (ex. ``https://anime-sama.to/catalogue/fairy-tail/saison1/vostfr/#episode=5``).
On re-télécharge ``episodes.js`` ici car les liens d'embed changent souvent :
on renvoie la liste ordonnée des embeds jouables (lecteur 1, lecteur 2, …),
hôtes bloqués retirés. Le worker yt-dlp essaiera chaque candidat dans l'ordre.
"""

import re
from urllib.parse import urlparse

from quasarr.providers.log import debug, log_event
from quasarr.providers.players import is_player_enabled
from quasarr.search.sources.am import (
    AM_BLOCKED_HOSTS,
    _am_request,
    _candidates_for_index,
    _host_tag,
    _parse_episodes_js,
    _update_hostname,
    _user_agent,
)

hostname = "am"


def _parse_source_url(url):
    """Extrait (slug, season_path, episode, player) depuis l'URL de release.

    ``player`` est l'index du lecteur choisi (chaque lecteur = une release dans
    Sonarr) ; None si non précisé (rétrocompat → tous les lecteurs).
    """
    parsed = urlparse(url)
    episode = 1
    player = None
    if parsed.fragment:
        for fragment in parsed.fragment.split("&"):
            key, _, value = fragment.partition("=")
            if key == "episode" and value.isdigit():
                episode = int(value)
            elif key == "player" and value:
                player = value  # nom du lecteur (ex. "Vidmoly") ; index numérique toléré

    parts = [p for p in parsed.path.split("/") if p]
    # .../catalogue/<slug>/<saison>/<langue>
    if "catalogue" not in parts:
        return None, None, episode, player
    idx = parts.index("catalogue")
    rest = parts[idx + 1:]
    if len(rest) < 3:
        return None, None, episode, player
    slug = rest[0]
    season_path = "/".join(rest[1:3])  # "<saison>/<langue>"
    return slug, season_path, episode, player


def _select_candidate(candidates, player):
    """Sélectionne l'embed correspondant au lecteur demandé.

    `player` est un nom d'hébergeur (ex. "Vidmoly", insensible à la casse) ;
    un index numérique est toléré pour les anciens liens (rétrocompat).
    """
    if str(player).isdigit():
        idx = int(player)
        return candidates[idx] if 0 <= idx < len(candidates) else None
    target = str(player).strip().lower()
    for candidate in candidates:
        if _host_tag(candidate).lower() == target:
            return candidate
    return None


def get_am_download_links(shared_state, url, mirror, title):
    """Renvoie la liste ordonnée des embeds jouables pour l'épisode demandé."""
    am = shared_state.values["config"]("Hostnames").get(hostname)
    parsed = urlparse(url)
    if parsed.netloc:
        am = am or parsed.netloc

    slug, season_path, episode, player = _parse_source_url(url)
    log_event("download_attempt", source="am-dl",
              title=title, episode=episode, player=player, slug=slug, url=url)

    if not slug or not season_path:
        log_event("download_error", source="am-dl", level="ERROR",
                  title=title, reason="could not parse anime-sama url", url=url)
        return []

    episodes_url = f"https://{am}/catalogue/{slug}/{season_path}/episodes.js"
    headers = {"User-Agent": _user_agent(shared_state)}
    try:
        response = _am_request("GET", episodes_url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as exc:
        log_event("download_error", source="am-dl", level="ERROR",
                  title=title, reason="episodes.js fetch failed",
                  error=str(exc), url=episodes_url)
        return []

    _update_hostname(shared_state, am, response.url)

    eps_map = _parse_episodes_js(response.text)
    candidates = _candidates_for_index(eps_map, episode - 1)
    if not candidates:
        log_event("download_skipped", source="am-dl", level="WARNING",
                  title=title, reason="no playable embed for episode",
                  episode=episode, url=episodes_url)
        return []

    # Une release = un lecteur précis (par NOM d'hébergeur) : on ne renvoie que
    # celui demandé. S'il échoue, Sonarr essaiera la release du lecteur suivant.
    if player is not None:
        if not str(player).isdigit() and not is_player_enabled(shared_state, str(player)):
            log_event("download_skipped", source="am-dl", level="WARNING",
                      title=title, reason="player disabled by user", player=player)
            return []
        chosen = _select_candidate(candidates, player)
        if chosen:
            fallbacks = [
                candidate for candidate in candidates
                if candidate != chosen and is_player_enabled(shared_state, _host_tag(candidate))
            ]
            log_event("download_resolved", source="am-dl", level="INFO",
                      title=title, episode=episode, player=player,
                      host=urlparse(chosen).netloc, fallbacks=len(fallbacks))
            # Le lecteur choisi reste prioritaire. Si yt-dlp le refuse, le
            # worker essaie les autres lecteurs actifs sans attendre un nouveau
            # cycle de recherche Sonarr.
            return [chosen, *fallbacks]
        log_event("download_skipped", source="am-dl", level="WARNING",
                  title=title, reason="requested player not available",
                  player=player, available=len(candidates), url=episodes_url)
        return []

    # Rétrocompat : pas de lecteur précisé → tous les candidats (fallback auto).
    log_event("download_resolved", source="am-dl", level="INFO",
              title=title, episode=episode, candidates=len(candidates),
              first_host=urlparse(candidates[0]).netloc)
    return candidates
