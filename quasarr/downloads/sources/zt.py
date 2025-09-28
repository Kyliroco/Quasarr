# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from quasarr.providers.log import info, debug

hostname = "zt"
UNSUPPORTED_MIRRORS =["nitroflare"]

IGNORED_HOSTS = {
    "imdb.com",
    "www.imdb.com",
    "themoviedb.org",
    "www.themoviedb.org",
    "youtube.com",
    "www.youtube.com",
    "twitter.com",
    "www.twitter.com",
    "facebook.com",
    "www.facebook.com",
}


def _update_hostname(shared_state, current_host, final_url):
    try:
        parsed = urlparse(final_url)
        final_host = parsed.netloc.lower()
    except Exception:
        return current_host

    if final_host and current_host and final_host != current_host:
        info(f"{hostname.upper()} redirect detected while resolving download page. Updating hostname to '{final_host}'.")
        shared_state.values["config"]("Hostnames").save(hostname.lower(), final_host)
        return final_host
    return current_host


def _extract_imdb_id(soup):
    link = soup.find("a", href=re.compile(r"imdb\.com/title/(tt\d+)", re.IGNORECASE))
    if not link:
        return None
    match = re.search(r"(tt\d+)", link.get("href", ""))
    return match.group(1) if match else None


def _iter_candidate_links(soup):
    selectors = [
        '[id*="download"]',
        '[class*="download"]',
        '[id*="ddl"]',
        '[class*="ddl"]',
        '[id*="telecharg"]',
        '[class*="telecharg"]',
        '[id*="lien"]',
        '[class*="lien"]',
        'div.card-body',
        'div.postinfo',
    ]

    seen = set()

    for selector in selectors:
        for node in soup.select(selector):
            if node not in seen:
                seen.add(node)
                yield node

    if not seen:
        yield soup

def denormalize_url(url: str) -> str:
    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")

    if len(path_parts) >= 2:
        category = path_parts[0]   # ex: film, serie, anime
        item_id = path_parts[1]    # ex: 34098-alvin...
        return f"{parsed.scheme}://{parsed.netloc}/?p={category}&id={item_id}"

    return url


def _split_episode_fragment(url: str):
    parsed = urlparse(url)
    episode = None
    if parsed.fragment:
        for fragment in parsed.fragment.split("&"):
            if not fragment:
                continue
            key, _, value = fragment.partition("=")
            if key == "episode" and value.isdigit():
                episode = int(value)
                break
    cleaned = parsed._replace(fragment="")
    return urlunparse(cleaned), episode


_EPISODE_RANGE_PATTERN = re.compile(
    r"(?i)(?:é?pisode|ep)\s*(\d{1,3})(?:\s*[-à]\s*(\d{1,3}))?"
)


def _episode_numbers_from_text(text: str):
    numbers = set()
    if not text:
        return numbers

    for match in _EPISODE_RANGE_PATTERN.finditer(text):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        for value in range(start, end + 1):
            numbers.add(value)

    if not numbers:
        for raw in re.findall(r"\d{1,3}", text):
            try:
                numbers.add(int(raw))
            except ValueError:
                continue

    return numbers

