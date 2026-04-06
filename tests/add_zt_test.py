#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Utilitaire pour ajouter un nouveau cas de test de non-régression ZT.

Il récupère les pages de recherche et les pages de détail, extrait le HTML
utile (en supprimant tout ce qui ne sert pas au parser), sauvegarde les
fixtures et crée un manifest.json avec les URLs dl-protect attendues.

Le test runner (test_zt_regression_cases.py) découvre automatiquement les
manifests et exécute les tests.

Exemples
--------
  # Film (Radarr) :
  python tests/add_zt_test.py \\
      --name inception \\
      --search-url "https://www.zone-telechargement.pizza/?p=films&search=inception" \\
      --detail-url "https://www.zone-telechargement.pizza/?p=films&id=12345-inception" \\
      --request-from Radarr \\
      --search-string "Inception"

  # Série (Sonarr) avec saison/épisode :
  python tests/add_zt_test.py \\
      --name breaking_bad_s1e3 \\
      --search-url "https://www.zone-telechargement.pizza/?p=series&search=breaking+bad" \\
      --detail-url "https://www.zone-telechargement.pizza/?p=series&id=78001-breaking-bad-s1" \\
      --request-from Sonarr \\
      --search-string "Breaking Bad" \\
      --season 1 --episode 3

  # Plusieurs pages de détail :
  python tests/add_zt_test.py \\
      --name multi_detail \\
      --search-url "https://www.zone-telechargement.pizza/?p=films&search=test" \\
      --detail-url "https://.../?p=films&id=111-film-a" \\
      --detail-url "https://.../?p=films&id=222-film-b" \\
      --request-from Radarr \\
      --search-string "Test"
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

# ── Chemins ──────────────────────────────────────────────────────────────────
CASES_DIR = Path(__file__).parent / "fixtures" / "zt" / "cases"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# Hébergeurs supportés par le parser (dupliqué depuis zt.py pour rester autonome)
SUPPORTED_MIRRORS = {"rapidgator", "1fichier", "turbobit", "uploady", "dailyuploads"}
UNSUPPORTED_HOSTERS = {"nitroflare"}
STREAM_TOKENS = {"a1", "b1", "h1"}


# ── Fonctions de nettoyage HTML ──────────────────────────────────────────────

def _strip_search_page(html: str) -> str:
    """Ne garde que les cartes <div class='cover_global'> d'une page de recherche."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.cover_global")
    if not cards:
        return "<!-- Aucune carte trouvée -->\n"
    lines = []
    for card in cards:
        lines.append(card.prettify())
    return "\n".join(lines)


def _strip_detail_page(html: str) -> str:
    """Supprime tout ce qui ne sert pas au parser d'une page de détail.

    Garde : .centersideinn h1, font[color=red], strong (titre original),
    divs textuels (qualité, taille, année, langue), div.postinfo (liens).
    Supprime : script, style, img, svg, noscript, iframe, nav, header, footer,
    link, meta, aside, form (hors postinfo).
    """
    soup = BeautifulSoup(html, "html.parser")

    # Supprimer les balises inutiles
    for tag_name in ["script", "style", "noscript", "iframe", "svg",
                     "img", "nav", "header", "footer", "link", "meta",
                     "aside", "ins", "video", "audio", "source", "picture",
                     "canvas", "object", "embed"]:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Supprimer les attributs inutiles (garder href, color, class)
    for tag in soup.find_all(True):
        allowed = {"href", "color", "class", "rel"}
        attrs_to_remove = [a for a in tag.attrs if a not in allowed]
        for a in attrs_to_remove:
            del tag[a]

    # Supprimer les divs vides (sans texte ni enfants utiles)
    for div in soup.find_all("div"):
        if not div.get_text(strip=True) and not div.find_all(["a", "b", "div"]):
            div.decompose()

    return soup.prettify()


# ── Extraction des URLs dl-protect ───────────────────────────────────────────

def _normalize_hoster(host: str) -> str:
    """Version simplifiée de _normalize_hoster_name pour l'utilitaire."""
    host = (host or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "").replace(".", "")
    if host.startswith("rapidgator"):
        return "rapidgator"
    if host.startswith("1fichier"):
        return "1fichier"
    if "nitro" in host:
        return "nitroflare"
    if "turbobit" in host:
        return "turbobit"
    if "uploady" in host:
        return "uploady"
    if "dailyupload" in host:
        return "dailyuploads"
    return host


