import unittest

from quasarr.api.arr import (
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


if __name__ == "__main__":
    unittest.main()
