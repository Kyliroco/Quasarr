# -*- coding: utf-8 -*-
"""Unit tests for ZT parsing helper functions.

Each test targets a single helper function with representative inputs
so that any change in parsing behaviour is caught immediately.
To add a new regression case, simply append to the relevant @parametrize list.
"""

import pytest
from bs4 import BeautifulSoup

from quasarr.search.sources.zt import (
    _normalize_hoster_name,
    _episode_numbers_from_text,
    _append_host_to_title,
    _extract_year_from_highlight,
    _extract_production_year,
    _extract_size_mb,
    _extract_detail_title,
    _extract_quality_language_tokens,
    _extract_original_title,
    _extract_supported_mirrors,
    _collect_download_entries,
    _strip_parenthetical_content,
    _strip_diacritics,
    _normalize_title,
    _normalize_quality_token,
    _coerce_series_quality_tokens,
    _build_final_title,
    _ensure_episode_tag,
    _attach_episode_fragment,
    _titles_equivalent,
    _tokenize_title,
    _extract_year_from_tokens,
    _contains_year_token,
)

from tests.conftest import load_fixture, MockSharedState


# ---------------------------------------------------------------------------
# _normalize_hoster_name
# ---------------------------------------------------------------------------
class TestNormalizeHosterName:
    @pytest.mark.parametrize("raw,expected", [
        ("Rapidgator", "rapidgator"),
        ("rapidgator.net", "rapidgator"),
        ("Rapidgator.Net", "rapidgator"),
        ("RAPIDGATOR", "rapidgator"),
        ("1Fichier", "1fichier"),
        ("1fichier.com", "1fichier"),
        ("1 Fichier", "1fichier"),
        ("Nitroflare", "nitroflare"),
        ("NitroFlare.com", "nitroflare"),
        ("nitro_upload", "nitroflare"),
        ("Turbobit", "turbobit"),
        ("turbobit.net", "turbobit"),
        ("Uploady", "uploady"),
        ("DailyUploads", "dailyuploads"),
        ("Daily Uploads", "dailyuploads"),
        ("dailyupload", "dailyuploads"),
        ("DDownload", "ddownload"),
        ("DDL", "ddownload"),
        ("ddl.to", "ddownload"),
        ("", ""),
        ("  ", ""),
        ("UnknownHost", "unknownhost"),
    ])
    def test_normalization(self, raw, expected):
        assert _normalize_hoster_name(raw) == expected


# ---------------------------------------------------------------------------
# _episode_numbers_from_text
# ---------------------------------------------------------------------------
class TestEpisodeNumbersFromText:
    @pytest.mark.parametrize("text,expected", [
        ("Episode 1", {1}),
        ("Episode 1 à 5", {1, 2, 3, 4, 5}),
        ("Épisode 3", {3}),
        ("ep 10", {10}),
        ("Episode 1-3", {1, 2, 3}),
        ("Ep12", {12}),
        ("Episode 1 à 10", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}),
        ("Télécharger", set()),
        ("", set()),
        (None, set()),
        # Fallback to raw numbers when no episode keyword
        ("Lien 7", {7}),
    ])
    def test_extraction(self, text, expected):
        assert _episode_numbers_from_text(text) == expected


# ---------------------------------------------------------------------------
# _append_host_to_title
# ---------------------------------------------------------------------------
class TestAppendHostToTitle:
    @pytest.mark.parametrize("title,host,expected", [
        ("Inception.2010.1080p", "1fichier", "Inception.2010.1080p.1fichier"),
        ("Inception.2010.1080p", "rapidgator", "Inception.2010.1080p.Rapidgator"),
        ("Inception.2010.1080p.1fichier", "1fichier", "Inception.2010.1080p.1fichier"),
        ("", "1fichier", ""),
        ("Title", "", "Title"),
        ("Title", None, "Title"),
    ])
    def test_append(self, title, host, expected):
        assert _append_host_to_title(title, host) == expected


