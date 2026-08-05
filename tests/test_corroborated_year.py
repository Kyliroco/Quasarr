# -*- coding: utf-8 -*-
"""L'année de référence n'est proposée que si la fiche du site le confirme.

Zone-Téléchargement n'a pas toujours de champ « Année de production » : l'année
vient alors du nom de fichier saisi par l'uploadeur, et elle se trompe — 2013
pour « One Piece Film Z » sorti en 2012, 2007 pour « Comme Cendrillon 2 » sorti
en 2008. Radarr rapproche sur le couple (titre, année) : un an d'écart et le
film reste introuvable.

Réécrire l'année serait dangereux : deux films peuvent porter le même nom et
seule l'année les distingue, donc un import silencieux du mauvais film serait
pire que de ne rien trouver. La release est donc émise dans l'année de référence
**en plus** des années du site, et seulement quand réalisateur et durée de la
fiche concordent — mesuré exact sur les trois cas ci-dessus.
"""

import pytest

from quasarr.search.sources.zt import (
    _corroborated_year,
    _extract_identity,
    _same_director,
)


class TestIdentityExtraction:
    def test_reads_director_and_duration(self):
        texte = ("Origine : Japon Réalisation : Tatsuya Nagamine "
                 "Acteur(s) : Mayumi Tanaka , Kazuya Nakai Durée : 01h47 Genre : Animation")
        identite = _extract_identity(texte)
        assert identite["director"] == "Tatsuya Nagamine"
        assert identite["runtime"] == 107

    def test_reads_duration_in_minutes(self):
        assert _extract_identity("Durée : 92 min Genre : Comédie")["runtime"] == 92

    def test_missing_fields_are_empty(self):
        identite = _extract_identity("Genre : Animation")
        assert identite == {"director": "", "runtime": None}


class TestDirectorComparison:
    @pytest.mark.parametrize("annonce, reference", [
        ("Dustin Mckenzie", "Dustin McKenzie"),      # casse différente, cas réel
        ("Tatsuya Nagamine", "Tatsuya Nagamine"),
        ("Damon Santostefano", "Damon Santostefano"),
    ])
    def test_same_person(self, annonce, reference):
        assert _same_director(annonce, reference)

    @pytest.mark.parametrize("annonce, reference", [
        ("Owen Hurley", "Ezekiel Norton"),
        ("", "Owen Hurley"),
        ("Owen Hurley", ""),
    ])
    def test_different_or_missing(self, annonce, reference):
        assert not _same_director(annonce, reference)


class TestCorroboration:
    def _shared_state(self, monkeypatch, reference):
        import quasarr.search.sources.zt as zt
        monkeypatch.setattr(zt, "get_reference_identity", lambda ss, i: reference)
        return None

    REFERENCE = {"year": "2012", "director": "Tatsuya Nagamine", "runtime": 108}

    def test_offers_the_reference_year_when_the_film_checks_out(self, monkeypatch):
        self._shared_state(monkeypatch, self.REFERENCE)
        annee = _corroborated_year(
            None, "tt2375379",
            {"director": "Tatsuya Nagamine", "runtime": 107},
            ("2013", ""),
        )
        assert annee == "2012"

    def test_stays_silent_when_the_site_already_says_the_right_year(self, monkeypatch):
        self._shared_state(monkeypatch, self.REFERENCE)
        assert not _corroborated_year(
            None, "tt2375379",
            {"director": "Tatsuya Nagamine", "runtime": 107},
            ("2012", ""),
        )

    def test_stays_silent_when_the_second_announced_year_matches(self, monkeypatch):
        self._shared_state(monkeypatch, self.REFERENCE)
        assert not _corroborated_year(
            None, "tt2375379",
            {"director": "Tatsuya Nagamine", "runtime": 107},
            ("2013", "2012"),
        )

    def test_refuses_when_the_director_differs(self, monkeypatch):
        """Le garde-fou contre l'homonyme : même nom, autre film."""
        self._shared_state(monkeypatch, self.REFERENCE)
        assert not _corroborated_year(
            None, "tt2375379",
            {"director": "Quelqu'un d'autre", "runtime": 107},
            ("2013", ""),
        )

    def test_refuses_when_the_running_time_is_far_off(self, monkeypatch):
        self._shared_state(monkeypatch, self.REFERENCE)
        assert not _corroborated_year(
            None, "tt2375379",
            {"director": "Tatsuya Nagamine", "runtime": 45},
            ("2013", ""),
        )

    @pytest.mark.parametrize("identite", [
        {"director": "Tatsuya Nagamine", "runtime": None},
        {"director": "", "runtime": 107},
        {},
    ])
    def test_refuses_without_enough_evidence(self, monkeypatch, identite):
        self._shared_state(monkeypatch, self.REFERENCE)
        assert not _corroborated_year(None, "tt2375379", identite, ("2013", ""))

    def test_tolerates_the_drift_of_the_sites_running_time(self, monkeypatch):
        """Mesuré : le site annonce 82 min là où la référence en donne 77."""
        self._shared_state(monkeypatch,
                           {"year": "2001", "director": "Owen Hurley", "runtime": 77})
        assert _corroborated_year(
            None, "tt0288441",
            {"director": "Owen Hurley", "runtime": 82},
            ("2002", ""),
        ) == "2001"
