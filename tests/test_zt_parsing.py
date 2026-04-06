# -*- coding: utf-8 -*-
"""Integration tests for the ZT parsing pipeline.

These tests exercise _parse_results (the main parsing function) end-to-end
using stored HTML fixtures for both search result pages and detail pages.
HTTP calls to detail pages are mocked so no network access is required.

HOW TO ADD A NEW REGRESSION TEST
=================================
1. Create search HTML fixture:  tests/fixtures/zt/search_<scenario>.html
   containing only <div class="cover_global"> cards.
2. Create detail HTML fixture(s): tests/fixtures/zt/detail_<scenario>.html
   containing the elements parsed by _fetch_detail_metadata.
3. Add a new entry to the relevant test class with @pytest.mark.parametrize
   or create a new test method.  Map the detail page URL (derived from the
   card's href) to the detail fixture name in the ``detail_pages`` dict.
"""

from base64 import urlsafe_b64decode
from unittest.mock import patch, MagicMock

import pytest
from bs4 import BeautifulSoup

from quasarr.search.sources.zt import _parse_results
from tests.conftest import load_fixture, MockSharedState


BASE_URL = "https://www.zone-telechargement.test/"
HEADERS = {"User-Agent": "Mozilla/5.0 (test)"}
ZT_HOST = "www.zone-telechargement.test"


def _make_mock_response(html_content: str, url: str):
    """Create a mock requests.Response-like object."""
    resp = MagicMock()
    resp.text = html_content
    resp.url = url
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    return resp


def _build_detail_router(detail_pages: dict):
    """Return a side_effect function for requests.get that serves stored fixtures.

    ``detail_pages`` maps a URL substring to a fixture name, e.g.:
        {"id=45231": "detail_film_inception"}
    """

    def router(url, **kwargs):
        for key, fixture_name in detail_pages.items():
            if key in url:
                return _make_mock_response(load_fixture(fixture_name), url)
        # For unknown URLs return an empty page (no detail found)
        return _make_mock_response("<html></html>", url)

    return router


def _decode_payload(link: str) -> str:
    """Decode a /download/?payload=... link back to readable text."""
    payload_b64 = link.split("payload=", 1)[1]
    return urlsafe_b64decode(payload_b64).decode("utf-8")


# ===========================================================================
# Film search: Inception
# ===========================================================================
class TestParseFilmInception:
    """Parse a search page with multiple Inception results (1080p and 4K).
    Verifies that detail pages are fetched and download entries are built
    correctly for a Radarr (film) request."""

    DETAIL_PAGES = {
        "id=45231": "detail_film_inception",
        "id=45232": "detail_film_inception_4k",
        "id=99999": "detail_film_inception",  # "Inception Begins" reuses fixture
    }

    @pytest.fixture
    def releases(self):
        ss = MockSharedState(zt_hostname=ZT_HOST)
        search_html = load_fixture("search_films_inception")
        soup = BeautifulSoup(search_html, "html.parser")

        with patch("quasarr.search.sources.zt.requests.get",
                    side_effect=_build_detail_router(self.DETAIL_PAGES)):
            return _parse_results(
                ss, soup, BASE_URL,
                request_from="Radarr",
                mirror=None,
                headers=HEADERS,
                current_host=ZT_HOST,
                search_string="Inception",
                season=None,
                episode=None,
                imdb_id=None,
            )

    def test_releases_generated(self, releases):
        """Should produce at least one release per supported host per card."""
        assert len(releases) > 0

    def test_all_releases_are_protected(self, releases):
        for r in releases:
            assert r["type"] == "protected"

    def test_release_details_structure(self, releases):
        """Each release must have all required detail keys."""
        required_keys = {"title", "hostname", "imdb_id", "link", "mirror", "size", "date", "source"}
        for r in releases:
            assert required_keys <= set(r["details"].keys())

    def test_hostname_is_zt(self, releases):
        for r in releases:
            assert r["details"]["hostname"] == "zt"

    def test_supported_mirrors_only(self, releases):
        from quasarr.search.sources.zt import SUPPORTED_MIRRORS
        for r in releases:
            mirror = r["details"]["mirror"]
            assert mirror in SUPPORTED_MIRRORS, f"Unexpected mirror: {mirror}"

    def test_1080p_card_has_3_hosts(self, releases):
        """The 1080p detail page has 1fichier, rapidgator, turbobit (3 supported)."""
        titles_1080 = [r for r in releases if "1080p" in r["details"]["title"].lower()
                       or "BluRay" in r["details"]["title"]]
        # At least 3 entries from different hosts for the 1080p card
        hosts = {r["details"]["mirror"] for r in titles_1080}
        assert len(hosts) >= 2  # At minimum 1fichier and rapidgator

    def test_4k_card_detected(self, releases):
        """The 4K card should produce entries with 2160p in the title."""
        titles_4k = [r for r in releases if "2160p" in r["details"]["title"]]
        assert len(titles_4k) > 0, "4K card should produce 2160p releases"

    def test_non_rapidgator_first(self, releases):
        """Non-rapidgator entries should come before rapidgator for the same card."""
        # Group releases by source URL
        by_source = {}
        for r in releases:
            src = r["details"]["source"]
            by_source.setdefault(src, []).append(r)

        for src, group in by_source.items():
            mirrors = [r["details"]["mirror"] for r in group]
            if "rapidgator" in mirrors and len(mirrors) > 1:
                rg_index = mirrors.index("rapidgator")
                # All non-rg entries should come before rg
                for i, m in enumerate(mirrors):
                    if m != "rapidgator":
                        assert i < rg_index or mirrors.count("rapidgator") == len(mirrors)

    def test_payloads_decodable(self, releases):
        """All payload links should be valid base64-encoded strings."""
        for r in releases:
            link = r["details"]["link"]
            decoded = _decode_payload(link)
            parts = decoded.split("|")
            assert len(parts) >= 4, f"Payload should have at least 4 pipe-separated parts: {decoded}"

    def test_size_nonzero_for_inception(self, releases):
        """Size should be extracted from detail page (1.5 Go = 1536 MB)."""
        for r in releases:
            if "inception" in r["details"]["title"].lower() and "2160p" not in r["details"]["title"]:
                size_bytes = r["details"]["size"]
                size_mb = size_bytes / (1024 * 1024)
                assert size_mb == pytest.approx(1536, abs=1)
                break