def get_zt_download_links(shared_state, url, mirror, title):
    config = shared_state.values["config"]("Hostnames")
    zt = config.get(hostname)
    headers = {"User-Agent": shared_state.values["user_agent"]}

    base_url, target_episode = _split_episode_fragment(url)

    debug(
        f"{hostname.upper()} fetching download page for '{title}' "
        f"(mirror={mirror}, episode={target_episode}) at {base_url}"
    )

    try:
        response = requests.get(denormalize_url(base_url), headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        message = (
            f"{hostname.upper()} site has been updated. Grabbing download links for {title} "
            f"not possible: {exc}"
        )
        info(message)
        raise RuntimeError(message) from exc

    zt = _update_hostname(shared_state, zt, response.url)
    soup = BeautifulSoup(response.text, "html.parser")

    candidates = []
    visited = set()

    for container in _iter_candidate_links(soup):
        header = container.find_previous("h2")
        if header and "streaming" in header.get_text(strip=True).lower():
            debug(
                f"{hostname.upper()} skipping streaming container for '{title}' on {response.url}"
            )
            continue

        for a_tag in container.find_all("a", href=True):
            href = a_tag.get("href", "").strip()
            if not href or href.startswith("javascript:"):
                debug(
                    f"{hostname.upper()} ignoring non-link anchor '{href}' for '{title}'"
                )
                continue

            absolute = urljoin(response.url, href)
            parsed = urlparse(absolute)
            scheme = parsed.scheme.lower()
            if scheme not in {"http", "https"}:
                debug(
                    f"{hostname.upper()} ignoring unsupported scheme '{scheme}' for link {absolute}"
                )
                continue

            netloc = parsed.netloc.lower()
            if not netloc:
                debug(f"{hostname.upper()} ignoring link without netloc: {absolute}")
                continue

            if netloc.endswith(zt):
                debug(
                    f"{hostname.upper()} skipping internal redirect link {absolute} for '{title}'"
                )
                continue

            if netloc in IGNORED_HOSTS:
                debug(f"{hostname.upper()} ignoring known non-hoster domain {netloc}")
                continue

            link_text_lower = a_tag.get_text(" ", strip=True).lower()
            if "regarder" in link_text_lower:
                debug(
                    f"{hostname.upper()} skipping streaming text link {absolute} for '{title}'"
                )
                continue

            query = parse_qs(parsed.query)
            rl_values = [v.lower() for values in query.values() for v in values]
            if any(value in {"a1", "h1"} for value in rl_values):
                debug(f"{hostname.upper()} skipping streaming query link {absolute}")
                continue

            hoster_name = None
            prev_div = a_tag.find_previous("div")
            while prev_div:
                if prev_div.find_parent("div", class_="postinfo") == container:
                    hoster_name = prev_div.get_text(strip=True)
                    break
                prev_div = prev_div.find_previous("div")

            if not hoster_name:
                hoster_parts = netloc.split(".")
                if len(hoster_parts) >= 2:
                    hoster_name = hoster_parts[-2]
                else:
                    hoster_name = netloc

            base_hoster = hoster_name.strip()
            if base_hoster.lower() in UNSUPPORTED_MIRRORS:
                debug(f"{hostname.upper()} skipping unsupported hoster '{base_hoster}'")
                continue

            if base_hoster.lower() in {zt.lower(), "dl-protect"}:
                debug(f"{hostname.upper()} ignoring protected redirect hoster {base_hoster}")
                continue

            if mirror and mirror.lower() not in base_hoster.lower():
                debug(
                    f"{hostname.upper()} skipping hoster '{base_hoster}' because it "
                    f"does not match requested mirror '{mirror}'"
                )
                continue

            if absolute in visited:
                debug(f"{hostname.upper()} already collected link {absolute}")
                continue

            anchor_text = a_tag.get_text(" ", strip=True)
            if anchor_text:
                anchor_text = " ".join(anchor_text.split())
            episode_numbers = _episode_numbers_from_text(anchor_text)

            if target_episode is not None:
                if episode_numbers and target_episode not in episode_numbers:
                    debug(
                        f"{hostname.upper()} skipping '{absolute}' because it covers episodes {sorted(episode_numbers)}"
                    )
                    continue
                if episode_numbers and len(episode_numbers) > 1:
                    debug(
                        f"{hostname.upper()} skipping pack link {absolute} covering episodes {sorted(episode_numbers)}"
                    )
                    continue

            visited.add(absolute)

            display_name = base_hoster
            if anchor_text:
                display_name = f"{base_hoster} - {anchor_text}"

            debug(
                f"{hostname.upper()} accepted download link {absolute} with label '{display_name}'"
            )
            candidates.append({
                "url": absolute,
                "display": display_name,
                "host": base_hoster,
                "episodes": frozenset(episode_numbers),
            })

    if not candidates:
        info(f"{hostname.upper()} site returned no recognizable download links for {title}.")
        return []

    hoster_groups = []
    hoster_index = {}
    for entry in candidates:
        host_key = entry["host"].lower()
        if host_key not in hoster_index:
            hoster_index[host_key] = len(hoster_groups)
            hoster_groups.append({"host": entry["host"], "entries": []})
        hoster_groups[hoster_index[host_key]]["entries"].append(entry)

    for index, group in enumerate(hoster_groups):
        if group["host"].lower() == "rapidgator":
            hoster_groups.append(hoster_groups.pop(index))
            break

    requested_episodes = set()
    for entry in candidates:
        requested_episodes.update(entry["episodes"])

    if target_episode is not None:
        requested_episodes = {target_episode}

    missing_episodes = set(requested_episodes)
    resolved_links = []
    resolved_seen = set()

    def _resolve_entry(entry):
        try:
            final_url = get_final_links(entry["url"])
        except Exception as exc:
            debug(f"{hostname.upper()} failed to resolve link {entry['url']}: {exc}")
            return None

        if not final_url:
            debug(f"{hostname.upper()} resolver returned no link for {entry['url']}")
            return None

        alive, info_data = is_link_alive(final_url)
        if not alive:
            debug(
                f"{hostname.upper()} skipping dead link {final_url}"
                f" ({info_data.get('status_code')} - {info_data.get('note')})"
            )
            return None

        return final_url

    for group in hoster_groups:
        host_name = group["host"]
        relevant_entries = []
        for entry in group["entries"]:
            episodes = set(entry["episodes"])
            if missing_episodes:
                if episodes:
                    if missing_episodes.isdisjoint(episodes):
                        debug(
                            f"{hostname.upper()} skipping '{entry['url']}' from hoster '{host_name}' "
                            f"because it only covers episodes {sorted(episodes)}"
                        )
                        continue
                else:
                    debug(
                        f"{hostname.upper()} considering '{entry['url']}' from hoster '{host_name}' "
                        "despite missing episode markers because requests remain"
                    )

            relevant_entries.append(entry)

        if not relevant_entries:
            continue

        debug(
            f"{hostname.upper()} validating {len(relevant_entries)} episode links for hoster '{host_name}'"
        )

        max_workers = max(1, min(8, len(relevant_entries)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_entry = {
                executor.submit(_resolve_entry, entry): entry for entry in relevant_entries
            }

            for future in as_completed(future_to_entry):
                entry = future_to_entry[future]
                try:
                    final_url = future.result()
                except Exception as exc:  # pragma: no cover - safeguard
                    debug(
                        f"{hostname.upper()} unexpected error while resolving {entry['url']}: {exc}"
                    )
                    continue

                if not final_url:
                    continue

                if final_url in resolved_seen:
                    debug(f"{hostname.upper()} skipping duplicate resolved link {final_url}")
                    continue

                resolved_seen.add(final_url)
                resolved_links.append(final_url)

                if missing_episodes:
                    resolved_eps = set(entry["episodes"])
                    if resolved_eps:
                        missing_episodes.difference_update(resolved_eps)
                    elif target_episode is not None:
                        missing_episodes.discard(target_episode)

        if requested_episodes and not missing_episodes:
            break

    if not resolved_links:
        info(f"{hostname.upper()} could not validate any download links for {title}.")
        return []

    debug(
        f"{hostname.upper()} resolved {len(resolved_links)} download links for '{title}'"
    )
    return resolved_links

DEFAULT_TIMEOUT = 15

def is_link_alive(url, session=None, timeout=DEFAULT_TIMEOUT, max_head_size=1024):
    """
    Teste si une URL de fichier est "vivante".
    Retourne un tuple (alive:bool, info:dict)
    info contient: status_code, reason, content_type, content_length, note
    """
    s = session or requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    })

    info = {"status_code": None, "reason": None, "content_type": None, "content_length": None, "note": None}
    try:
        # 1) quick HEAD
        resp = s.head(url, allow_redirects=False, timeout=timeout)
        info["status_code"] = resp.status_code
        info["reason"] = resp.reason
        info["content_type"] = resp.headers.get("Content-Type")
        info["content_length"] = resp.headers.get("Content-Length")
        # If HEAD gives a clear positive:
        if 200 <= resp.status_code < 300:
            # If it's binary (non-html) and has length -> very likely alive
            ct = info["content_type"] or ""
            if "text/html" not in ct.lower():
                info["note"] = "HEAD OK and non-HTML content-type"
                return True, info
            # if HTML returned on HEAD, we need to inspect content (some hosts respond HTML even for files)
        elif resp.status_code in (403, 401):
            # Some hosts block HEAD; fallthrough to GET test
            info["note"] = f"HEAD returned {resp.status_code}; trying GET"
        elif resp.status_code in (404, 410):
            info["note"] = "Not found according to HEAD"
            return False, info
        elif 300<= resp.status_code <400 :
            parsed = urlparse(url)
            hostname_base = parsed.hostname
            parsed = urlparse(resp.headers["Location"])
            hostname_redirection = parsed.hostname
            if hostname_redirection == None:
                return is_link_alive("https://"+hostname_base+resp.headers["Location"])
            if hostname_base != hostname_redirection:
                return is_link_alive(resp.headers["Location"])
            info["note"] = "Redirection pas normal"
            return False,info
        else:
            # other 3xx / 4xx / 5xx -> try GET for safety
            info["note"] = f"HEAD returned {resp.status_code}; trying GET"
    except requests.RequestException as e:
        info["note"] = f"HEAD failed: {e}; trying GET"

    # 2) Fallback: small GET to check body (use Range to avoid big download)
    try:
        headers = {"Range": f"bytes=0-{max_head_size-1}"}
        resp = s.get(url, allow_redirects=True, timeout=timeout, headers=headers, stream=True)
        info["status_code"] = resp.status_code
        info["reason"] = resp.reason
        info["content_type"] = resp.headers.get("Content-Type")
        info["content_length"] = resp.headers.get("Content-Length")
        body_snippet = resp.content[:max_head_size].decode('utf-8', errors='ignore').lower()

        # common patterns indicating a dead link (customize per host)
        dead_signatures = [
            "file not found", "not found", "404", "no such file", "the file was removed",
            "this file has been deleted", "error 404", "page not found", "no such user",
        ]
        # host specific quick checks (example: 1fichier returns HTML error message)
        if resp.status_code in (200, 206):  # 206 if range supported
            # if content-type is HTML and body contains error phrases -> dead
            ct = (info["content_type"] or "").lower()
            if "text/html" in ct:
                for sig in dead_signatures:
                    if sig in body_snippet:
                        info["note"] = f"HTML page contains error signature: '{sig}'"
                        return False, info
                # otherwise could still be a valid download page (some hosts require JS)

                if  "searching for the file" in resp.text.lower():
                    info["note"] = "turbobit dont find"
                    return False, info
                info["note"] = "GET returned HTML but no obvious error signature"
                return True, info
            else:
                info["note"] = "GET returned non-HTML (likely file) => alive"
                return True, info
        elif resp.status_code in (403, 401):
            info["note"] = "Access denied (403/401)"
            return False, info
        elif resp.status_code in (404, 410):
            info["note"] = "Not found (404/410)"
            return False, info
        else:
            info["note"] = f"GET returned {resp.status_code}"
            # consider dead for 5xx or unknown codes
            return False, info
    except requests.RequestException as e:
        info["note"] = f"GET failed: {e}"
        return False, info

#!/usr/bin/env python3
"""
solve_and_extract_link.py

Même principe que précédemment, mais main() renvoie (et affiche) le href du premier
élément <a> dont l'attribut rel contient 'external' et 'nofollow'.

Usage:
  pip install requests beautifulsoup4
  python3 solve_and_extract_link.py
"""

import time

# ---------------- CONFIGURATION ----------------
API_KEY = "e8afb42ed3de82c60392cfea55ecf555"                         # <-- remplace par ta clé 2captcha
MAX_WAIT_SECONDS = 200                              # timeout max pour la résolution
POLL_INTERVAL = 5                                   # intervalle de polling (s)
# ------------------------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
}