def _extract_expected_dl_protect_urls(html: str) -> list[str]:
    """Extrait les URLs dl-protect qu'on s'attend à retrouver dans le résultat du parser.

    Reproduit la logique de _collect_download_entries : parcourt les div.postinfo,
    identifie l'hébergeur courant, filtre streaming + hosts non supportés,
    et retourne les URLs restantes.
    """
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    current_host = None
    skip = False

    for block in soup.select("div.postinfo"):
        for bold in block.find_all("b"):
            host_div = bold.find("div")
            if host_div:
                h = _normalize_hoster(host_div.get_text(strip=True))
                if not h:
                    current_host = None
                    skip = False
                    continue
                if h in UNSUPPORTED_HOSTERS or h not in SUPPORTED_MIRRORS:
                    current_host = None
                    skip = True
                    continue
                current_host = h
                skip = False
                continue

            anchor = bold.find("a", href=True)
            if not anchor or skip:
                continue

            href = anchor["href"]
            parsed = urlparse(href)
            if parsed.scheme.lower() not in {"http", "https", ""}:
                continue

            # Vérifier les tokens de streaming
            rl_tokens = set()
            for values in parse_qs(parsed.query).values():
                for v in values:
                    rl_tokens.add(v.lower())
            if rl_tokens & STREAM_TOKENS:
                continue

            # Vérifier l'hébergeur
            host_for_check = current_host
            if not host_for_check:
                netloc = parsed.netloc.lower()
                host_for_check = _normalize_hoster(
                    netloc.split(".")[-2] if "." in netloc else netloc
                )
            if not host_for_check or host_for_check in UNSUPPORTED_HOSTERS or host_for_check not in SUPPORTED_MIRRORS:
                continue

            urls.append(href)

    return urls


# ── Fetch avec retry ────────────────────────────────────────────────────────

def _fetch(url: str, max_retries: int = 3) -> requests.Response:
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  ⚠ Erreur ({exc}), nouvelle tentative dans {wait}s...")
                time.sleep(wait)
            else:
                raise


# ── Pagination de recherche ──────────────────────────────────────────────────