# ---------------------------------------------------------------------------
# _extract_year_from_highlight
# ---------------------------------------------------------------------------
class TestExtractYearFromHighlight:
    def test_single_year(self):
        soup = BeautifulSoup('<font color="red">Film.2010.1080p</font>', "html.parser")
        assert _extract_year_from_highlight(soup) == "2010"

    def test_multiple_years_returns_last(self):
        soup = BeautifulSoup(
            '<font color="red">Film.1999.2023.Remaster</font>', "html.parser"
        )
        assert _extract_year_from_highlight(soup) == "2023"

    def test_no_year(self):
        soup = BeautifulSoup('<font color="red">Film.1080p</font>', "html.parser")
        assert _extract_year_from_highlight(soup) == ""

    def test_no_font_tag(self):
        soup = BeautifulSoup("<div>Nothing here</div>", "html.parser")
        assert _extract_year_from_highlight(soup) == ""

    def test_none_soup(self):
        assert _extract_year_from_highlight(None) == ""


# ---------------------------------------------------------------------------
# _extract_production_year
# ---------------------------------------------------------------------------
class TestExtractProductionYear:
    @pytest.mark.parametrize("text,expected", [
        ("Année de production : 2010", "2010"),
        ("année de production: 2023", "2023"),
        ("Annee de production : 1999", "1999"),
        ("Pas d'année ici", ""),
        ("", ""),
        (None, ""),
    ])
    def test_extraction(self, text, expected):
        assert _extract_production_year(text) == expected


# ---------------------------------------------------------------------------
# _extract_size_mb
# ---------------------------------------------------------------------------
class TestExtractSizeMb:
    @pytest.fixture
    def ss(self):
        return MockSharedState()

    @pytest.mark.parametrize("text,expected_mb", [
        ("Taille du fichier : 1.5 Go", 1536),
        ("Taille du fichier : 700 Mo", 700),
        ("Taille d'un episode : ~350 Mo", 350),
        ("Taille du fichier : ≈2,8 Go", 2867),
        ("Taille du fichier : 4.5 To", 4718592),
        ("Pas de taille", 0),
        ("", 0),
        (None, 0),
    ])
    def test_extraction(self, ss, text, expected_mb):
        result = _extract_size_mb(ss, text)
        assert result == expected_mb


# ---------------------------------------------------------------------------
# _extract_detail_title
# ---------------------------------------------------------------------------
class TestExtractDetailTitle:
    def test_from_h1(self):
        soup = BeautifulSoup(
            '<div class="centersideinn"><h1>Inception</h1></div>', "html.parser"
        )
        assert _extract_detail_title(soup) == "Inception"

    def test_from_font_red_fallback(self):
        soup = BeautifulSoup(
            '<font color="red">The Dark Knight</font>', "html.parser"
        )
        assert _extract_detail_title(soup) == "The Dark Knight"

    def test_h1_preferred_over_font(self):
        soup = BeautifulSoup(
            '<div class="centersideinn"><h1>Title H1</h1></div>'
            '<font color="red">Title Font</font>',
            "html.parser",
        )
        assert _extract_detail_title(soup) == "Title H1"

    def test_empty_h1_falls_back_to_font(self):
        soup = BeautifulSoup(
            '<div class="centersideinn"><h1>  </h1></div>'
            '<font color="red">Fallback</font>',
            "html.parser",
        )
        assert _extract_detail_title(soup) == "Fallback"

    def test_no_title_elements(self):
        soup = BeautifulSoup("<div>Nothing</div>", "html.parser")
        assert _extract_detail_title(soup) is None

    def test_none_soup(self):
        assert _extract_detail_title(None) is None


# ---------------------------------------------------------------------------
# _extract_quality_language_tokens
# ---------------------------------------------------------------------------
class TestExtractQualityLanguageTokens:
    def test_pipe_separated(self):
        soup = BeautifulSoup("<div>Qualité : 1080p | VF | x264</div>", "html.parser")
        tokens = _extract_quality_language_tokens(soup)
        assert tokens == ["1080p", "VF", "x264"]

    def test_single_quality(self):
        soup = BeautifulSoup("<div>Qualité : HDRip</div>", "html.parser")
        tokens = _extract_quality_language_tokens(soup)
        assert tokens == ["HDRip"]

    def test_quality_with_separate_language(self):
        soup = BeautifulSoup(
            "<div>Qualité : 720p</div><div>Langue : French</div>", "html.parser"
        )
        tokens = _extract_quality_language_tokens(soup)
        assert "720p" in tokens
        assert "French" in tokens

    def test_no_quality(self):
        soup = BeautifulSoup("<div>Pas de qualité</div>", "html.parser")
        assert _extract_quality_language_tokens(soup) == []

    def test_none_soup(self):
        assert _extract_quality_language_tokens(None) == []

    def test_skips_egalement(self):
        soup = BeautifulSoup(
            "<div>Qualité également disponible en 4K</div>"
            "<div>Qualité : 1080p</div>",
            "html.parser",
        )
        tokens = _extract_quality_language_tokens(soup)
        assert "1080p" in tokens