def fetch_page(session: requests.Session, url: str):
    r = session.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text, r.url  # renvoie HTML et URL finale (redirects possibles)

def find_form_and_sitekey(html: str):
    soup = BeautifulSoup(html, "html.parser")

    # Cherche d'abord un div Turnstile
    div_turn = soup.find("div", class_="cf-turnstile")
    if div_turn and div_turn.get("data-sitekey"):
        return soup, div_turn.get("data-sitekey"), div_turn

    # Sinon recherche reCAPTCHA / hCaptcha (fallback)
    rec = soup.find(attrs={"data-sitekey": True})
    if rec:
        sk = rec.get("data-sitekey")
        return soup, sk, rec

    # Si pas trouvé
    return soup, None, None

def extract_form(soup: BeautifulSoup):
    form = soup.find("form")
    if not form:
        raise RuntimeError("Aucun <form> trouvé sur la page.")
    action = form.get("action") or ""
    method = (form.get("method") or "GET").upper()
    data = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        value = inp.get("value", "")
        data[name] = value
    return form, action, method, data

def submit_to_2captcha_turnstile(api_key: str, sitekey: str, pageurl: str):
    in_url = "https://2captcha.com/in.php"
    params = {
        "key": api_key,
        "method": "turnstile",
        "pageurl": pageurl,
        "sitekey": sitekey,
        "json": 1
    }
    resp = requests.get(in_url, params=params, timeout=30)
    j = resp.json()
    if j.get("status") != 1:
        raise RuntimeError(f"Erreur soumission 2Captcha: {j}")
    return j["request"]  # id

