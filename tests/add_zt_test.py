#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Utilitaire pour ajouter un nouveau cas de test de non-regression ZT.

Il recupere les pages de recherche et les pages de detail, extrait le HTML
utile (en supprimant tout ce qui ne sert pas au parser), sauvegarde les
fixtures et cree un manifest.json avec les URLs dl-protect attendues.

Le test runner (test_zt_regression_cases.py) decouvre automatiquement les
manifests et execute les tests.

----------------------------------------------------------------------
INTERFACE SIMPLIFIEE (recommandee)
----------------------------------------------------------------------

  # 1. Mettre a jour le domaine ZT (a faire quand le domaine change) :
  python tests/add_zt_test.py --set-domain www.zone-telechargement.pizza

  # 2. Afficher le domaine actuel :
  python tests/add_zt_test.py --show-domain

  # 3. Ajouter un cas de film (Radarr) :
  python tests/add_zt_test.py \\
      --name inception \\
      --category films \\
      --search "inception" \\
      --detail-id 12345 \\
      --request-from Radarr

  # 4. Ajouter un cas de serie (Sonarr) :
  python tests/add_zt_test.py \\
      --name breaking_bad_s1e3 \\
      --category series \\
      --search "breaking bad" \\
      --detail-id 78001 \\
      --request-from Sonarr \\
      --season 1 --episode 3

  # 5. Plusieurs pages de detail :
  python tests/add_zt_test.py \\
      --name multi_detail \\
      --category films \\
      --search "matrix" \\
      --detail-id 111 --detail-id 222 \\
      --request-from Radarr

----------------------------------------------------------------------
INTERFACE AVANCEE (URLs completes, ex. pour un domaine ponctuel different)
----------------------------------------------------------------------

  python tests/add_zt_test.py \\
      --name inception \\
      --search-url "https://www.zone-telechargement.pizza/?p=films&search=inception" \\
      --detail-url "https://www.zone-telechargement.pizza/?p=films&id=12345-inception" \\
      --request-from Radarr \\
      --search-string "Inception"
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, quote_plus

import requests
from bs4 import BeautifulSoup

# ── Chemins ──────────────────────────────────────────────────────────────────
CASES_DIR = Path(__file__).parent / "fixtures" / "zt" / "cases"
DOMAIN_FILE = Path(__file__).parent / "fixtures" / "zt" / "current_domain.txt"

DEFAULT_DOMAIN = "www.zone-telechargement.test"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

SUPPORTED_MIRRORS = {"rapidgator", "1fichier", "turbobit", "uploady", "dailyuploads"}
UNSUPPORTED_HOSTERS = {"nitroflare"}
STREAM_TOKENS = {"a1", "b1", "h1"}

CATEGORIES = ["films", "series", "ebooks", "logiciels", "jeux"]


# ── Gestion du domaine ───────────────────────────────────────────────────────

def _read_domain() -> str:
    if DOMAIN_FILE.exists():
        domain = DOMAIN_FILE.read_text(encoding="utf-8").strip()
        if domain:
            return domain
    return DEFAULT_DOMAIN


def _save_domain(domain: str):
    domain = domain.strip()
    if domain.startswith(("http://", "https://")):
        from urllib.parse import urlparse as _up
        parsed = _up(domain)
        domain = parsed.netloc or parsed.path
    domain = domain.rstrip("/")
    DOMAIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    DOMAIN_FILE.write_text(domain + "\n", encoding="utf-8")


# ── Construction d'URLs depuis les arguments simplifiés ──────────────────────

def _build_search_url(domain: str, category: str, search: str) -> str:
    return f"https://{domain}/?p={category}&search={quote_plus(search)}"


def _build_detail_url(domain: str, category: str, detail_id: str) -> str:
    return f"https://{domain}/?p={category}&id={detail_id}"


# ── Fonctions de nettoyage HTML ──────────────────────────────────────────────