# ---------------------------------------------------------------------------
# _extract_original_title
# ---------------------------------------------------------------------------
class TestExtractOriginalTitle:
    def test_from_strong_colon(self):
        soup = BeautifulSoup(
            "<strong>Titre Original : The Intouchables</strong>", "html.parser"
        )
        assert _extract_original_title(soup) == "The Intouchables"

    def test_from_strong_sibling(self):
        soup = BeautifulSoup(
            "<div><strong>Titre Original :</strong> Inception</div>", "html.parser"
        )
        assert _extract_original_title(soup) == "Inception"

    def test_no_original_title(self):
        soup = BeautifulSoup("<div>Qualité : 1080p</div>", "html.parser")
        assert _extract_original_title(soup) is None

    def test_none_soup(self):
        assert _extract_original_title(None) is None


# ---------------------------------------------------------------------------
# _collect_download_entries  (with HTML fixtures)
# ---------------------------------------------------------------------------
class TestCollectDownloadEntries:
    def _soup(self, fixture_name):
        return BeautifulSoup(load_fixture(fixture_name), "html.parser")

    def test_film_inception_entries(self):
        """Inception detail page should yield 1fichier, rapidgator, turbobit (supported),
        skip nitroflare (unsupported) and uptobox (not in SUPPORTED_MIRRORS)."""
        entries = _collect_download_entries(
            self._soup("detail_film_inception"),
            "https://www.zone-telechargement.test/",
        )
        hosts = [e["host"] for e in entries]
        assert "1fichier" in hosts
        assert "rapidgator" in hosts
        assert "turbobit" in hosts
        assert "nitroflare" not in hosts
        assert "uptobox" not in hosts
        assert len(entries) == 3

    def test_series_breakingbad_s1_entries(self):
        """Breaking Bad S1 should yield 7 episodes for 1fichier + 7 for rapidgator."""
        entries = _collect_download_entries(
            self._soup("detail_series_breakingbad_s1"),
            "https://www.zone-telechargement.test/",
        )
        fichier_entries = [e for e in entries if e["host"] == "1fichier"]
        rg_entries = [e for e in entries if e["host"] == "rapidgator"]
        assert len(fichier_entries) == 7
        assert len(rg_entries) == 7

        # Check episode extraction
        for i, entry in enumerate(fichier_entries, start=1):
            assert i in entry["episodes"]

    def test_series_onepiece_pack_episodes(self):
        """One Piece has episode packs (1-5, 6-10) + individual (11, 12)."""
        entries = _collect_download_entries(
            self._soup("detail_series_onepiece_s1"),
            "https://www.zone-telechargement.test/",
        )
        fichier_entries = [e for e in entries if e["host"] == "1fichier"]
        assert len(fichier_entries) == 4

        pack_1_5 = fichier_entries[0]
        assert pack_1_5["episodes"] == frozenset({1, 2, 3, 4, 5})

        pack_6_10 = fichier_entries[1]
        assert pack_6_10["episodes"] == frozenset({6, 7, 8, 9, 10})

        ep11 = fichier_entries[2]
        assert ep11["episodes"] == frozenset({11})

    def test_mixed_hosts_filtering(self):
        """Mixed hosts page: streaming and unsupported hosts must be filtered."""
        entries = _collect_download_entries(
            self._soup("detail_film_mixed_hosts"),
            "https://www.zone-telechargement.test/",
        )
        hosts = [e["host"] for e in entries]
        urls = [e["url"] for e in entries]

        # Only supported download (rl=a2) entries remain
        assert "1fichier" in hosts
        assert "turbobit" in hosts
        assert "rapidgator" in hosts
        assert len(entries) == 3

        # Streaming links (rl=a1, rl=h1) must be excluded
        assert not any("rl=a1" in u for u in urls)
        assert not any("rl=h1" in u for u in urls)

        # Unsupported hosts excluded
        assert "nitroflare" not in hosts
        assert "uptobox" not in hosts

    def test_no_supported_links(self):
        """Detail page with only unsupported hosts returns empty list."""
        entries = _collect_download_entries(
            self._soup("detail_film_no_links"),
            "https://www.zone-telechargement.test/",
        )
        assert entries == []

    def test_entries_have_valid_urls(self):
        """All collected entries should have absolute https URLs."""
        entries = _collect_download_entries(
            self._soup("detail_film_inception"),
            "https://www.zone-telechargement.test/",
        )
        for entry in entries:
            assert entry["url"].startswith("https://")


