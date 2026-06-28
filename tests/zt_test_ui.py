#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interface web locale pour gerer les cas de test de non-regression ZT.

Lancer : python tests/zt_test_ui.py
Puis ouvrir : http://localhost:8765
"""

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, quote_plus

sys.path.insert(0, str(Path(__file__).parent.parent))

import bottle
import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
CASES_DIR = Path(__file__).parent / "fixtures" / "zt" / "cases"
DOMAIN_FILE = Path(__file__).parent / "fixtures" / "zt" / "current_domain.txt"
DEFAULT_DOMAIN = "www.zone-telechargement.test"
CATEGORIES = ["films", "series", "ebooks", "logiciels", "jeux"]
PORT = 8765
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
SUPPORTED_MIRRORS = {"rapidgator", "1fichier", "turbobit", "uploady", "dailyuploads"}
UNSUPPORTED_HOSTERS = {"nitroflare"}
STREAM_TOKENS = {"a1", "b1", "h1"}


# ── Helpers domaine ───────────────────────────────────────────────────────────

def _read_domain():
    if DOMAIN_FILE.exists():
        d = DOMAIN_FILE.read_text(encoding="utf-8").strip()
        if d:
            return d
    return DEFAULT_DOMAIN


def _save_domain(domain):
    domain = domain.strip()
    # Accepte "https://www.site.com/" ou "www.site.com"
    if domain.startswith(("http://", "https://")):
        parsed = urlparse(domain)
        domain = parsed.netloc or parsed.path
    domain = domain.rstrip("/")
    DOMAIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    DOMAIN_FILE.write_text(domain + "\n", encoding="utf-8")
    return domain


# ── Helpers cases ─────────────────────────────────────────────────────────────

def _list_cases():
    cases = []
    if not CASES_DIR.exists():
        return cases
    for manifest_path in sorted(CASES_DIR.glob("*/manifest.json")):
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            qs = parse_qs(urlparse(m.get("search_url", "")).query)
            total_urls = sum(
                len(d.get("expected_dl_protect_urls", []))
                for d in m.get("detail_pages", {}).values()
            )
            cases.append({
                "name": m["name"],
                "category": qs.get("p", ["?"])[0],
                "request_from": m.get("request_from", "?"),
                "search_string": m.get("search_string", ""),
                "season": m.get("season"),
                "episode": m.get("episode"),
                "n_search_pages": len(m.get("search_pages", [])),
                "n_detail_pages": len(m.get("detail_pages", {})),
                "n_expected_urls": total_urls,
            })
        except Exception:
            pass
    return cases


# ── Helpers fetch/parse (repris de add_zt_test.py) ───────────────────────────

def _fetch(url, max_retries=3):
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                raise


def _strip_search_page(html):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.cover_global")
    if not cards:
        return "<!-- Aucune carte trouvee -->\n"
    return "\n".join(card.prettify() for card in cards)


def _strip_detail_page(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag_name in ["script", "style", "noscript", "iframe", "svg", "img", "nav",
                     "header", "footer", "link", "meta", "aside", "ins", "video",
                     "audio", "source", "picture", "canvas", "object", "embed"]:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    for tag in soup.find_all(True):
        allowed = {"href", "color", "class", "rel"}
        for a in [k for k in tag.attrs if k not in allowed]:
            del tag[a]
    for div in soup.find_all("div"):
        if not div.get_text(strip=True) and not div.find_all(["a", "b", "div"]):
            div.decompose()
    return soup.prettify()


def _normalize_hoster(host):
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


def _extract_expected_urls(html):
    soup = BeautifulSoup(html, "html.parser")
    urls, current_host, skip = [], None, False
    for block in soup.select("div.postinfo"):
        for bold in block.find_all("b"):
            host_div = bold.find("div")
            if host_div:
                h = _normalize_hoster(host_div.get_text(strip=True))
                if not h:
                    current_host, skip = None, False
                elif h in UNSUPPORTED_HOSTERS or h not in SUPPORTED_MIRRORS:
                    current_host, skip = None, True
                else:
                    current_host, skip = h, False
                continue
            anchor = bold.find("a", href=True)
            if not anchor or skip:
                continue
            href = anchor["href"]
            parsed = urlparse(href)
            if parsed.scheme.lower() not in {"http", "https", ""}:
                continue
            rl_tokens = {v.lower() for vals in parse_qs(parsed.query).values() for v in vals}
            if rl_tokens & STREAM_TOKENS:
                continue
            hc = current_host or _normalize_hoster(
                parsed.netloc.lower().split(".")[-2] if "." in parsed.netloc else parsed.netloc
            )
            if not hc or hc in UNSUPPORTED_HOSTERS or hc not in SUPPORTED_MIRRORS:
                continue
            urls.append(href)
    return urls


def _fetch_search_pages(search_url, max_pages=10):
    pages, parsed = [], urlparse(search_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    for page_num in range(1, max_pages + 1):
        qs_copy = dict(qs)
        qs_copy["page"] = [str(page_num)]
        url = urlunparse(parsed._replace(query=urlencode(qs_copy, doseq=True)))
        try:
            resp = _fetch(url)
        except requests.RequestException:
            break
        soup = BeautifulSoup(resp.text, "html.parser")
        if not soup.select("div.cover_global"):
            break
        pages.append(_strip_search_page(resp.text))
    return pages


def _run_add_case(name, search_url, detail_urls, request_from, search_string,
                  season, episode, mirror, max_pages=10):
    """Execute le cas d'ajout et retourne (success, log, case_name)."""
    log = []

    # Nommer le cas
    if not name:
        raw = search_string or "case"
        name = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
        if season is not None:
            name += f"_s{season:02d}"
        if episode is not None:
            name += f"e{episode:02d}"

    case_dir = CASES_DIR / name
    if case_dir.exists():
        return False, [f"Le dossier '{name}' existe deja. Choisissez un autre nom."], None

    case_dir.mkdir(parents=True)
    log.append(f"Dossier cree : {case_dir.name}")

    # Pages de recherche
    log.append("--- Pages de recherche ---")
    search_pages_html = _fetch_search_pages(search_url, max_pages=max_pages)
    if not search_pages_html:
        case_dir.rmdir()
        return False, log + ["Aucune page de recherche recuperee."], None

    search_filenames = []
    for i, html in enumerate(search_pages_html, 1):
        fname = f"search_p{i}.html"
        (case_dir / fname).write_text(html, encoding="utf-8")
        search_filenames.append(fname)
        cards = html.count("cover_global")
        log.append(f"  Page {i} : {cards} carte(s) -> {fname}")

    # Pages de detail
    detail_pages = {}
    if detail_urls:
        log.append("--- Pages de detail ---")
        for detail_url in detail_urls:
            log.append(f"  {detail_url}")
            try:
                resp = _fetch(detail_url)
            except requests.RequestException as exc:
                log.append(f"  ERREUR : {exc}")
                continue

            id_match = re.search(r"id=(\d+)", detail_url)
            if id_match:
                route_key = f"id={id_match.group(1)}"
            else:
                path_parts = urlparse(detail_url).path.rstrip("/").split("/")
                route_key = path_parts[-1] if path_parts else detail_url

            fname = f"detail_{route_key.replace('id=', '')}.html"
            stripped = _strip_detail_page(resp.text)
            (case_dir / fname).write_text(stripped, encoding="utf-8")
            expected = _extract_expected_urls(stripped)
            log.append(f"  -> {fname} : {len(expected)} URL(s) dl-protect")
            for u in expected:
                log.append(f"     {u}")
            detail_pages[route_key] = {"fixture": fname, "expected_dl_protect_urls": expected}

    # Manifest
    manifest = {
        "name": name,
        "search_url": search_url,
        "request_from": request_from,
        "search_string": search_string,
        "season": season,
        "episode": episode,
        "mirror": mirror,
        "search_pages": search_filenames,
        "detail_pages": detail_pages,
    }
    (case_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    total = sum(len(d["expected_dl_protect_urls"]) for d in detail_pages.values())
    log.append(f"--- Termine ---")
    log.append(f"  {len(search_filenames)} page(s) de recherche")
    log.append(f"  {len(detail_pages)} page(s) de detail")
    log.append(f"  {total} URL(s) dl-protect attendue(s)")

    return True, log, name


# ── Bottle app ────────────────────────────────────────────────────────────────

app = bottle.Bottle()


def _html(title, body, back_link=""):
    domain = _read_domain()
    back_html = f'<a href="{back_link}" class="back">← Retour</a>' if back_link else ""
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} – ZT Tests</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#f0f2f5;color:#1a1a2e;min-height:100vh}}
a{{color:#2563eb;text-decoration:none}}
a:hover{{text-decoration:underline}}

/* Header */
.hdr{{background:#1e40af;color:#fff;padding:14px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
.hdr-title{{font-size:1.1rem;font-weight:700;letter-spacing:.3px}}
.domain-pill{{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.25);
  border-radius:20px;padding:4px 12px;font-family:monospace;font-size:.82rem;display:flex;align-items:center;gap:8px}}
.domain-pill input[type=text]{{background:transparent;border:none;color:#fff;font-family:monospace;
  font-size:.82rem;outline:none;width:220px}}
.domain-pill button{{background:rgba(255,255,255,.2);border:none;color:#fff;padding:2px 8px;
  border-radius:10px;cursor:pointer;font-size:.75rem}}
.domain-pill button:hover{{background:rgba(255,255,255,.35)}}
.hdr-spacer{{flex:1}}
.btn{{display:inline-block;padding:8px 18px;border-radius:6px;font-size:.9rem;font-weight:500;
  cursor:pointer;border:none;transition:opacity .15s}}
.btn-primary{{background:#2563eb;color:#fff}}
.btn-primary:hover{{opacity:.85}}
.btn-danger{{background:#dc2626;color:#fff}}
.btn-danger:hover{{opacity:.85}}
.btn-sm{{padding:4px 10px;font-size:.78rem}}

/* Content */
.container{{max-width:980px;margin:0 auto;padding:28px 20px}}
h2{{font-size:1rem;font-weight:600;color:#374151;margin-bottom:16px;text-transform:uppercase;
  letter-spacing:.5px}}

/* Cases grid */
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:14px;margin-bottom:28px}}
.card{{background:#fff;border-radius:10px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.08);
  border:1px solid #e5e7eb;position:relative}}
.card-name{{font-weight:600;font-size:.95rem;margin-bottom:8px;padding-right:32px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.card-meta{{font-size:.78rem;color:#6b7280;display:flex;flex-direction:column;gap:3px}}
.badge{{display:inline-block;padding:2px 7px;border-radius:10px;font-size:.72rem;
  font-weight:600;margin-right:4px}}
.badge-films{{background:#dbeafe;color:#1d4ed8}}
.badge-series{{background:#dcfce7;color:#15803d}}
.badge-ebooks{{background:#fef9c3;color:#854d0e}}
.badge-logiciels{{background:#f3e8ff;color:#7e22ce}}
.badge-jeux{{background:#fee2e2;color:#b91c1c}}
.badge-other{{background:#f3f4f6;color:#374151}}
.badge-radarr{{background:#ede9fe;color:#5b21b6}}
.badge-sonarr{{background:#ecfeff;color:#0e7490}}
.card-stats{{margin-top:10px;display:flex;gap:10px;font-size:.78rem;color:#6b7280}}
.card-stat{{display:flex;flex-direction:column;align-items:center;background:#f9fafb;
  border-radius:6px;padding:4px 8px;gap:1px}}
.card-stat span:first-child{{font-size:1rem;font-weight:700;color:#111827}}
.card-delete{{position:absolute;top:10px;right:10px;background:none;border:none;
  color:#d1d5db;cursor:pointer;font-size:1rem;padding:2px 5px;border-radius:4px}}
.card-delete:hover{{color:#dc2626;background:#fee2e2}}

/* Add card */
.card-add{{background:#f8faff;border:2px dashed #93c5fd;border-radius:10px;padding:16px;
  display:flex;align-items:center;justify-content:center;cursor:pointer;min-height:120px;
  transition:border-color .15s,background .15s}}
.card-add:hover{{border-color:#2563eb;background:#eff6ff}}
.card-add-inner{{text-align:center;color:#3b82f6}}
.card-add-inner .plus{{font-size:2rem;font-weight:300;line-height:1}}
.card-add-inner .lbl{{font-size:.82rem;margin-top:4px}}

/* Form */
.form-card{{background:#fff;border-radius:10px;padding:24px;box-shadow:0 1px 4px rgba(0,0,0,.08);
  border:1px solid #e5e7eb;max-width:620px}}
.form-row{{margin-bottom:16px}}
.form-row label{{display:block;font-size:.83rem;font-weight:600;color:#374151;margin-bottom:5px}}
.form-row input[type=text],.form-row select{{width:100%;padding:8px 11px;border:1px solid #d1d5db;
  border-radius:6px;font-size:.9rem;background:#fff;color:#111;outline:none;transition:border .15s}}
.form-row input:focus,.form-row select:focus{{border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.1)}}
.form-row .hint{{font-size:.75rem;color:#9ca3af;margin-top:3px}}
.form-inline{{display:flex;gap:10px}}
.form-inline .form-row{{flex:1;margin-bottom:0}}

/* Detail IDs */
.detail-ids{{display:flex;flex-direction:column;gap:6px}}
.detail-id-row{{display:flex;gap:6px;align-items:center}}
.detail-id-row input{{flex:1}}
.detail-id-row button{{background:none;border:1px solid #e5e7eb;border-radius:6px;
  padding:6px 10px;cursor:pointer;color:#6b7280;font-size:1rem}}
.detail-id-row button:hover{{border-color:#dc2626;color:#dc2626;background:#fee2e2}}
.btn-add-id{{background:none;border:1px dashed #93c5fd;border-radius:6px;padding:7px 14px;
  cursor:pointer;color:#3b82f6;font-size:.82rem;width:100%;margin-top:4px}}
.btn-add-id:hover{{background:#eff6ff;border-color:#2563eb}}

/* Result */
.result-card{{background:#fff;border-radius:10px;padding:20px;
  box-shadow:0 1px 4px rgba(0,0,0,.08);border:1px solid #e5e7eb;max-width:680px}}
.result-ok{{border-left:4px solid #16a34a}}
.result-err{{border-left:4px solid #dc2626}}
.result-title{{font-size:1.05rem;font-weight:600;margin-bottom:14px}}
.result-title.ok{{color:#15803d}}
.result-title.err{{color:#b91c1c}}
.log{{background:#0f172a;color:#e2e8f0;font-family:monospace;font-size:.8rem;border-radius:6px;
  padding:14px 16px;white-space:pre;overflow-x:auto;max-height:360px;overflow-y:auto;line-height:1.6}}
.log .log-ok{{color:#4ade80}}
.log .log-err{{color:#f87171}}
.log .log-url{{color:#93c5fd}}
.log .log-section{{color:#fbbf24}}

/* Loading overlay */
#loading{{display:none;position:fixed;inset:0;background:rgba(15,23,42,.6);z-index:100;
  align-items:center;justify-content:center;flex-direction:column;gap:16px;color:#fff}}
.spinner{{width:40px;height:40px;border:4px solid rgba(255,255,255,.2);
  border-top-color:#fff;border-radius:50%;animation:spin .8s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
#loading p{{font-size:.95rem;opacity:.9}}

.back{{display:inline-block;margin-bottom:18px;font-size:.85rem;color:#6b7280}}
.back:hover{{color:#1e40af}}
.empty-state{{text-align:center;padding:40px;color:#9ca3af;font-size:.9rem}}
</style>
</head>
<body>
<header class="hdr">
  <span class="hdr-title">ZT Tests</span>
  <form class="domain-pill" action="/domain" method="post" style="display:flex;align-items:center;gap:8px">
    <span style="opacity:.7;font-size:.75rem">Domaine :</span>
    <input type="text" name="domain" value="{domain}" placeholder="www.zone-telechargement.xyz">
    <button type="submit">Mettre a jour</button>
  </form>
  <div class="hdr-spacer"></div>
  <a href="/add" class="btn btn-primary">+ Ajouter un cas</a>
</header>
<div id="loading">
  <div class="spinner"></div>
  <p>Recuperation des pages en cours... (peut prendre 15-30s)</p>
</div>
<div class="container">
  {back_html}
  {body}
</div>
<script>
document.querySelectorAll('form.submit-loading').forEach(f => {{
  f.addEventListener('submit', () => {{
    document.getElementById('loading').style.display = 'flex';
  }});
}});
</script>
</body>
</html>"""


@app.route("/")
def index():
    cases = _list_cases()
    msg = bottle.request.query.get("msg", "")
    banner = ""
    if msg:
        banner = f'<div style="background:#dcfce7;border:1px solid #86efac;color:#15803d;padding:10px 14px;border-radius:6px;margin-bottom:16px;font-size:.88rem">Domaine mis a jour : <strong>{msg}</strong></div>'

    if not cases:
        cards_html = '<div class="empty-state">Aucun cas de test. <a href="/add">Ajouter le premier</a>.</div>'
    else:
        cards_html = ""
        for c in cases:
            cat = c["category"]
            badge_class = f"badge-{cat}" if cat in CATEGORIES else "badge-other"
            rf = c["request_from"]
            rf_class = "badge-radarr" if rf == "Radarr" else "badge-sonarr" if rf == "Sonarr" else "badge-other"
            ep_info = ""
            if c["season"] is not None:
                ep_info = f"S{c['season']:02d}"
                if c["episode"] is not None:
                    ep_info += f"E{c['episode']:02d}"

            cards_html += f"""
<div class="card">
  <div class="card-name" title="{c['name']}">{c['name']}</div>
  <div class="card-meta">
    <div>
      <span class="badge {badge_class}">{cat}</span>
      <span class="badge {rf_class}">{rf}</span>
      {"<span class='badge badge-other'>" + ep_info + "</span>" if ep_info else ""}
    </div>
    <div style="margin-top:4px;color:#374151;font-size:.82rem">{c['search_string']}</div>
  </div>
  <div class="card-stats">
    <div class="card-stat">
      <span>{c['n_search_pages']}</span>
      <span>page(s)</span>
    </div>
    <div class="card-stat">
      <span>{c['n_detail_pages']}</span>
      <span>detail(s)</span>
    </div>
    <div class="card-stat">
      <span>{c['n_expected_urls']}</span>
      <span>URL(s)</span>
    </div>
  </div>
  <form action="/case/{c['name']}/delete" method="post" style="display:inline"
        onsubmit="return confirm('Supprimer ce cas ?')">
    <button class="card-delete" title="Supprimer">&#10005;</button>
  </form>
</div>"""

    # Add card
    cards_html += """
<a href="/add" class="card-add" style="text-decoration:none">
  <div class="card-add-inner">
    <div class="plus">+</div>
    <div class="lbl">Ajouter un cas</div>
  </div>
</a>"""

    count = len(cases)
    body = f"""
{banner}<h2>{count} cas de test</h2>
<div class="grid">{cards_html}</div>
"""
    return _html("Accueil", body)


@app.route("/domain", method="POST")
def set_domain():
    domain = bottle.request.forms.get("domain", "").strip()
    if domain:
        try:
            saved = _save_domain(domain)
        except Exception as exc:
            return _html("Erreur domaine", f'<p style="color:red">Erreur : {exc}</p><a href="/">Retour</a>')
        bottle.redirect(f"/?msg={quote_plus(saved)}")
    bottle.redirect("/")


@app.route("/add")
def add_form():
    domain = _read_domain()

    cat_options = "".join(
        f'<option value="{c}"{"selected" if c == "films" else ""}>{c}</option>'
        for c in CATEGORIES
    )
    rf_options = "".join(
        f'<option value="{r}"{"selected" if r == "Radarr" else ""}>{r}</option>'
        for r in ["Radarr", "Sonarr", "LazyLibrarian"]
    )

    body = f"""
<a href="/" class="back">← Retour</a>
<h2>Nouveau cas de test</h2>
<div class="form-card">
  <form action="/add" method="post" class="submit-loading">

    <div class="form-row">
      <label>Nom du cas (optionnel)</label>
      <input type="text" name="name" placeholder="Auto-genere depuis la recherche">
    </div>

    <div class="form-inline">
      <div class="form-row">
        <label>Categorie</label>
        <select name="category">{cat_options}</select>
      </div>
      <div class="form-row">
        <label>Depuis</label>
        <select name="request_from">{rf_options}</select>
      </div>
    </div>

    <div class="form-row">
      <label>Recherche</label>
      <input type="text" name="search" placeholder="ex: inception" required>
      <div class="hint">Domaine actuel : {domain}</div>
    </div>

    <div class="form-row">
      <label>IDs des pages de detail</label>
      <div class="detail-ids" id="detail-ids">
        <div class="detail-id-row">
          <input type="text" name="detail_id" placeholder="ex: 12345">
          <button type="button" onclick="removeRow(this)">&#10005;</button>
        </div>
      </div>
      <button type="button" class="btn-add-id" onclick="addRow()">+ Ajouter un ID</button>
      <div class="hint">
        Trouver l'ID dans l'URL : .../?p=films&amp;id=<strong>12345</strong>-titre
      </div>
    </div>

    <div class="form-inline">
      <div class="form-row">
        <label>Saison (optionnel)</label>
        <input type="text" name="season" placeholder="ex: 1">
      </div>
      <div class="form-row">
        <label>Episode (optionnel)</label>
        <input type="text" name="episode" placeholder="ex: 3">
      </div>
    </div>

    <div style="margin-top:20px">
      <button type="submit" class="btn btn-primary">Creer le cas de test</button>
    </div>
  </form>
</div>

<script>
function addRow() {{
  const container = document.getElementById('detail-ids');
  const row = document.createElement('div');
  row.className = 'detail-id-row';
  row.innerHTML = '<input type="text" name="detail_id" placeholder="ex: 12345">'
                + '<button type="button" onclick="removeRow(this)">&#10005;</button>';
  container.appendChild(row);
  row.querySelector('input').focus();
}}
function removeRow(btn) {{
  const rows = document.querySelectorAll('.detail-id-row');
  if (rows.length > 1) btn.parentElement.remove();
  else btn.previousElementSibling.value = '';
}}
document.querySelectorAll('form.submit-loading').forEach(f => {{
  f.addEventListener('submit', () => {{
    document.getElementById('loading').style.display = 'flex';
  }});
}});
</script>
"""
    return _html("Ajouter", body)


@app.route("/add", method="POST")
def do_add():
    f = bottle.request.forms
    name = f.get("name", "").strip() or None
    category = f.get("category", "films").strip()
    search = f.get("search", "").strip()
    request_from = f.get("request_from", "Radarr").strip()
    detail_ids = [v.strip() for v in f.getall("detail_id") if v.strip()]
    season_raw = f.get("season", "").strip()
    episode_raw = f.get("episode", "").strip()
    season = int(season_raw) if season_raw.isdigit() else None
    episode = int(episode_raw) if episode_raw.isdigit() else None

    if not search:
        bottle.redirect("/add")

    domain = _read_domain()
    search_url = f"https://{domain}/?p={category}&search={quote_plus(search)}"
    detail_urls = [f"https://{domain}/?p={category}&id={did}" for did in detail_ids]

    success, log, case_name = _run_add_case(
        name=name,
        search_url=search_url,
        detail_urls=detail_urls,
        request_from=request_from,
        search_string=search,
        season=season,
        episode=episode,
        mirror=None,
    )

    # Format log with colors
    def fmt_line(line):
        if line.startswith("ERREUR") or "ERREUR" in line:
            return f'<span class="log-err">{line}</span>'
        if line.startswith("---"):
            return f'<span class="log-section">{line}</span>'
        if line.strip().startswith("http"):
            return f'<span class="log-url">{line}</span>'
        if line.startswith("  ->") or "URL(s)" in line:
            return f'<span class="log-ok">{line}</span>'
        return line

    log_html = "\n".join(fmt_line(l) for l in log)
    result_class = "result-ok" if success else "result-err"
    title_class = "ok" if success else "err"
    title_text = f"Cas '{case_name}' cree avec succes !" if success else "Echec de la creation"

    body = f"""
<a href="/" class="back">← Retour</a>
<h2>Resultat</h2>
<div class="result-card {result_class}">
  <div class="result-title {title_class}">{title_text}</div>
  <div class="log">{log_html}</div>
  <div style="margin-top:16px;display:flex;gap:10px">
    <a href="/" class="btn btn-primary">← Retour a la liste</a>
    <a href="/add" class="btn btn-primary" style="background:#6b7280">Ajouter un autre</a>
  </div>
</div>
"""
    return _html("Resultat", body)


@app.route("/case/<name>/delete", method="POST")
def delete_case(name):
    import shutil
    case_dir = CASES_DIR / name
    if case_dir.exists() and (case_dir / "manifest.json").exists():
        shutil.rmtree(case_dir)
    bottle.redirect("/")


# ── Lancement ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import webbrowser
    url = f"http://localhost:{PORT}"
    print(f"Interface ZT Tests : {url}")
    print("Ctrl+C pour arreter\n")
    webbrowser.open(url)
    bottle.run(app, host="localhost", port=PORT, quiet=True)
