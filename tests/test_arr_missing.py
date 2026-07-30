# -*- coding: utf-8 -*-
"""Tests de la page « manquants introuvables ».

Le tri est la seule logique qui compte vraiment ici : ne remonter que les titres
monitorés, sans fichier, **hors blocklist** — un titre blocklisté a été trouvé
puis écarté, il n'a rien à faire dans une liste de « jamais trouvés ».
"""

import pytest

from quasarr.providers import arr_client


# --- faux Radarr/Sonarr -----------------------------------------------------

RADARR_MOVIES = [
    # attendu : jamais trouvé -> doit apparaitre
    {"id": 1, "title": "Barbie in Rock 'N Royals", "originalTitle": "Barbie in Rock 'N Royals",
     "year": 2015, "imdbId": "tt4955162", "monitored": True, "hasFile": False, "isAvailable": True},
    # deja sur le disque -> exclu
    {"id": 2, "title": "Inception", "originalTitle": "Inception", "year": 2010,
     "imdbId": "tt1375666", "monitored": True, "hasFile": True, "isAvailable": True},
    # non monitore -> exclu
    {"id": 3, "title": "Tarzan", "originalTitle": "Tarzan", "year": 1999,
     "imdbId": "tt0120855", "monitored": False, "hasFile": False, "isAvailable": True},
    # blocklisté -> trouvé puis écarté, donc exclu
    {"id": 4, "title": "Dune", "originalTitle": "Dune", "year": 2021,
     "imdbId": "tt1160419", "monitored": True, "hasFile": False, "isAvailable": True},
    # pas encore sorti -> introuvable par nature, exclu
    {"id": 5, "title": "Avengers: Doomsday", "originalTitle": "Avengers: Doomsday", "year": 2026,
     "imdbId": "tt21357150", "monitored": True, "hasFile": False, "isAvailable": False},
]

RADARR_BLOCKLIST = {"records": [{"id": 90, "movieId": 4, "sourceTitle": "Dune.2021.BAD"}]}

SONARR_MISSING = {"records": [
    {"id": 11, "seriesId": 100, "seasonNumber": 2, "episodeNumber": 1,
     "series": {"id": 100, "title": "Agents of S.H.I.E.L.D.", "year": 2013, "imdbId": "tt2364582"}},
    {"id": 12, "seriesId": 100, "seasonNumber": 2, "episodeNumber": 2,
     "series": {"id": 100, "title": "Agents of S.H.I.E.L.D.", "year": 2013, "imdbId": "tt2364582"}},
    # episode blocklisté -> exclu du comptage
    {"id": 13, "seriesId": 200, "seasonNumber": 1, "episodeNumber": 1,
     "series": {"id": 200, "title": "Breaking Bad", "year": 2008, "imdbId": "tt0903747"}},
]}

SONARR_BLOCKLIST = {"records": [{"id": 91, "seriesId": 200, "episodeIds": [13]}]}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


@pytest.fixture
def fake_arr(monkeypatch):
    """Branche _get sur les jeux de donnees ci-dessus, sans reseau."""

    def fake_get(kind, path, params=None):
        if kind == 'Radarr' and path == 'movie':
            return RADARR_MOVIES
        if kind == 'Radarr' and path == 'blocklist':
            return RADARR_BLOCKLIST
        if kind == 'Sonarr' and path == 'wanted/missing':
            return SONARR_MISSING
        if kind == 'Sonarr' and path == 'blocklist':
            return SONARR_BLOCKLIST
        raise AssertionError(f"appel inattendu: {kind} /{path}")

    monkeypatch.setattr(arr_client, "_get", fake_get)


class TestMissingMovies:
    def test_only_never_found_movies_are_listed(self, fake_arr):
        titles = [m['arr_title'] for m in arr_client.get_missing_movies()]
        assert titles == ["Barbie in Rock 'N Royals"]

    def test_movie_on_disk_is_excluded(self, fake_arr):
        assert "Inception" not in [m['arr_title'] for m in arr_client.get_missing_movies()]

    def test_unmonitored_movie_is_excluded(self, fake_arr):
        assert "Tarzan" not in [m['arr_title'] for m in arr_client.get_missing_movies()]

    def test_blocklisted_movie_is_excluded(self, fake_arr):
        # Dune est manquant ET monitoré, mais il a déjà été trouvé puis blocklisté.
        assert "Dune" not in [m['arr_title'] for m in arr_client.get_missing_movies()]

    def test_unreleased_movie_is_excluded(self, fake_arr):
        assert "Avengers: Doomsday" not in [m['arr_title'] for m in arr_client.get_missing_movies()]

    def test_imdb_id_is_carried_for_tmdb_lookup(self, fake_arr):
        assert arr_client.get_missing_movies()[0]['imdb_id'] == "tt4955162"


class TestMissingSeries:
    def test_series_are_grouped_with_episode_counts(self, fake_arr):
        result = arr_client.get_missing_series()
        assert len(result) == 1
        assert result[0]['arr_title'] == "Agents of S.H.I.E.L.D."
        assert result[0]['count'] == 2

    def test_blocklisted_episode_removes_its_series(self, fake_arr):
        # Le seul épisode manquant de Breaking Bad est blocklisté.
        assert "Breaking Bad" not in [s['arr_title'] for s in arr_client.get_missing_series()]


class TestZtSearchUrl:
    def test_builds_encoded_search_link(self, shared_state):
        url = arr_client.zt_search_url(shared_state, "Barbie : Rock et Royales", "films")
        assert url.startswith("https://www.zone-telechargement.test/?p=films&search=")
        assert "Barbie" in url
        assert " " not in url  # doit etre encode

    def test_empty_title_yields_no_link(self, shared_state):
        assert arr_client.zt_search_url(shared_state, "", "films") == ""


class TestNotConfigured:
    def test_missing_config_raises_explicit_error(self, monkeypatch):
        monkeypatch.setattr(arr_client, "get_arr_config", lambda kind: ("", ""))
        with pytest.raises(arr_client.ArrError) as exc:
            arr_client._get('Radarr', 'movie')
        assert "pas configuré" in str(exc.value)