# ---------------------------------------------------------------------------
# _extract_supported_mirrors
# ---------------------------------------------------------------------------
class TestExtractSupportedMirrors:
    def _soup(self, fixture_name):
        return BeautifulSoup(load_fixture(fixture_name), "html.parser")

    def test_inception_mirrors(self):
        mirrors = _extract_supported_mirrors(self._soup("detail_film_inception"))
        assert "1fichier" in mirrors
        assert "rapidgator" in mirrors
        assert "turbobit" in mirrors
        assert "nitroflare" not in mirrors

    def test_no_links_page(self):
        mirrors = _extract_supported_mirrors(self._soup("detail_film_no_links"))
        assert mirrors == []


# ---------------------------------------------------------------------------
# _strip_parenthetical_content
# ---------------------------------------------------------------------------
class TestStripParentheticalContent:
    @pytest.mark.parametrize("text,expected", [
        ("Intouchables (The Intouchables)", "Intouchables"),
        ("Film (2010) Title", "Film Title"),
        ("No Parentheses", "No Parentheses"),
        ("", ""),
        (None, None),
    ])
    def test_strip(self, text, expected):
        assert _strip_parenthetical_content(text) == expected


# ---------------------------------------------------------------------------
# _strip_diacritics
# ---------------------------------------------------------------------------
class TestStripDiacritics:
    @pytest.mark.parametrize("text,expected", [
        ("École", "Ecole"),
        ("café", "cafe"),
        ("Épisode", "Episode"),
        ("naïve", "naive"),
        ("normal", "normal"),
        ("", ""),
        (None, None),
    ])
    def test_strip(self, text, expected):
        assert _strip_diacritics(text) == expected


# ---------------------------------------------------------------------------
# _normalize_title
# ---------------------------------------------------------------------------
class TestNormalizeTitle:
    @pytest.mark.parametrize("title,expected", [
        ("Inception", "Inception"),
        ("The Dark Knight", "The.Dark.Knight"),
        ("Film : Le Retour", "Film.Le.Retour"),
        ("L'aventure", "Laventure"),
        ("Saison 1 Episode 3", "S01E03"),
        ("Film  Multiple   Spaces", "Film.Multiple.Spaces"),
        ("", ""),
        (None, None),
    ])
    def test_normalization(self, title, expected):
        assert _normalize_title(title) == expected


# ---------------------------------------------------------------------------
# _normalize_quality_token
# ---------------------------------------------------------------------------
class TestNormalizeQualityToken:
    @pytest.mark.parametrize("token,expected", [
        ("hdrip", "HDTV 720p"),
        ("HDRip", "HDTV 720p"),
        ("4K", "2160p"),
        ("4k HDR", "2160p HDR"),
        ("1080p", "1080p"),
        ("VF", "VF"),
        ("", ""),
        (None, None),
    ])
    def test_normalization(self, token, expected):
        assert _normalize_quality_token(token) == expected


