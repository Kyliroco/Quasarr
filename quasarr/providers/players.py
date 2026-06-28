# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

"""Base des lecteurs (hébergeurs d'embed) anime-sama.

À chaque découverte d'un nouveau lecteur lors d'une recherche, on l'enregistre
dans la table SQLite ``players`` (persistée dans /config, donc conservée au
redémarrage app/docker) avec le contexte de première découverte (animé, saison,
épisode). L'utilisateur peut activer/désactiver chaque lecteur depuis l'UI ; les
lecteurs désactivés ne sont plus proposés ni téléchargés.

Un cache mémoire évite de retoucher la DB à chaque recherche.
"""

import json
import threading
import time

from quasarr.providers.log import info, debug

TABLE = "players"

_CACHE = None
_LOCK = threading.RLock()


def _load(shared_state):
    global _CACHE
    if _CACHE is None:
        cache = {}
        rows = shared_state.get_db(TABLE).retrieve_all_titles() or []
        for name, raw in rows:
            try:
                cache[name] = json.loads(raw)
            except Exception:
                continue
        _CACHE = cache
    return _CACHE


def invalidate_cache():
    global _CACHE
    with _LOCK:
        _CACHE = None


def get_players(shared_state):
    """Retourne {nom: {enabled, anime, season, episode, first_seen}} (copie)."""
    with _LOCK:
        return {name: dict(entry) for name, entry in _load(shared_state).items()}


def register_player(shared_state, name, anime, season, episode):
    """Enregistre un lecteur s'il est inconnu (avec le contexte de découverte)."""
    if not name:
        return
    with _LOCK:
        cache = _load(shared_state)
        if name in cache:
            return
        entry = {
            "name": name,
            "enabled": True,
            "anime": anime,
            "season": season,
            "episode": episode,
            "first_seen": int(time.time()),
        }
        cache[name] = entry
        shared_state.get_db(TABLE).update_store(name, json.dumps(entry))
        info(f"New anime-sama player discovered: '{name}' "
             f"(first seen on {anime} S{season}E{episode})")


def is_player_enabled(shared_state, name):
    """True si le lecteur est activé (ou inconnu — activé par défaut)."""
    with _LOCK:
        entry = _load(shared_state).get(name)
    return bool(entry.get("enabled", True)) if entry else True


def set_player_enabled(shared_state, name, enabled):
    with _LOCK:
        cache = _load(shared_state)
        entry = cache.get(name)
        if not entry:
            return False
        entry["enabled"] = bool(enabled)
        shared_state.get_db(TABLE).update_store(name, json.dumps(entry))
        debug(f"Player '{name}' {'enabled' if enabled else 'disabled'}")
        return True