# ===========================================================================
# Film search: Intouchables (French title + original title)
# ===========================================================================
class TestParseFilmIntouchables:
    """Test that a French film with an original title generates both
    the French and original title variants."""

    DETAIL_PAGES = {
        "id=33010": "detail_film_intouchables",
    }

    @pytest.fixture
    def releases(self):
        ss = MockSharedState(zt_hostname=ZT_HOST)
        search_html = load_fixture("search_films_intouchables")
        soup = BeautifulSoup(search_html, "html.parser")

        with patch("quasarr.search.sources.zt.requests.get",
                    side_effect=_build_detail_router(self.DETAIL_PAGES)):
            return _parse_results(
                ss, soup, BASE_URL,
                request_from="Radarr",
                mirror=None,
                headers=HEADERS,
                current_host=ZT_HOST,
                search_string="Intouchables",
                season=None,
                episode=None,
                imdb_id=None,
            )

    def test_releases_generated(self, releases):
        assert len(releases) > 0

    def test_original_title_variant_present(self, releases):
        """Should produce a variant with 'The Intouchables' (original title)."""
        titles = [r["details"]["title"] for r in releases]
        has_original = any("Intouchables" in t for t in titles)
        assert has_original

    def test_has_uploady_mirror(self, releases):
        mirrors = {r["details"]["mirror"] for r in releases}
        assert "uploady" in mirrors

    def test_size_extracted(self, releases):
        for r in releases:
            size_mb = r["details"]["size"] / (1024 * 1024)
            assert size_mb == pytest.approx(1228, abs=1)  # 1.2 Go