def _strip_search_page(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.cover_global")
    if not cards:
        return "<!-- Aucune carte trouvée -->\n"
    return "\n".join(card.prettify() for card in cards)


def _strip_detail_page(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag_name in ["script", "style", "noscript", "iframe", "svg",
                     "img", "nav", "header", "footer", "link", "meta",
                     "aside", "ins", "video", "audio", "source", "picture",
                     "canvas", "object", "embed"]:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for tag in soup.find_all(True):
        allowed = {"href", "color", "class", "rel"}
        attrs_to_remove = [a for a in tag.attrs if a not in allowed]
        for a in attrs_to_remove:
            del tag[a]

    for div in soup.find_all("div"):
        if not div.get_text(strip=True) and not div.find_all(["a", "b", "div"]):
            div.decompose()

    return soup.prettify()


# ── Extraction des URLs dl-protect ───────────────────────────────────────────

def _normalize_hoster(host: str) -> str:
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

            rl_tokens = set()
            for values in parse_qs(parsed.query).values():
                for v in values:
                    rl_tokens.add(v.lower())
            if rl_tokens & STREAM_TOKENS:
                continue

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


# ── Fetch avec retry ─────────────────────────────────────────────────────────

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

    # ── Gestion du domaine ───────────────────────────────────────────────
    domain_group = parser.add_argument_group("Domaine ZT")
    domain_group.add_argument(
        "--set-domain", metavar="DOMAIN",
        help="Mettre à jour le domaine ZT par défaut (ex: www.zone-telechargement.pizza) et quitter",
    )
    domain_group.add_argument(
        "--show-domain", action="store_true",
        help="Afficher le domaine ZT actuel et quitter",
    )
    domain_group.add_argument(
        "--domain", default=None,
        help="Utiliser ce domaine pour cette exécution uniquement (sans sauvegarder)",
    )

    # ── Interface simplifiée ─────────────────────────────────────────────
    simple_group = parser.add_argument_group("Interface simplifiée (recommandée)")
    simple_group.add_argument(
        "--category", choices=CATEGORIES,
        help=f"Catégorie ZT : {', '.join(CATEGORIES)}",
    )
    simple_group.add_argument(
        "--search", metavar="QUERY",
        help="Terme de recherche (construit l'URL avec --category et le domaine configuré)",
    )
    simple_group.add_argument(
        "--detail-id", action="append", default=[], dest="detail_ids", metavar="ID",
        help="ID numérique d'une page de détail (répétable, ex: --detail-id 12345)",
    )

    # ── Interface avancée (URLs complètes) ───────────────────────────────
    adv_group = parser.add_argument_group("Interface avancée (URLs complètes)")
    adv_group.add_argument(
        "--search-url",
        help="URL de recherche complète (remplace --search + --category)",
    )
    adv_group.add_argument(
        "--detail-url", action="append", default=[], metavar="URL",
        help="URL de page de détail complète (répétable, remplace --detail-id)",
    )

    # ── Métadonnées du cas ───────────────────────────────────────────────
    meta_group = parser.add_argument_group("Métadonnées du cas de test")
    meta_group.add_argument(
        "--name",
        help="Nom du cas de test (dossier). Auto-généré depuis --search si omis.",
    )
    meta_group.add_argument(
        "--request-from", required=False,
        choices=["Radarr", "Sonarr", "LazyLibrarian"],
        help="Type de requête simulée",
    )
    meta_group.add_argument(
        "--search-string", default=None,
        help="Chaîne passée à is_valid_release. Défaut : valeur de --search",
    )
    meta_group.add_argument("--season", type=int, default=None, help="Numéro de saison (Sonarr)")
    meta_group.add_argument("--episode", type=int, default=None, help="Numéro d'épisode (Sonarr)")
    meta_group.add_argument("--mirror", default=None, help="Filtrer sur un hébergeur spécifique")
    meta_group.add_argument(
        "--max-pages", type=int, default=10,
        help="Nombre max de pages de recherche (défaut: 10)",
    )

    args = parser.parse_args()

    # ── Commandes de gestion du domaine ─────────────────────────────────
    if args.set_domain:
        domain = _save_domain(args.set_domain)
        print(f"OK Domaine ZT mis a jour : {domain}")
        print(f"   (sauvegarde dans {DOMAIN_FILE})")
        sys.exit(0)

    if args.show_domain:
        domain = _read_domain()
        print(f"Domaine ZT actuel : {domain}")
        print(f"  (source : {DOMAIN_FILE})")
        sys.exit(0)

    # ── Résoudre le domaine ──────────────────────────────────────────────
    domain = args.domain or _read_domain()

    # ── Construire l'URL de recherche ────────────────────────────────────
    if args.search_url:
        search_url = args.search_url
        # Extraire la catégorie depuis l'URL si non fournie
        if not args.category:
            qs = parse_qs(urlparse(search_url).query)
            args.category = qs.get("p", [None])[0]
    elif args.search and args.category:
        search_url = _build_search_url(domain, args.category, args.search)
    else:
        parser.error(
            "Fournir soit (--search + --category) soit --search-url.\n"
            f"  Exemple : --category films --search \"inception\"\n"
            f"  Domaine actuel : {domain}  (changer avec --set-domain)"
        )

    # ── Construire les URLs de détail ────────────────────────────────────
    detail_urls = list(args.detail_url)
    for did in args.detail_ids:
        category = args.category or "films"
        detail_urls.append(_build_detail_url(domain, category, did))

    # ── Valider les métadonnées obligatoires ─────────────────────────────
    if not args.request_from:
        parser.error("--request-from est requis (Radarr, Sonarr, ou LazyLibrarian)")

    search_string = args.search_string or args.search
    if not search_string:
        parser.error("--search-string est requis si --search n'est pas fourni")

    # ── Nommer le cas de test ────────────────────────────────────────────
    case_name = args.name
    if not case_name:
        raw = args.search or search_string
        case_name = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
        if args.season is not None:
            case_name += f"_s{args.season:02d}"
        if args.episode is not None:
            case_name += f"e{args.episode:02d}"

    case_dir = CASES_DIR / case_name
    if case_dir.exists():
        print(f"✗ Le dossier '{case_dir}' existe déjà. Choisissez un autre --name.")
        sys.exit(1)

    case_dir.mkdir(parents=True)
    print(f"Dossier créé : {case_dir}")
    print(f"Domaine utilisé : {domain}\n")

    # ── 1. Pages de recherche ────────────────────────────────────────────
    print("═══ Récupération des pages de recherche ═══")
    search_pages = _fetch_search_pages(search_url, max_pages=args.max_pages)
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
    if detail_urls:
        print(f"\n═══ Récupération des pages de détail ({len(detail_urls)}) ═══")
        for detail_url in detail_urls:
            print(f"\n  URL : {detail_url}")
            try:
                resp = _fetch(detail_url)
            except requests.RequestException as exc:
                print(f"  ✗ Impossible de charger : {exc}")
                continue

            id_match = re.search(r"id=(\d+)", detail_url)
            if id_match:
                route_key = f"id={id_match.group(1)}"
            else:
                path_parts = urlparse(detail_url).path.rstrip("/").split("/")
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
        "name": case_name,
        "search_url": search_url,
        "request_from": args.request_from,
        "search_string": search_string,
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
    print(f"\n✓ Cas de test '{case_name}' créé avec succès !")
    print(f"  {len(search_filenames)} page(s) de recherche")
    print(f"  {len(detail_pages)} page(s) de détail")
    print(f"  {total_urls} URL(s) dl-protect attendue(s)")
    print(f"\nLancer les tests :")
    print(f"  python -m pytest tests/test_zt_regression_cases.py -v")


if __name__ == "__main__":
    main()