def poll_2captcha_result(api_key: str, captcha_id: str, max_wait: int = MAX_WAIT_SECONDS, poll_interval: int = POLL_INTERVAL):
    res_url = "https://2captcha.com/res.php"
    waited = 0
    while waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval
        r = requests.get(res_url, params={"key": api_key, "action": "get", "id": captcha_id, "json": 1}, timeout=30)
        j = r.json()
        if j.get("status") == 1:
            return j["request"]
        if j.get("request") and j.get("request") != "CAPCHA_NOT_READY":
            raise RuntimeError(f"Erreur 2Captcha pendant polling: {j}")
    raise TimeoutError("Timeout waiting for 2Captcha result")

def find_external_nofollow_href(html: str, base_url: str):
    """
    Retourne le href du premier <a> dont rel contient 'external' et 'nofollow'.
    Si href est relatif, le convertit en URL absolue via base_url.
    """
    soup = BeautifulSoup(html, "html.parser")
    # Parcours tous les <a> et vérifie l'attribut rel
    for a in soup.find_all("a", href=True):
        rel = a.get("rel")
        if not rel:
            # parfois rel est une string au lieu d'une liste
            rel_attr = a.get("rel")
        else:
            rel_attr = rel
        # normaliser en string pour test (ex: ['external','nofollow'] ou "external nofollow")
        if isinstance(rel_attr, (list, tuple)):
            rel_str = " ".join(rel_attr).lower()
        else:
            rel_str = str(rel_attr).lower()
        # vérifier la présence des deux tokens
        if "external" in rel_str and "nofollow" in rel_str:
            href = a["href"]
            return urljoin(base_url, href)
    return None