def _fetch_search_pages(base_search_url: str, max_pages: int = 10) -> list[str]:
    """Fetch toutes les pages de recherche (tant qu'il y a des cartes)."""
    pages_html = []
    parsed = urlparse(base_search_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)

    for page_num in range(1, max_pages + 1):
        qs_copy = dict(qs)
        qs_copy["page"] = [str(page_num)]
        page_url = urlunparse(parsed._replace(query=urlencode(qs_copy, doseq=True)))

        print(f"  Recherche page {page_num}: {page_url}")
        try:
            resp = _fetch(page_url)
        except requests.RequestException as exc:
            print(f"  ✗ Impossible de charger la page {page_num}: {exc}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("div.cover_global")
        if not cards:
            print(f"  → Page {page_num} : aucune carte, arrêt de la pagination.")
            break

        stripped = _strip_search_page(resp.text)
        pages_html.append(stripped)
        print(f"  → Page {page_num} : {len(cards)} carte(s)")

    return pages_html


# ── Point d'entrée ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Ajouter un cas de test de non-régression ZT.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--name", required=True,
                        help="Nom du cas de test (utilisé pour le dossier)")
    parser.add_argument("--search-url", required=True,
                        help="URL de recherche ZT (page 1, sans &page=)")
    parser.add_argument("--detail-url", action="append", default=[],
                        help="URL(s) de page(s) de détail à inclure (répétable)")
    parser.add_argument("--request-from", required=True,
                        choices=["Radarr", "Sonarr", "LazyLibrarian"],
                        help="Type de requête simulée")
    parser.add_argument("--search-string", required=True,
                        help="Chaîne de recherche utilisée pour is_valid_release")
    parser.add_argument("--season", type=int, default=None,
                        help="Numéro de saison (Sonarr)")
    parser.add_argument("--episode", type=int, default=None,
                        help="Numéro d'épisode (Sonarr)")
    parser.add_argument("--mirror", default=None,
                        help="Filtrer sur un hébergeur spécifique")
    parser.add_argument("--max-pages", type=int, default=10,
                        help="Nombre max de pages de recherche (défaut: 10)")

    args = parser.parse_args()

    case_dir = CASES_DIR / args.name
    if case_dir.exists():
        print(f"✗ Le dossier '{case_dir}' existe déjà. Choisissez un autre --name.")
        sys.exit(1)

    case_dir.mkdir(parents=True)
    print(f"Dossier créé : {case_dir}\n")

    # ── 1. Pages de recherche ────────────────────────────────────────────
    print("═══ Récupération des pages de recherche ═══")
    search_pages = _fetch_search_pages(args.search_url, max_pages=args.max_pages)
    if not search_pages:
        print("✗ Aucune page de recherche récupérée.")
        sys.exit(1)

    search_filenames = []
    for i, html in enumerate(search_pages, start=1):
        fname = f"search_p{i}.html"
        (case_dir / fname).write_text(html, encoding="utf-8")
        search_filenames.append(fname)
        print(f"  Sauvegardé : {fname}")

    # ── 2. Pages de détail ───────────────────────────────────────────────
    detail_pages = {}
    if args.detail_url:
        print(f"\n═══ Récupération des pages de détail ({len(args.detail_url)}) ═══")
        for detail_url in args.detail_url:
            print(f"\n  URL : {detail_url}")
            try:
                resp = _fetch(detail_url)
            except requests.RequestException as exc:
                print(f"  ✗ Impossible de charger : {exc}")
                continue

            # Trouver l'identifiant dans l'URL (ex: "id=12345" ou "12345-titre")
            detail_parsed = urlparse(detail_url)
            detail_qs = parse_qs(detail_parsed.query)

            # Construire une clé d'identification pour le routeur de test
            # On cherche le pattern "id=XXXXX" ou le dernier segment du path
            id_match = re.search(r"id=(\d+)", detail_url)
            if id_match:
                route_key = f"id={id_match.group(1)}"
            else:
                # Fallback : dernier segment du path
                path_parts = detail_parsed.path.rstrip("/").split("/")
                route_key = path_parts[-1] if path_parts else detail_url

            fname = f"detail_{route_key.replace('id=', '')}.html"

            stripped_html = _strip_detail_page(resp.text)
            (case_dir / fname).write_text(stripped_html, encoding="utf-8")

            expected_urls = _extract_expected_dl_protect_urls(stripped_html)
            print(f"  Sauvegardé : {fname}")
            print(f"  URLs dl-protect trouvées : {len(expected_urls)}")
            for url in expected_urls:
                print(f"    → {url}")

            detail_pages[route_key] = {
                "fixture": fname,
                "expected_dl_protect_urls": expected_urls,
            }

    # ── 3. Manifest ──────────────────────────────────────────────────────
    manifest = {
        "name": args.name,
        "search_url": args.search_url,
        "request_from": args.request_from,
        "search_string": args.search_string,
        "season": args.season,
        "episode": args.episode,
        "mirror": args.mirror,
        "search_pages": search_filenames,
        "detail_pages": detail_pages,
    }

    manifest_path = case_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n═══ Manifest sauvegardé : {manifest_path} ═══")

    # ── 4. Résumé ────────────────────────────────────────────────────────
    total_urls = sum(len(d["expected_dl_protect_urls"]) for d in detail_pages.values())
    print(f"\n✓ Cas de test '{args.name}' créé avec succès !")
    print(f"  {len(search_filenames)} page(s) de recherche")
    print(f"  {len(detail_pages)} page(s) de détail")
    print(f"  {total_urls} URL(s) dl-protect attendue(s)")
    print(f"\nLes tests seront automatiquement découverts par :")
    print(f"  python -m pytest tests/test_zt_regression_cases.py -v")


if __name__ == "__main__":
    main()