# ---------------------------------------------------------------------------
# _coerce_series_quality_tokens
# ---------------------------------------------------------------------------
class TestCoerceSeriesQualityTokens:
    def test_vf_hd_coerces_to_720p(self):
        quality, lang, tokens = _coerce_series_quality_tokens(True, "VF HD", [])
        assert "720p" in quality
        assert lang == "FRENCH"
        assert "720p" in tokens

    def test_vostfr_hd_coerces_to_720p(self):
        quality, lang, tokens = _coerce_series_quality_tokens(True, "VOSTFR HD", [])
        assert "720p" in quality
        assert lang is None  # VOSTFR HD doesn't set language to FRENCH

    def test_vf_alone_coerces_to_480p(self):
        quality, lang, tokens = _coerce_series_quality_tokens(True, "VF", [])
        assert "480p" in quality
        assert lang == "FRENCH"

    def test_vostfr_alone_coerces_to_480p(self):
        quality, lang, tokens = _coerce_series_quality_tokens(True, "VOSTFR", [])
        assert "480p" in quality
        assert lang is None

    def test_explicit_resolution_not_overridden(self):
        quality, lang, tokens = _coerce_series_quality_tokens(True, "1080p", ["1080p"])
        assert quality == "1080p"
        assert "1080p" in tokens

    def test_non_series_passthrough(self):
        quality, lang, tokens = _coerce_series_quality_tokens(False, "VF", [])
        assert quality == "VF"
        assert lang is None
        assert tokens == []


# ---------------------------------------------------------------------------
# _build_final_title
# ---------------------------------------------------------------------------
class TestBuildFinalTitle:
    def test_basic_film(self):
        result = _build_final_title("Inception", "Inception", "2010", ["1080p", "VF"], "")
        assert "Inception" in result
        assert "2010" in result
        assert "1080p" in result

    def test_year_already_in_title(self):
        result = _build_final_title("Inception.2010", "Inception", "2010", ["1080p"], "")
        # Year should not be duplicated
        assert result.count("2010") == 1

    def test_quality_from_text_fallback(self):
        result = _build_final_title("Film", "Film", "2023", [], "720p")
        assert "720p" in result

    def test_empty_inputs(self):
        result = _build_final_title("", "", "", [], "")
        assert result  # Should produce something (fallback)


# ---------------------------------------------------------------------------
# _ensure_episode_tag
# ---------------------------------------------------------------------------
class TestEnsureEpisodeTag:
    @pytest.mark.parametrize("title,season,episode,expected_tag", [
        ("Breaking.Bad.S01", 1, 5, "S01E05"),
        ("Breaking.Bad", 1, 5, "S01E05"),
        ("Breaking.Bad.S01E03", 1, 5, "S01E03"),  # Already has ep tag, don't change
        ("Title.S02", 2, 10, "S02E10"),
    ])
    def test_episode_injection(self, title, season, episode, expected_tag):
        result = _ensure_episode_tag(title, season, episode)
        assert expected_tag in result

    def test_no_season_or_episode(self):
        assert _ensure_episode_tag("Title", None, None) == "Title"
        assert _ensure_episode_tag("Title", 1, None) == "Title"
        assert _ensure_episode_tag("Title", None, 5) == "Title"

    def test_none_title(self):
        assert _ensure_episode_tag(None, 1, 5) is None


# ---------------------------------------------------------------------------
# _attach_episode_fragment
# ---------------------------------------------------------------------------
class TestAttachEpisodeFragment:
    def test_basic(self):
        url = "https://dl-protect.test/abc?rl=a2"
        result = _attach_episode_fragment(url, 5)
        assert result == "https://dl-protect.test/abc?rl=a2#episode=5"

    def test_existing_fragment_replaced(self):
        url = "https://dl-protect.test/abc?rl=a2#episode=3"
        result = _attach_episode_fragment(url, 7)
        assert "episode=7" in result
        assert "episode=3" not in result

    def test_non_integer_episode(self):
        url = "https://example.com"
        assert _attach_episode_fragment(url, "abc") == url
        assert _attach_episode_fragment(url, None) == url


# ---------------------------------------------------------------------------
# _titles_equivalent
# ---------------------------------------------------------------------------
class TestTitlesEquivalent:
    @pytest.mark.parametrize("a,b,expected", [
        ("Inception", "Inception", True),
        ("inception", "Inception", True),
        ("Inception.2010", "2010.Inception", True),  # Same token signature
        ("Film A", "Film B", False),
        ("", "Inception", False),
        (None, "Inception", False),
        (None, None, False),
    ])
    def test_equivalence(self, a, b, expected):
        assert _titles_equivalent(a, b) == expected