def get_final_links(url):
    session = requests.Session()
    session.headers.update(HEADERS)

    html, final_url = fetch_page(session, url)

    soup, sitekey, _ = find_form_and_sitekey(html)
    if not sitekey:
        raise RuntimeError("Impossible de trouver de sitekey Turnstile / reCAPTCHA sur la page.")

    form, action, method, form_data = extract_form(soup)
    action_url = urljoin(final_url, action) if action else final_url

    # Soumettre le captcha à 2captcha
    captcha_id = submit_to_2captcha_turnstile(API_KEY, sitekey, final_url)

    token = poll_2captcha_result(API_KEY, captcha_id, max_wait=MAX_WAIT_SECONDS, poll_interval=POLL_INTERVAL)

    # Nom du champ Turnstile
    field_name = "cf-turnstile-response"
    if "cf-turnstile-response" in form_data:
        field_name = "cf-turnstile-response"
    elif "g-recaptcha-response" in form_data:
        field_name = "g-recaptcha-response"

    form_data[field_name] = token

    if method == "POST":
        resp = session.post(action_url, data=form_data, allow_redirects=True, timeout=60)
    else:
        resp = session.get(action_url, params=form_data, allow_redirects=True, timeout=60)


    # Cherche le lien <a rel="external nofollow"> dans la réponse finale
    href = find_external_nofollow_href(resp.text, resp.url)
    if href:
        debug("[+] Lien trouvé :"+ str(href))
    else:
        info("[!] Aucun lien <a rel='external nofollow'> trouvé dans la réponse.")

    # retourne le href (ou None)
    return href


