# -*- coding: utf-8 -*-
"""Le nom de release doit se normaliser comme le titre du film côté Radarr.

Radarr relie une release à un film en comparant des titres « nettoyés » :
suppression des caractères non alphanumériques, des accents, et des articles
isolés (a, an, the, and, or, of). Si Quasarr produit un nom qui ne se nettoie
pas exactement comme le titre du film, Radarr répond :

    Unknown Movie. Unable to match to correct movie using release title.

Régression : `_normalize_title` **supprimait** l'apostrophe au lieu de la
remplacer, collant les deux moitiés d'une élision. « Y'a pas de réseau »
donnait « Ya.pas.de.reseau » : côté Radarr le film se nettoie en
« ypasdereseau » (le « a » isolé saute) alors que la release donnait
« yapasdereseau ». Aucun rapprochement possible.
"""

import re
import unicodedata

import pytest

from quasarr.search.sources.zt import _normalize_title

# Articles retirés par Radarr lorsqu'ils sont isolés.
_ARTICLES = r"(?:a|an|the|and|or|of)"
_RADARR_NORMALIZE = re.compile(rf"((?:\b|_)(?<!^){_ARTICLES}(?:\b|_))|\W|_", re.IGNORECASE)


def radarr_clean_title(title):
    """Reproduit la normalisation de Radarr pour comparer les deux côtés."""
    cleaned = _RADARR_NORMALIZE.sub("", title).lower()
    decomposed = unicodedata.normalize("NFD", cleaned)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


class TestElisionSurvivesNormalisation:
    """Cas réel remonté par Radarr : « Unknown Movie » sur tous les titres élidés."""

    @pytest.mark.parametrize("titre_film", [
        "Y'a pas de réseau",
        "L'Île des Miam-nimaux",
        "Panique sur l'île au croissant de lune",
        "L'Examen enflammé de sélection des Chûnin",
        "Comme Cendrillon 2 : Danse jusqu'au bout de la nuit",
        "Barbie, princesse de l'île merveilleuse",
    ])
    def test_release_name_cleans_like_the_movie_title(self, titre_film):
        release = _normalize_title(titre_film)
        assert radarr_clean_title(release) == radarr_clean_title(titre_film), (
            f"Radarr ne rapprochera pas {release!r} de {titre_film!r}"
        )

    def test_the_exact_reported_case(self):
        film = "Y'a pas de réseau"
        release = _normalize_title(film)
        # le point sépare bien l'élision, il ne la recolle pas
        assert release == "Y.a.pas.de.reseau"
        assert radarr_clean_title(release) == radarr_clean_title(film) == "ypasdereseau"


class TestTitlesWithoutElisionAreUnaffected:
    @pytest.mark.parametrize("titre_film", [
        "Barbie dans Casse-noisette",
        "Tempête de boulettes géantes",
        "Le Voyage de Chihiro",
        "One Piece Film - Z",
    ])
    def test_still_matches(self, titre_film):
        release = _normalize_title(titre_film)
        assert radarr_clean_title(release) == radarr_clean_title(titre_film)


class TestYearComesFromTheStructuredField:
    """L'année du site, mais celle du bon champ.

    Radarr rapproche une release sur (titre, année) : un an d'écart et c'est
    « Unknown Movie ». Zone-Téléchargement expose deux années — le champ de
    fiche « Année de production » et celle lue dans le nom de fichier surligné,
    saisi par l'uploadeur. Mesuré sur 12 films face à TMDB : le champ de fiche
    est juste 11 fois, le nom de fichier 8 fois. Cas type « Barbie apprentie
    princesse » : fiche 2011 (juste), nom de fichier 2012 (faux).

    On ne substitue **pas** l'année de TMDB : elle est ce qui distingue deux
    homonymes, et la réécrire ferait importer un autre film sans alerte.
    """

    def _detail(self, production, highlight):
        from bs4 import BeautifulSoup
        bloc = f'<font color="red">Film.{highlight}.DVDRIP</font>' if highlight else ""
        html = f"<html><body>{bloc}<p>Année de production : {production}</p></body></html>"
        return BeautifulSoup(html, "html.parser")

    def test_structured_field_wins_over_uploader_filename(self):
        from quasarr.search.sources.zt import (
            _extract_production_year, _extract_year_from_highlight,
        )
        soup = self._detail("2011", "2012")
        texte = soup.get_text(" ", strip=True)

        assert _extract_production_year(texte) == "2011"
        assert _extract_year_from_highlight(soup) == "2012"
        # la priorite appliquee dans _fetch_detail_metadata
        assert (_extract_production_year(texte) or _extract_year_from_highlight(soup)) == "2011"

    def test_filename_year_is_used_when_field_is_absent(self):
        from quasarr.search.sources.zt import (
            _extract_production_year, _extract_year_from_highlight,
        )
        soup = self._detail("", "2014")
        texte = soup.get_text(" ", strip=True)
        assert (_extract_production_year(texte) or _extract_year_from_highlight(soup)) == "2014"


class TestBothAnnouncedYearsAreOffered:
    """Le site annonce deux années : on les propose toutes les deux.

    Zone-Téléchargement expose le champ de fiche « Année de production » et
    l'année lue dans le nom de fichier surligné. Aucune des deux n'est fiable à
    tous les coups (mesuré sur 12 films : 11/12 pour la fiche, 8/12 pour le nom
    de fichier), et l'écart fait échouer le rapprochement de Radarr, qui se fait
    sur (titre, année).

    Plutôt que de parier sur l'une, la release est émise dans les deux
    millésimes : Radarr retient celui qui correspond à sa fiche et ignore
    l'autre en « Unknown Movie », sans effet de bord. On n'invente jamais
    l'année depuis TMDB : elle est ce qui distingue deux homonymes.
    """

    def _fetch(self, monkeypatch, html):
        import quasarr.search.sources.zt as zt

        class _Resp:
            status_code = 200
            url = "https://zt.test/?p=film&id=1"
            text = html

            def raise_for_status(self):
                pass

        monkeypatch.setattr(zt, "_zt_get", lambda *a, **kw: _Resp())
        monkeypatch.setattr(zt, "_update_hostname", lambda ss, cur, url: cur)
        return zt._fetch_detail_metadata(None, "https://zt.test/x", {"User-Agent": "t"}, "zt.test")

    def test_both_years_are_reported_when_they_differ(self, monkeypatch):
        result = self._fetch(monkeypatch,
                             '<html><body><font color="red">Film.2012.DVDRIP</font>'
                             '<p>Année de production : 2011</p></body></html>')
        production_year, filename_year = result[1], result[8]
        assert production_year == "2011"   # champ de fiche, prioritaire
        assert filename_year == "2012"     # nom de fichier, proposé en second

    def test_no_second_year_when_both_agree(self, monkeypatch):
        result = self._fetch(monkeypatch,
                             '<html><body><font color="red">Film.2014.DVDRIP</font>'
                             '<p>Année de production : 2014</p></body></html>')
        assert result[1] == result[8] == "2014"

    def test_filename_year_is_used_when_field_missing(self, monkeypatch):
        result = self._fetch(monkeypatch,
                             '<html><body><font color="red">Film.2014.DVDRIP</font>'
                             '</body></html>')
        assert result[1] == "2014"