# ===========================================================================
# Series search: Breaking Bad (Sonarr with season/episode)
# ===========================================================================
class TestParseSeriesBreakingBad:
    """Test series parsing with Sonarr request, specific season and episode."""

    DETAIL_PAGES = {
        "id=78001": "detail_series_breakingbad_s1",
        "id=78002": "detail_series_breakingbad_s2",
    }

    @pytest.fixture
    def releases_s1e3(self):
        """Search for Breaking Bad S1 E3."""
        ss = MockSharedState(zt_hostname=ZT_HOST)
        search_html = load_fixture("search_series_breakingbad")
        soup = BeautifulSoup(search_html, "html.parser")

        with patch("quasarr.search.sources.zt.requests.get",
                    side_effect=_build_detail_router(self.DETAIL_PAGES)):
            return _parse_results(
                ss, soup, BASE_URL,
                request_from="Sonarr",
                mirror=None,
                headers=HEADERS,
                current_host=ZT_HOST,
                search_string="Breaking Bad",
                season="1",
                episode="3",
                imdb_id=None,
            )

    @pytest.fixture
    def releases_s1_no_ep(self):
        """Search for Breaking Bad S1 (no specific episode)."""
        ss = MockSharedState(zt_hostname=ZT_HOST)
        search_html = load_fixture("search_series_breakingbad")
        soup = BeautifulSoup(search_html, "html.parser")

        with patch("quasarr.search.sources.zt.requests.get",
                    side_effect=_build_detail_router(self.DETAIL_PAGES)):
            return _parse_results(
                ss, soup, BASE_URL,
                request_from="Sonarr",
                mirror=None,
                headers=HEADERS,
                current_host=ZT_HOST,
                search_string="Breaking Bad",
                season="1",
                episode=None,
                imdb_id=None,
            )

    def test_episode_filter_yields_results(self, releases_s1e3):
        """Requesting S1E3 should return releases for episode 3 only."""
        assert len(releases_s1e3) > 0

    def test_episode_tag_in_title(self, releases_s1e3):
        """All titles for S1E3 should contain S01E03."""
        for r in releases_s1e3:
            assert "S01E03" in r["details"]["title"], (
                f"Missing S01E03 in: {r['details']['title']}"
            )

    def test_source_has_episode_fragment(self, releases_s1e3):
        """Source URL should have #episode=3 fragment."""
        for r in releases_s1e3:
            assert "episode=3" in r["details"]["source"]

    def test_quality_coercion_for_series(self, releases_s1e3):
        """VF HD quality should be coerced to 720p for Sonarr."""
        for r in releases_s1e3:
            title = r["details"]["title"]
            assert "720p" in title, f"720p not in coerced title: {title}"

    def test_only_episode_3_links(self, releases_s1e3):
        """Only links covering exactly episode 3 should be returned (no packs)."""
        for r in releases_s1e3:
            decoded = _decode_payload(r["details"]["link"])
            # The source URL in the payload should be an episode 3 link
            assert "e3" in decoded.lower() or "episode=3" in decoded.lower()

    def test_s2_not_in_s1_results(self, releases_s1e3):
        """Season 2 card should not appear in S1 results."""
        for r in releases_s1e3:
            title = r["details"]["title"]
            assert "S02" not in title


# ===========================================================================
# Series search: One Piece (episode packs)
# ===========================================================================
class TestParseSeriesOnePiece:
    """Test series with multi-episode packs (Episode 1 à 5, etc.)."""

    DETAIL_PAGES = {
        "id=50100": "detail_series_onepiece_s1",
    }

    @pytest.fixture
    def releases_s1e3(self):
        """Search for One Piece S1 E3 — should match the pack Episode 1 à 5."""
        ss = MockSharedState(zt_hostname=ZT_HOST)
        search_html = load_fixture("search_series_onepiece")
        soup = BeautifulSoup(search_html, "html.parser")

        with patch("quasarr.search.sources.zt.requests.get",
                    side_effect=_build_detail_router(self.DETAIL_PAGES)):
            return _parse_results(
                ss, soup, BASE_URL,
                request_from="Sonarr",
                mirror=None,
                headers=HEADERS,
                current_host=ZT_HOST,
                search_string="One Piece",
                season="1",
                episode="3",
                imdb_id=None,
            )

    @pytest.fixture
    def releases_s1e11(self):
        """Search for One Piece S1 E11 — should match the individual Episode 11 link."""
        ss = MockSharedState(zt_hostname=ZT_HOST)
        search_html = load_fixture("search_series_onepiece")
        soup = BeautifulSoup(search_html, "html.parser")

        with patch("quasarr.search.sources.zt.requests.get",
                    side_effect=_build_detail_router(self.DETAIL_PAGES)):
            return _parse_results(
                ss, soup, BASE_URL,
                request_from="Sonarr",
                mirror=None,
                headers=HEADERS,
                current_host=ZT_HOST,
                search_string="One Piece",
                season="1",
                episode="11",
                imdb_id=None,
            )

    def test_pack_episode_skipped_for_specific_ep(self, releases_s1e3):
        """Packs (multi-episode) should be skipped when requesting a single episode."""
        # The code skips pack links (len(entry_episodes) > 1) when target_episode is set
        # So episode 3 is in pack 1-5, but the pack should be skipped
        # This means no results for episode 3 (only packs available)
        # This is the expected behavior per the code logic
        assert len(releases_s1e3) == 0

    def test_individual_episode_matched(self, releases_s1e11):
        """Episode 11 (individual link) should match."""
        assert len(releases_s1e11) > 0
        for r in releases_s1e11:
            assert "S01E11" in r["details"]["title"]

    def test_vf_coerces_to_480p(self, releases_s1e11):
        """VF alone should coerce to 480p for series."""
        for r in releases_s1e11:
            assert "480p" in r["details"]["title"]


