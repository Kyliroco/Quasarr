# -*- coding: utf-8 -*-
"""Le budget de recherche doit couvrir *tous* les chemins.

Radarr et Sonarr coupent une requête indexeur à 100 s et désactivent
l'indexeur après quelques échecs. Quasarr doit donc toujours rendre la main
avant, quitte à renvoyer un résultat partiel.

Régression : le secours anime (anime-sama muet -> repli sur zt) était appelé en
synchrone *après* le bloc protégé, donc hors budget. Une recherche zt de 143 s
est ainsi passée au travers et a fait expirer Radarr.
"""

import time

import pytest

import quasarr.search as search


@pytest.fixture
def fast_budget(monkeypatch):
    """Budget d'une seconde pour garder le test rapide."""
    monkeypatch.setattr(search, "SEARCH_BUDGET_SECONDS", 1)


@pytest.fixture
def only_am_and_zt(shared_state):
    """Config ne déclarant que anime-sama et zt."""
    hostnames = shared_state.values["config"]("Hostnames")
    for key in ("al", "by", "dd", "dt", "dw", "fx", "mb", "nx", "sf", "sl", "wd"):
        hostnames.save(key, "")
    hostnames.save("am", "anime-sama.test")
    hostnames.save("zt", "zt.test")
    return shared_state


class TestAnimeFallbackRespectsBudget:
    def test_slow_zt_fallback_does_not_blow_the_budget(
        self, monkeypatch, fast_budget, only_am_and_zt,
    ):
        monkeypatch.setattr(search, "is_anime", lambda ss, imdb_id: True)
        # anime-sama ne renvoie rien -> déclenche le secours zt
        monkeypatch.setattr(search, "am_search", lambda *a, **kw: [])

        def slow_zt(*args, **kwargs):
            time.sleep(10)
            return [{"details": {"hostname": "zt", "title": "trop tard"}}]

        monkeypatch.setattr(search, "zt_search", slow_zt)

        started = time.time()
        results = search.get_search_results(
            only_am_and_zt, "Radarr/6.4.0", imdb_id="tt1010435",
        )
        elapsed = time.time() - started

        # On rend la main sur le budget, pas au bout des 10 s de zt.
        assert elapsed < 5, f"le secours zt a ignoré le budget ({elapsed:.1f}s)"
        assert results == []

    def test_fast_zt_fallback_results_are_kept(
        self, monkeypatch, fast_budget, only_am_and_zt,
    ):
        """Le garde-fou ne doit pas jeter un secours qui répond à temps."""
        monkeypatch.setattr(search, "is_anime", lambda ss, imdb_id: True)
        monkeypatch.setattr(search, "am_search", lambda *a, **kw: [])
        monkeypatch.setattr(
            search, "zt_search",
            lambda *a, **kw: [{"details": {"hostname": "zt", "title": "a temps"}}],
        )

        results = search.get_search_results(
            only_am_and_zt, "Radarr/6.4.0", imdb_id="tt1010435",
        )
        assert [r["details"]["title"] for r in results] == ["a temps"]

    def test_am_results_skip_the_fallback_entirely(
        self, monkeypatch, fast_budget, only_am_and_zt,
    ):
        monkeypatch.setattr(search, "is_anime", lambda ss, imdb_id: True)
        monkeypatch.setattr(
            search, "am_search",
            lambda *a, **kw: [{"details": {"hostname": "am", "title": "depuis am"}}],
        )

        def zt_must_not_run(*args, **kwargs):
            raise AssertionError("zt ne doit pas être appelé si anime-sama a répondu")

        monkeypatch.setattr(search, "zt_search", zt_must_not_run)

        results = search.get_search_results(
            only_am_and_zt, "Radarr/6.4.0", imdb_id="tt1010435",
        )
        assert [r["details"]["title"] for r in results] == ["depuis am"]


class TestParallelRunRespectsBudget:
    def test_slow_source_in_parallel_run_is_capped(
        self, monkeypatch, fast_budget, only_am_and_zt,
    ):
        # non-anime : zt part dans le run parallèle
        monkeypatch.setattr(search, "is_anime", lambda ss, imdb_id: False)

        def slow_zt(*args, **kwargs):
            time.sleep(10)
            return []

        monkeypatch.setattr(search, "zt_search", slow_zt)

        started = time.time()
        search.get_search_results(only_am_and_zt, "Radarr/6.4.0", imdb_id="tt4955162")
        elapsed = time.time() - started

        assert elapsed < 5, f"le run parallèle a ignoré le budget ({elapsed:.1f}s)"
