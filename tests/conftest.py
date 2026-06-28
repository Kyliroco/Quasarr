# -*- coding: utf-8 -*-
"""Shared fixtures and helpers for ZT parsing regression tests."""

import os
import pathlib

# Set required env vars before any quasarr module is imported.
os.environ.setdefault("API_KEY", "test_dummy_key")

import pytest

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "zt"


def load_fixture(name: str) -> str:
    """Load an HTML fixture file by name (without extension)."""
    path = FIXTURES_DIR / f"{name}.html"
    return path.read_text(encoding="utf-8")


class _FakeConfig:
    """Minimal stand-in for Config('Hostnames')."""

    def __init__(self):
        self._data = {}

    def get(self, key):
        return self._data.get(key)

    def save(self, key, value):
        self._data[key] = value


class MockSharedState:
    """Lightweight mock that satisfies the shared_state interface used by zt.py.

    Provides:
      - values dict with 'internal_address', 'user_agent', 'config'
      - convert_to_mb, is_valid_release, normalize_localized_season_episode_tags,
        normalize_magazine_title, is_imdb_id  (delegating to real implementations)
    """

    def __init__(self, zt_hostname="www.zone-telechargement.test"):
        self._config = _FakeConfig()
        self._config._data["zt"] = zt_hostname

        self.values = {
            "internal_address": "http://localhost:5050",
            "user_agent": "Mozilla/5.0 (test)",
            "config": lambda section: self._config,
        }

    # Delegate to real shared_state functions so the parsing logic is truly tested.
    @staticmethod
    def convert_to_mb(item):
        from quasarr.providers.shared_state import convert_to_mb
        return convert_to_mb(item)

    @staticmethod
    def is_valid_release(title, request_from, search_string, season=None, episode=None):
        from quasarr.providers.shared_state import is_valid_release
        return is_valid_release(title, request_from, search_string, season, episode)

    @staticmethod
    def normalize_localized_season_episode_tags(title):
        from quasarr.providers.shared_state import normalize_localized_season_episode_tags
        return normalize_localized_season_episode_tags(title)

    @staticmethod
    def normalize_magazine_title(title):
        from quasarr.providers.shared_state import normalize_magazine_title
        return normalize_magazine_title(title)

    @staticmethod
    def is_imdb_id(search_string):
        from quasarr.providers.shared_state import is_imdb_id
        return is_imdb_id(search_string)


@pytest.fixture
def shared_state():
    """Return a fresh MockSharedState for each test."""
    return MockSharedState()