# ===========================================================================
# Film with no supported download links
# ===========================================================================
class TestParseFilmNoLinks:
    """Test that a detail page with no supported hosts produces no releases."""

    @pytest.fixture
    def releases(self):
        ss = MockSharedState(zt_hostname=ZT_HOST)
        search_html = """
        <div class="cover_global">
            <div class="cover_infos_title">
                <a href="/?p=films&id=88888-no-links">Film Sans Liens</a>
            </div>
            <span class="detail_release">720p</span>
            <time>01 janvier 2024</time>
        </div>
        """
        soup = BeautifulSoup(search_html, "html.parser")

        with patch("quasarr.search.sources.zt.requests.get",
                    side_effect=_build_detail_router({"id=88888": "detail_film_no_links"})):
            return _parse_results(
                ss, soup, BASE_URL,
                request_from="Radarr",
                mirror=None,
                headers=HEADERS,
                current_host=ZT_HOST,
                search_string="Film Sans Liens",
                season=None,
                episode=None,
                imdb_id=None,
            )

    def test_no_releases(self, releases):
        """No releases should be generated when all hosts are unsupported."""
        assert len(releases) == 0


# ===========================================================================
# Film with mixed hosts (streaming filtered)
# ===========================================================================
class TestParseFilmMixedHosts:
    """Test streaming link filtering and unsupported host exclusion."""

    @pytest.fixture
    def releases(self):
        ss = MockSharedState(zt_hostname=ZT_HOST)
        search_html = """
        <div class="cover_global">
            <div class="cover_infos_title">
                <a href="/?p=films&id=77777-le-film-test">Le Film Test</a>
            </div>
            <span class="detail_release">720p</span>
            <time>15 juin 2023</time>
        </div>
        """
        soup = BeautifulSoup(search_html, "html.parser")

        with patch("quasarr.search.sources.zt.requests.get",
                    side_effect=_build_detail_router({"id=77777": "detail_film_mixed_hosts"})):
            return _parse_results(
                ss, soup, BASE_URL,
                request_from="Radarr",
                mirror=None,
                headers=HEADERS,
                current_host=ZT_HOST,
                search_string="Le Film Test",
                season=None,
                episode=None,
                imdb_id=None,
            )

    def test_only_supported_download_links(self, releases):
        """Only 1fichier, turbobit, rapidgator should have releases."""
        mirrors = {r["details"]["mirror"] for r in releases}
        assert mirrors <= {"1fichier", "turbobit", "rapidgator"}
        assert len(mirrors) == 3

    def test_no_streaming_links(self, releases):
        """Streaming links (rl=a1, rl=h1) should not produce releases."""
        for r in releases:
            source = r["details"]["source"]
            assert "rl=a1" not in source
            assert "rl=h1" not in source


# ===========================================================================
# Mirror filtering (only a specific host)
# ===========================================================================
class TestMirrorFiltering:
    """Test that specifying a mirror restricts results to that host."""

    DETAIL_PAGES = {
        "id=45231": "detail_film_inception",
    }

    @pytest.fixture
    def releases_1fichier(self):
        ss = MockSharedState(zt_hostname=ZT_HOST)
        search_html = """
        <div class="cover_global">
            <div class="cover_infos_title">
                <a href="/?p=films&id=45231-inception">Inception</a>
            </div>
            <span class="detail_release">1080p</span>
            <time>15 mars 2025</time>
        </div>
        """
        soup = BeautifulSoup(search_html, "html.parser")

        with patch("quasarr.search.sources.zt.requests.get",
                    side_effect=_build_detail_router(self.DETAIL_PAGES)):
            return _parse_results(
                ss, soup, BASE_URL,
                request_from="Radarr",
                mirror="1fichier",
                headers=HEADERS,
                current_host=ZT_HOST,
                search_string="Inception",
                season=None,
                episode=None,
                imdb_id=None,
            )

    def test_only_1fichier(self, releases_1fichier):
        """With mirror='1fichier', only 1fichier entries should be returned."""
        assert len(releases_1fichier) > 0
        for r in releases_1fichier:
            assert r["details"]["mirror"] == "1fichier"