# ---------------------------------------------------------------------------
# _tokenize_title
# ---------------------------------------------------------------------------
class TestTokenizeTitle:
    @pytest.mark.parametrize("text,expected", [
        ("Inception.2010.1080p", ["Inception", "2010", "1080p"]),
        ("The Dark Knight", ["The", "Dark", "Knight"]),
        ("Film-Title_Here", ["Film", "Title", "Here"]),
        ("", []),
        (None, []),
    ])
    def test_tokenize(self, text, expected):
        assert _tokenize_title(text) == expected


# ---------------------------------------------------------------------------
# _extract_year_from_tokens
# ---------------------------------------------------------------------------
class TestExtractYearFromTokens:
    @pytest.mark.parametrize("tokens,expected", [
        (["Inception", "2010", "1080p"], "2010"),
        (["Film", "1999", "2023", "Remaster"], "2023"),
        (["Film", "1080p"], ""),
        ([], ""),
        (None, ""),
    ])
    def test_extraction(self, tokens, expected):
        assert _extract_year_from_tokens(tokens) == expected


# ---------------------------------------------------------------------------
# _contains_year_token
# ---------------------------------------------------------------------------
class TestContainsYearToken:
    @pytest.mark.parametrize("text,year,expected", [
        ("Inception.2010.1080p", "2010", True),
        ("Film.1080p", "2010", False),
        ("Film.12010.1080p", "2010", False),  # Year embedded in larger number
        ("", "2010", False),
        ("Film", "", False),
        (None, "2010", False),
    ])
    def test_detection(self, text, year, expected):
        assert _contains_year_token(text, year) == expected


# ---------------------------------------------------------------------------
# Full detail page extraction with fixtures
# ---------------------------------------------------------------------------
class TestDetailPageExtraction:
    """Integration tests that load HTML fixtures and run multiple extraction
    functions to verify they all agree on the same detail page."""

    def _soup(self, name):
        return BeautifulSoup(load_fixture(name), "html.parser")

    def test_inception_detail(self):
        soup = self._soup("detail_film_inception")
        ss = MockSharedState()
        text = soup.get_text(" ", strip=True)

        assert _extract_detail_title(soup) == "Inception"
        assert _extract_original_title(soup) == "Inception"
        assert _extract_production_year(text) == "2010"
        assert _extract_year_from_highlight(soup) == "2010"
        assert _extract_size_mb(ss, text) == 1536  # 1.5 Go
        tokens = _extract_quality_language_tokens(soup)
        assert "BluRay.1080p" in tokens or "1080p" in tokens

    def test_intouchables_detail(self):
        soup = self._soup("detail_film_intouchables")
        ss = MockSharedState()
        text = soup.get_text(" ", strip=True)

        assert _extract_detail_title(soup) == "Intouchables (The Intouchables)"
        assert _extract_original_title(soup) == "The Intouchables"
        assert _extract_production_year(text) == "2011"
        assert _extract_size_mb(ss, text) == 1228  # 1.2 Go

    def test_breakingbad_s1_detail(self):
        soup = self._soup("detail_series_breakingbad_s1")
        ss = MockSharedState()
        text = soup.get_text(" ", strip=True)

        assert _extract_detail_title(soup) == "Breaking Bad - Saison 1"
        assert _extract_original_title(soup) == "Breaking Bad"
        assert _extract_production_year(text) == "2008"
        assert _extract_size_mb(ss, text) == 350  # ~350 Mo

    def test_onepiece_s1_detail(self):
        soup = self._soup("detail_series_onepiece_s1")
        ss = MockSharedState()
        text = soup.get_text(" ", strip=True)

        assert _extract_detail_title(soup) == "One Piece - Saison 1"
        assert _extract_original_title(soup) is None  # No original title in this fixture
        assert _extract_production_year(text) == "1999"
        assert _extract_size_mb(ss, text) == 180  # ~180 Mo

    def test_size_with_comma_and_tilde(self):
        soup = self._soup("detail_film_size_variants")
        ss = MockSharedState()
        text = soup.get_text(" ", strip=True)
        assert _extract_size_mb(ss, text) == 2867  # ~2,8 Go → 2867 MB
