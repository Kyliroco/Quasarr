# -*- coding: utf-8 -*-
"""Test runner automatique pour les cas de régression ZT.

Découvre tous les fichiers manifest.json dans tests/fixtures/zt/cases/*/,
charge les fixtures HTML associées, exécute _parse_results et vérifie que
les URLs dl-protect attendues sont bien présentes dans les résultats.

Règle de validation :
  - Chaque URL dl-protect listée dans le manifest DOIT apparaître dans
    au moins un résultat (champ `source`, fragment ignoré).
  - Le parser peut retourner PLUS d'URLs que prévu (pas un échec).
  - Si une URL attendue est absente → échec du test.

Pour ajouter un nouveau cas de test :
  python tests/add_zt_test.py --name mon_test --search-url ... --detail-url ...
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.parse import urlparse, urlunparse

import pytest
from bs4 import BeautifulSoup

from quasarr.search.sources.zt import _parse_results
from tests.conftest import MockSharedState

CASES_DIR = Path(__file__).parent / "fixtures" / "zt" / "cases"
HEADERS = {"User-Agent": "Mozilla/5.0 (test)"}


# ── Découverte des cas de test ───────────────────────────────────────────────

def _discover_cases() -> list[tuple[str, dict]]:
    """Trouve tous les manifest.json et retourne (case_name, manifest_dict)."""
    cases = []
    if not CASES_DIR.exists():
        return cases
    for manifest_path in sorted(CASES_DIR.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        case_dir = manifest_path.parent
        cases.append((manifest["name"], manifest, case_dir))
    return cases


def _load_case_fixture(case_dir: Path, filename: str) -> str:
    return (case_dir / filename).read_text(encoding="utf-8")


def _make_mock_response(html: str, url: str):
    resp = MagicMock()
    resp.text = html
    resp.url = url
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    return resp


def _build_detail_router(case_dir: Path, detail_pages: dict):
    """Construit un side_effect pour requests.get à partir du manifest."""
    def router(url, **kwargs):
        for route_key, info in detail_pages.items():
            if route_key in url:
                html = _load_case_fixture(case_dir, info["fixture"])
                return _make_mock_response(html, url)
        return _make_mock_response("<html></html>", url)
    return router


def _strip_fragment(url: str) -> str:
    """Retire le fragment (#...) d'une URL."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def _run_case(manifest: dict, case_dir: Path) -> tuple[list[dict], set[str]]:
    """Exécute _parse_results pour un cas de test et retourne (releases, found_urls)."""
    # Extraire le hostname depuis l'URL de recherche
    search_parsed = urlparse(manifest["search_url"])
    zt_host = search_parsed.netloc

    ss = MockSharedState(zt_hostname=zt_host)
    base_url = f"{search_parsed.scheme}://{zt_host}/"

    # Combiner toutes les pages de recherche
    combined_html = ""
    for search_file in manifest["search_pages"]:
        combined_html += _load_case_fixture(case_dir, search_file) + "\n"

    soup = BeautifulSoup(combined_html, "html.parser")

    with patch("quasarr.search.sources.zt.requests.get",
               side_effect=_build_detail_router(case_dir, manifest["detail_pages"])):
        releases = _parse_results(
            ss, soup, base_url,
            request_from=manifest["request_from"],
            mirror=manifest.get("mirror"),
            headers=HEADERS,
            current_host=zt_host,
            search_string=manifest["search_string"],
            season=manifest.get("season"),
            episode=manifest.get("episode"),
            imdb_id=None,
        )

    # Collecter toutes les URLs source (dl-protect) retournées
    found_urls = set()
    for r in releases:
        source = r.get("details", {}).get("source", "")
        if source:
            found_urls.add(_strip_fragment(source))

    return releases, found_urls


# ── Tests paramétrés ─────────────────────────────────────────────────────────

_CASES = _discover_cases()


@pytest.mark.skipif(not _CASES, reason="Aucun cas de régression dans tests/fixtures/zt/cases/")
class TestZtRegressionCases:
    """Tests de non-régression générés automatiquement depuis les manifests."""

    @pytest.mark.parametrize(
        "case_name,manifest,case_dir",
        _CASES,
        ids=[c[0] for c in _CASES],
    )
    def test_expected_dl_protect_urls_present(self, case_name, manifest, case_dir):
        """Vérifie que toutes les URLs dl-protect attendues sont trouvées par le parser.

        Le test échoue si une URL attendue est absente.
        Le test passe si le parser retourne des URLs en plus (faux positifs acceptés).
        """
        releases, found_urls = _run_case(manifest, case_dir)

        # Collecter toutes les URLs attendues de toutes les pages de détail
        all_expected = []
        for route_key, info in manifest["detail_pages"].items():
            for expected_url in info["expected_dl_protect_urls"]:
                all_expected.append((route_key, _strip_fragment(expected_url)))

        if not all_expected:
            pytest.skip(f"Cas '{case_name}' : aucune URL attendue dans le manifest")

        missing = []
        for route_key, expected_url in all_expected:
            if expected_url not in found_urls:
                missing.append(f"  [{route_key}] {expected_url}")

        if missing:
            found_list = "\n".join(f"  {u}" for u in sorted(found_urls)) or "  (aucune)"
            missing_list = "\n".join(missing)
            pytest.fail(
                f"Cas '{case_name}' : {len(missing)} URL(s) dl-protect manquante(s).\n\n"
                f"URLs attendues manquantes :\n{missing_list}\n\n"
                f"URLs trouvées par le parser :\n{found_list}\n\n"
                f"Total releases : {len(releases)}"
            )

    @pytest.mark.parametrize(
        "case_name,manifest,case_dir",
        _CASES,
        ids=[c[0] for c in _CASES],
    )
    def test_releases_are_well_formed(self, case_name, manifest, case_dir):
        """Vérifie la structure des releases (type, clés requises)."""
        releases, _ = _run_case(manifest, case_dir)

        required_keys = {"title", "hostname", "link", "mirror", "size", "date", "source"}
        for r in releases:
            assert r["type"] == "protected", f"Release non 'protected' : {r}"
            details = r["details"]
            missing_keys = required_keys - set(details.keys())
            assert not missing_keys, (
                f"Clés manquantes {missing_keys} dans : {details.get('title', '?')}"
            )
            assert details["hostname"] == "zt"

    @pytest.mark.parametrize(
        "case_name,manifest,case_dir",
        _CASES,
        ids=[c[0] for c in _CASES],
    )
    def test_generates_at_least_one_release(self, case_name, manifest, case_dir):
        """Vérifie qu'au moins un release est généré (sinon le parsing est cassé)."""
        releases, _ = _run_case(manifest, case_dir)

        # Seulement si on attend des URLs (sinon le cas peut légitimement être vide)
        has_expected = any(
            info["expected_dl_protect_urls"]
            for info in manifest["detail_pages"].values()
        )
        if has_expected:
            assert len(releases) > 0, (
                f"Cas '{case_name}' : aucun release généré alors que des URLs "
                f"dl-protect sont attendues"
            )