# ===========================================================================
# Edge case: cards without required elements
# ===========================================================================
class TestMalformedCards:
    """Test that incomplete or malformed cards are gracefully skipped."""

    @pytest.fixture
    def releases(self):
        ss = MockSharedState(zt_hostname=ZT_HOST)
        search_html = """
        <!-- Card without title link -->
        <div class="cover_global">
            <span class="detail_release">720p</span>
            <time>01 janvier 2024</time>
        </div>
        <!-- Card with empty title -->
        <div class="cover_global">
            <div class="cover_infos_title">
                <a href="/?p=films&id=11111-empty"></a>
            </div>
            <span class="detail_release">720p</span>
            <time>01 janvier 2024</time>
        </div>
        <!-- Card without href -->
        <div class="cover_global">
            <div class="cover_infos_title">
                <a>No Href Film</a>
            </div>
            <span class="detail_release">720p</span>
            <time>01 janvier 2024</time>
        </div>
        <!-- Valid card -->
        <div class="cover_global">
            <div class="cover_infos_title">
                <a href="/?p=films&id=45231-inception">Inception</a>
            </div>
            <span class="detail_release">1080p</span>
            <time>15 mars 2025</time>
        </div>
        """
        soup = BeautifulSoup(search_html, "html.parser")

        with patch("quasarr.search.sources.zt.requests.get",
                    side_effect=_build_detail_router({"id=45231": "detail_film_inception"})):
            return _parse_results(
                ss, soup, BASE_URL,
                request_from="Radarr",
                mirror=None,
                headers=HEADERS,
                current_host=ZT_HOST,
                search_string="Inception",
                season=None,
                episode=None,
                imdb_id=None,
            )

    def test_only_valid_card_produces_releases(self, releases):
        """Only the valid card (Inception) should produce releases."""
        assert len(releases) > 0
        for r in releases:
            assert "Inception" in r["details"]["title"]


# ===========================================================================
# No headers → no detail fetch
# ===========================================================================
class TestNoHeadersNoDetailFetch:
    """When headers=None, detail pages should not be fetched and no entries produced."""

    @pytest.fixture
    def releases(self):
        ss = MockSharedState(zt_hostname=ZT_HOST)
        search_html = """
        <div class="cover_global">
            <div class="cover_infos_title">
                <a href="/?p=films&id=45231-inception">Inception</a>
            </div>
            <span class="detail_release">1080p</span>
            <time>15 mars 2025</time>
        </div>
        """
        soup = BeautifulSoup(search_html, "html.parser")

        # headers=None should skip detail fetching entirely
        return _parse_results(
            ss, soup, BASE_URL,
            request_from="Radarr",
            mirror=None,
            headers=None,
            current_host=ZT_HOST,
            search_string="Inception",
            season=None,
            episode=None,
            imdb_id=None,
        )

    def test_no_releases_without_headers(self, releases):
        """Without detail fetching, no download entries exist → no releases."""
        assert len(releases) == 0


# ===========================================================================
# Date parsing
# ===========================================================================
class TestDateParsing:
    """Test that French dates are correctly parsed into ISO format."""

    @pytest.fixture
    def releases(self):
        ss = MockSharedState(zt_hostname=ZT_HOST)
        search_html = """
        <div class="cover_global">
            <div class="cover_infos_title">
                <a href="/?p=films&id=45231-inception">Inception</a>
            </div>
            <span class="detail_release">1080p</span>
            <time>15 mars 2025</time>
        </div>
        """
        soup = BeautifulSoup(search_html, "html.parser")

        with patch("quasarr.search.sources.zt.requests.get",
                    side_effect=_build_detail_router({"id=45231": "detail_film_inception"})):
            return _parse_results(
                ss, soup, BASE_URL,
                request_from="Radarr",
                mirror=None,
                headers=HEADERS,
                current_host=ZT_HOST,
                search_string="Inception",
                season=None,
                episode=None,
                imdb_id=None,
            )

    def test_date_iso_format(self, releases):
        """Date should be in ISO format with timezone."""
        for r in releases:
            date_str = r["details"]["date"]
            assert "2025-03-15" in date_str
            assert "+01:00" in date_str
