import unittest

from quasarr.api.arr import (
    _build_caps_xml,
    _expand_category_ids,
    _derive_newznab_category,
    _filter_releases_by_categories,
    _format_newznab_attrs,
)


class ArrIndexerHelperTests(unittest.TestCase):
    def test_movie_requests_map_to_movies_category(self):
        category_id, name = _derive_newznab_category("Radarr/5", "movie")
        self.assertEqual(category_id, "2000")
        self.assertEqual(name, "Movies")

    def test_tv_requests_map_to_tv_category(self):
        category_id, name = _derive_newznab_category("Sonarr/4", "tvsearch")
        self.assertEqual(category_id, "5000")
        self.assertEqual(name, "TV")

    def test_docs_requests_map_to_books_category(self):
        category_id, name = _derive_newznab_category("LazyLibrarian", "search")
        self.assertEqual(category_id, "7000")
        self.assertEqual(name, "Books")

    def test_release_category_overrides_detection(self):
        category_id, name = _derive_newznab_category("Radarr/5", "movie", "5000")
        self.assertEqual(category_id, "5000")
        self.assertEqual(name, "TV")

    def test_newznab_attrs_formatting(self):
        attrs = _format_newznab_attrs("2000", "tt1234567", 12345)
        expected = (
            " " * 28 + "<newznab:attr name=\"category\" value=\"2000\" />\n"
            + " " * 28 + "<newznab:attr name=\"imdbid\" value=\"1234567\" />\n"
            + " " * 28 + "<newznab:attr name=\"size\" value=\"12345\" />"
        )
        self.assertEqual(attrs, expected)

    def test_newznab_attrs_skip_when_empty(self):
        self.assertEqual(_format_newznab_attrs(None, None, 0), "")

    def test_category_filter_returns_matching_releases(self):
        releases = [
            {"details": {"category": "2000", "title": "Movie"}},
            {"details": {"category": "5000", "title": "Show"}},
        ]

        filtered = _filter_releases_by_categories(
            releases,
            {"2000"},
            "Radarr/5",
            "movie",
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["details"]["title"], "Movie")

    def test_category_filter_matches_parent_for_subcategories(self):
        releases = [
            {"details": {"category": "2000", "title": "Movie"}},
        ]

        filtered = _filter_releases_by_categories(
            releases,
            {"2010"},
            "Radarr/5",
            "movie",
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["details"]["title"], "Movie")

    def test_expand_category_ids_includes_parent(self):
        expanded = _expand_category_ids({"2010", "5000"})
        self.assertIn("2010", expanded)
        self.assertIn("2000", expanded)
        self.assertIn("5000", expanded)

    def test_category_filter_drops_unclassified_releases(self):
        releases = [
            {"details": {}},
            {"details": {"category": "5000"}},
        ]

        filtered = _filter_releases_by_categories(
            releases,
            {"2000"},
            "Radarr/5",
            "movie",
        )

        self.assertFalse(filtered)

    def test_caps_xml_matches_expected_format(self):
        xml_output = _build_caps_xml(
            "http://indexer.local",
            "1.2.3",
            last_update="2025-01-01T00:00:00Z",
        )

        expected = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<caps>\n"
            "  <server version=\"1.2.3\" title=\"Quasarr\" strapline=\"Maison Energy indexer bridge\"\n"
            "      email=\"support@quasarr.app\" url=\"http://indexer.local/\"\n"
            "      image=\"http://indexer.local/static/logo.png\" />\n"
            "  <limits max=\"100\" default=\"50\" />\n"
            "  <retention days=\"0\" />\n"
            "  <registration available=\"no\" open=\"no\" />\n"
            "  <searching>\n"
            "    <search available=\"yes\" supportedParams=\"q\" />\n"
            "    <tv-search available=\"yes\" supportedParams=\"imdbid,season,ep\" />\n"
            "    <movie-search available=\"yes\" supportedParams=\"imdbid\" />\n"
            "    <audio-search available=\"no\" supportedParams=\"q\" />\n"
            "    <book-search available=\"no\" supportedParams=\"q\" />\n"
            "  </searching>\n"
            "  <categories>\n"
            "    <category id=\"2000\" name=\"Movies\">\n"
            "      <subcat id=\"2010\" name=\"Foreign\" />\n"
            "    </category>\n"
            "    <category id=\"5000\" name=\"TV\">\n"
            "      <subcat id=\"5040\" name=\"HD\" />\n"
            "      <subcat id=\"5070\" name=\"Anime\" />\n"
            "    </category>\n"
            "  </categories>\n"
            "  <groups>\n"
            "    <group id=\"1\" name=\"maison.energy\" description=\"Maison Energy releases\" lastupdate=\"2025-01-01T00:00:00Z\" />\n"
            "  </groups>\n"
            "  <genres>\n"
            "    <genre id=\"1\" categoryid=\"5000\" name=\"Anime\" />\n"
            "  </genres>\n"
            "  <tags>\n"
            "    <tag name=\"anonymous\" description=\"Uploader is anonymous\" />\n"
            "    <tag name=\"trusted\" description=\"Uploader has high reputation\" />\n"
            "    <tag name=\"internal\" description=\"Uploader is an internal release group\" />\n"
            "  </tags>\n"
            "</caps>"
        )

        self.assertEqual(xml_output, expected)


if __name__ == "__main__":
    unittest.main()
