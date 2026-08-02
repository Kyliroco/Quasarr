# -*- coding: utf-8 -*-
"""Routage anime-sama / zt.

`is_anime()` décide si une recherche part sur anime-sama (et exclut zt du run
parallèle, zt ne servant plus que de secours) ou reste sur zt.

Régression : un **film** d'animation japonaise (One Piece film 5, Film Red…)
était classé anime. anime-sama, qui ne diffuse que des épisodes de séries, y
répondait 0 résultat en 9 à 18 s, puis zt était relancé en secours — sérialisé
derrière. Un film reste donc un film, quelle que soit son origine.
"""

import pytest

from quasarr.providers import imdb_metadata


def _tmdb(media_type, genre_ids, language='ja', countries=('JP',)):
    payload = {'genre_ids': list(genre_ids),
               'original_language': language,
               'origin_country': list(countries)}
    return lambda imdb_id, language=None: (payload, media_type)


class TestMoviesAreNeverRoutedToAnimeSama:
    @pytest.mark.parametrize("genres", [
        [16, 12, 28],           # One Piece film 5
        [16, 12, 28, 14, 10402],  # One Piece Film Red
        [16],                    # animation pure
    ])
    def test_japanese_animated_movie_is_not_anime(self, monkeypatch, genres):
        monkeypatch.setattr(imdb_metadata, "_tmdb_find", _tmdb('movie', genres))
        assert imdb_metadata.is_anime(None, "tt1010435") is False


class TestSeriesRoutingIsUnchanged:
    def test_japanese_animated_series_is_anime(self, monkeypatch):
        monkeypatch.setattr(imdb_metadata, "_tmdb_find", _tmdb('tv', [16, 10759]))
        assert imdb_metadata.is_anime(None, "tt0409591") is True

    def test_western_animated_series_is_not_anime(self, monkeypatch):
        monkeypatch.setattr(
            imdb_metadata, "_tmdb_find",
            _tmdb('tv', [16], language='en', countries=('US',)),
        )
        assert imdb_metadata.is_anime(None, "tt0182576") is False

    def test_live_action_japanese_series_is_not_anime(self, monkeypatch):
        monkeypatch.setattr(imdb_metadata, "_tmdb_find", _tmdb('tv', [18]))
        assert imdb_metadata.is_anime(None, "tt0000000") is False


class TestUnknownTitle:
    def test_missing_tmdb_result_is_not_anime(self, monkeypatch):
        monkeypatch.setattr(
            imdb_metadata, "_tmdb_find", lambda imdb_id, language=None: (None, None),
        )
        assert imdb_metadata.is_anime(None, "tt9999999") is False
