# -*- coding: utf-8 -*-
"""Correspondance titre de release <-> titre recherché.

Ces tests figent un compromis **volontaire** sur le repli « préfixe » : une
carte réduite au nom de la franchise ("Barbie") répond à la recherche d'un de
ses dérivés ("Barbie : Rock et Royales").

C'est permissif et cela fait remonter des releases d'un autre film de la
franchise, étiquetées avec l'IMDb-ID recherché. Radarr les écarte lui-même sur
l'année, et la tolérance évite de rater un film que le site liste sous un
libellé raccourci. Le coût est en revanche une page de détail chargée par faux
positif : c'est le préchargement parallèle de zt.py qui l'absorbe.

Si un jour on resserre ce repli, ce fichier doit être mis à jour sciemment,
pas contourné.
"""

import pytest

from quasarr.providers.shared_state import (
    has_non_latin_letters,
    sanitize_string,
    search_string_in_sanitized_title as matches,
)


class TestFranchisePrefixIsDeliberatelyAccepted:
    """Compromis assumé : le nom de franchise seul répond à ses dérivés."""

    @pytest.mark.parametrize("search, zt_title", [
        ("Barbie : Rock et Royales", "Barbie"),
        ("Barbie : Grande Ville, Grands Rêves", "Barbie"),
        ("Hercule de zéro à héros", "Hercule"),
        ("Raiponce Moi j'ai un rêve", "Raiponce"),
    ])
    def test_accepted(self, search, zt_title):
        assert matches(search, zt_title) is True


class TestSequelNumberingStaysBlocking:
    """La numérotation de suite reste discriminante : "Barbie 2" != "Barbie"."""

    @pytest.mark.parametrize("search, zt_title", [
        ("Shrek 2", "Shrek"),
        ("Rocky II", "Rocky"),
    ])
    def test_sequel_number_is_not_swallowed(self, search, zt_title):
        assert matches(search, zt_title) is False


class TestRealTitlesStillMatch:
    @pytest.mark.parametrize("search, zt_title", [
        # le vrai libellé ZT du film recherché (ZT omet le deux-points)
        ("Barbie : Rock et Royales", "Barbie Rock et Royales"),
        # ponctuation et accents indifférents
        ("Tempête de boulettes géantes", "Tempete de boulettes geantes"),
        # la recherche est contenue dans un titre de release plus long
        ("Le secret de la Petite Sirène", "Le secret de la Petite Sirene 2008 HDLIGHT"),
    ])
    def test_accepted(self, search, zt_title):
        assert matches(search, zt_title) is True


class TestUnrelatedTitlesAreRejected:
    @pytest.mark.parametrize("search, zt_title", [
        ("Barbie : Rock et Royales", "Rocky IV: Rocky Vs. Drago"),
        ("Barbie : Rock et Royales", "Barbie: Spy Squad"),
        ("Barbie : Rock et Royales", "Barbie dans Casse-noisette"),
    ])
    def test_rejected(self, search, zt_title):
        assert matches(search, zt_title) is False


class TestEmptySearchGuard:
    def test_empty_sanitized_search_matches_nothing(self):
        # un titre entièrement non-ASCII se réduit à vide après nettoyage
        assert matches("進撃の巨人", "N'importe quel titre") is False


class TestNonLatinQueryDetection:
    """Un titre partiellement non latin perd sa spécificité au nettoyage.

    Cas réel : le titre original TMDB de « One Piece, film 5 » est
    "ONE PIECE 呪われた聖剣". sanitize_string() retire les caractères japonais et
    laisse "one piece" — qui matche tout le catalogue de la franchise. Résultat
    observé en production : 296 releases, 143 s de recherche, timeout Radarr et
    indexeur désactivé.
    """

    @pytest.mark.parametrize("titre", [
        "ONE PIECE 呵われた聖剣",   # japonais partiel
        "進撃の巨人",                    # japonais intégral
        "Война и мир",  # cyrillique
    ])
    def test_non_latin_is_detected(self, titre):
        assert has_non_latin_letters(titre) is True

    @pytest.mark.parametrize("titre", [
        "One Piece, film 5 : La Malédiction de l'épée sacrée",  # accents = latin
        "Tempête de boulettes géantes",
        "Barbie in Rock 'N Royals",
        "Naruto Shippuden",
        "",
        None,
    ])
    def test_latin_is_not_flagged(self, titre):
        assert has_non_latin_letters(titre) is False

    def test_degradation_is_what_makes_it_dangerous(self):
        """Illustre pourquoi le garde-fou « chaîne vide » ne suffisait pas."""
        japonais_partiel = "ONE PIECE 呵われた聖剣"
        # le nettoyage ne vide pas la requête : il la rend générique
        assert sanitize_string(japonais_partiel) == "one piece"
        # et cette requête générique matche alors n'importe quel One Piece
        assert matches(japonais_partiel, "One Piece Film Z") is True
        # d'où la nécessité de la détecter en amont
        assert has_non_latin_letters(japonais_partiel) is True
