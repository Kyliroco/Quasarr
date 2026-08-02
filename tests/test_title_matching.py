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


class TestLinkingWordInsertedByTmdb:
    """TMDB insère parfois un mot de liaison absent du libellé du site.

    Cas réel : Radarr veut « Barbie dans cœur de princesse » (TMDB) alors que
    Zone-Téléchargement liste « Barbie coeur de princesse ». Le « dans » en trop
    n'est pas en fin de chaîne, donc le repli par préfixe ne le rattrapait pas
    et le film restait introuvable.
    """

    @pytest.mark.parametrize("search, zt_title", [
        ("Barbie dans cœur de princesse", "Barbie coeur de princesse"),
        ("Barbie dans Casse-noisette", "Barbie Casse-noisette"),
        ("Le Voyage de Chihiro", "Voyage Chihiro"),
    ])
    def test_accepted(self, search, zt_title):
        assert matches(search, zt_title) is True

    @pytest.mark.parametrize("search, zt_title", [
        # l'ordre compte : ce n'est pas une simple inclusion de mots
        ("Barbie dans cœur de princesse", "Barbie princesse coeur"),
        # un mot absent de la recherche suffit à rejeter
        ("Barbie dans cœur de princesse", "Barbie au bal des princesses"),
        ("Barbie dans cœur de princesse", "Barbie et le Palais de Diamant"),
    ])
    def test_rejected(self, search, zt_title):
        assert matches(search, zt_title) is False

    def test_sequel_numbering_still_blocks(self):
        # le garde-fou d'origine reste prioritaire sur ce repli
        assert matches("Shrek 2", "Shrek") is False


class TestFrenchLigatures:
    """œ et æ doivent être transcrites, pas supprimées.

    NFD ne les décompose pas : elles arrivaient intactes jusqu'au filtre ASCII
    qui les effaçait. "cœur" devenait "cur", qui ne correspond plus au "coeur"
    écrit par le site. Bug d'autant plus gênant sur un fork français.
    """

    @pytest.mark.parametrize("mot, attendu", [
        ("cœur", "coeur"),
        ("sœur", "soeur"),
        ("œuvre", "oeuvre"),
        ("vœux", "voeux"),
        ("nævus", "naevus"),
        ("CŒUR", "coeur"),
    ])
    def test_ligature_is_transcribed(self, mot, attendu):
        assert sanitize_string(mot) == attendu

    @pytest.mark.parametrize("search, zt_title", [
        ("Barbie dans cœur de princesse", "Barbie coeur de princesse"),
        ("Naruto, le Génie Et Les Trois Vœux", "Naruto le Genie et les Trois Voeux"),
    ])
    def test_titles_with_ligatures_match_their_ascii_spelling(self, search, zt_title):
        assert matches(search, zt_title) is True


class TestLigaturesAreNotMistakenForForeignScript:
    """Régression : le garde-fou « non latin » écartait les titres français.

    has_non_latin_letters() rejetait "cœur" parce que NFD ne décompose pas la
    ligature. Conséquence : la requête entière était abandonnée avant même
    d'interroger le site, et le film restait introuvable. Les deux fonctions
    partagent désormais la même table de transcription.
    """

    @pytest.mark.parametrize("titre", [
        "Barbie dans cœur de princesse",
        "Naruto, le Génie Et Les Trois Vœux",
        "Le Cœur des hommes",
        "Sœurs d'armes",
        "Nævus",
    ])
    def test_french_ligature_is_latin(self, titre):
        assert has_non_latin_letters(titre) is False

    @pytest.mark.parametrize("titre", [
        "ONE PIECE 呵われた聖剣",
        "進撃の巨人",
    ])
    def test_real_foreign_script_still_detected(self, titre):
        assert has_non_latin_letters(titre) is True

    def test_transcription_matches_sanitize_string(self):
        """Les deux fonctions doivent s'accorder, sinon on écarte à tort."""
        for titre in ["cœur", "sœur", "nævus", "Straße"]:
            assert has_non_latin_letters(titre) is False
            # sanitize_string conserve bien le mot au lieu de l'amputer
            assert sanitize_string(titre) != ""
            assert len(sanitize_string(titre)) >= len(titre) - 1
