# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import datetime
import locale

import html
import re
import time
from base64 import urlsafe_b64encode
from datetime import timezone, timedelta
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

from quasarr.providers.imdb_metadata import get_localized_title
from quasarr.providers.log import info, debug

hostname = "zt"
supported_mirrors = ["rapidgator","turbobit","1fichier", "nitroflare", "dailyuploads","uploady"]

def update_hostname(shared_state, current_host, final_url):
    try:
        parsed = urlparse(final_url)
        final_host = parsed.netloc.lower()
    except Exception:
        return current_host

    if final_host and current_host and final_host != current_host:
        info(f"{hostname.upper()} redirect detected. Updating hostname to '{final_host}'.")
        shared_state.values["config"]("Hostnames").save(hostname.lower(), final_host)
        return final_host
    return current_host

def extract_size(text):
    match = re.match(r"([\d\.]+)\s*([KMGT]o)", text, re.IGNORECASE)
    if match:
        size = match.group(1)
        unit = match.group(2).upper()
        return {"size": size, "sizeunit": unit}
    else:
        raise ValueError(f"Invalid size format: {text}")


def parse_published_datetime(article):
    date_box = article.find('time').text
    locale.setlocale(locale.LC_TIME, "fr_FR.UTF-8")
    dt = datetime.strptime(date_box, "%d %B %Y")
    month_num = dt.month
    day = dt.day
    year = dt.year
    hh, mm = 0, 0

    # this timezone is fixed to CET+1 and might be wrong
    cet = timezone(timedelta(hours=1))
    dt = datetime.datetime(int(year), month_num, int(day), hh, mm, tzinfo=cet)
    return dt.isoformat()


def zt_feed(shared_state, start_time, request_from, mirror=None):
    zt = shared_state.values['config']('Hostnames').get(hostname)

    if "lazylibrarian" in request_from.lower():
        feed_type = "?cat=71"
    elif "radarr" in request_from.lower():
        feed_type = "?p=films"
    else:
        feed_type = "?p=series"

    base_url = f"https://{zt}"
    url = f"{base_url}/{feed_type}"
    headers = {'User-Agent': shared_state.values['user_agent']}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        me = update_hostname(shared_state, me, response.url)
        soup = BeautifulSoup(response.content, 'html.parser')
        releases = _parse_posts(soup, shared_state, base_url, request_from=request_from, mirror_filter=mirror)
    except Exception as e:
        info(f"Error loading {hostname.upper()} feed: {e}")
        releases = []
    debug(f"Time taken: {time.time() - start_time:.2f}s ({hostname})")
    return releases

def _parse_posts(soup:BeautifulSoup, shared_state, base_url, mirror_filter,
                 is_search=False, request_from=None, search_string=None,
                 season=None, episode=None):
    releases = []
    
    for entry in soup.find_all("div", {"class": "cover_global"}):
        try:
            url_page = entry.find("a").get("href","").strip()
            headers = {'User-Agent': shared_state.values['user_agent']}
            response = requests.get(url_page, headers=headers, timeout=10)
            response.raise_for_status()
            page_soup = BeautifulSoup(response.content, 'html.parser')
            if "radarr"  in request_from.lower():
                title = page_soup.find("font",{"color":"red"}).text
                pattern = re.compile(
                    r'^(?P<titre>.+?)'                         # Titre avant l'année
                    r'(?:[\.\s]*\(?)(?P<annee>\d{4})(?:\)?)'   # Année, avec ou sans parenthèses
                    r'(?P<details>.+?)\.(?P<ext>\w+)'          # Détails + extension
                    r'\s*\((?P<taille>[\d\.]+\s+\w+)\)$'       # Taille
                )

            match = pattern.match(title)
            if match:
                annee = match.group("annee")
                details = match.group("details").replace(".", " ")
                taille = match.group("taille")
            payload = urlsafe_b64encode(
                f"{title}|{source}|{mirror_filter}|{mb}|{password}|{imdb_id}".encode()
            ).decode()
            link = f"{shared_state.values['internal_address']}/download/?payload={payload}"

            releases.append({
                'details': {
                    'title': title,
                    'hostname': hostname,
                    'imdb_id': imdb_id,
                    'link': link,
                    'mirror': mirror_filter,
                    'size': size_bytes,
                    'date': published,
                    'source': source
                },
                'type': 'protected'
            })
        except Exception as e:
            debug(f"Error parsing {hostname.upper()}: {e}")
            continue

    return releases
def zt_search(shared_state, start_time, request_from, search_string, mirror=None, season=None, episode=None):
    releases = []
    dt = shared_state.values["config"]("Hostnames").get(hostname.lower())
    password = dt

    if "lazylibrarian" in request_from.lower():
        cat_id = "100"
    elif "radarr" in request_from.lower():
        cat_id = "9"
    else:
        cat_id = "64"

    if mirror and mirror not in supported_mirrors:
        debug(f'Mirror "{mirror}" not supported by "{hostname.upper()}". Skipping search!')
        return releases

    try:
        imdb_id = shared_state.is_imdb_id(search_string)
        if imdb_id:
            search_string = get_localized_title(shared_state, imdb_id, 'en')
            if not search_string:
                info(f"Could not extract title from IMDb-ID {imdb_id}")
                return releases
            search_string = html.unescape(search_string)

        q = quote_plus(search_string)

        url = (
            f"https://{dt}/index.php?"
            f"do=search&"
            f"subaction=search&"
            f"search_start=0&"
            f"full_search=1&"
            f"story={q}&"
            f"catlist%5B%5D={cat_id}&"
            f"sortby=date&"
            f"resorder=desc&"
            f"titleonly=3&"
            f"searchuser=&"
            f"beforeafter=after&"
            f"searchdate=0&"
            f"replyless=0&"
            f"replylimit=0&"
            f"showposts=0"
        )
        headers = {"User-Agent": shared_state.values["user_agent"]}

        resp = requests.get(url, headers=headers, timeout=10).content
        page = BeautifulSoup(resp, "html.parser")

        for article in page.find_all("article"):
            try:
                link_tag = article.select_one("h4.font-weight-bold a")
                if not link_tag:
                    debug(f"No title link in search-article: {article}")
                    continue
                source = link_tag["href"]
                title_raw = link_tag.text.strip()
                title = (title_raw.
                         replace(' - ', '-').
                         replace(' ', '.').
                         replace('(', '').
                         replace(')', '')
                         )

                if not shared_state.is_valid_release(title,
                                                     request_from,
                                                     search_string,
                                                     season,
                                                     episode):
                    continue

                if 'lazylibrarian' in request_from.lower():
                    # lazylibrarian can only detect specific date formats / issue numbering for magazines
                    title = shared_state.normalize_magazine_title(title)

                try:
                    imdb_id = re.search(r"tt\d+", str(article)).group()
                except:
                    imdb_id = None

                body_text = article.find("div", class_="card-body").get_text(" ")
                m = re.search(r"(\d+(?:\.\d+)?\s*(?:GB|MB|KB|TB))", body_text, re.IGNORECASE)
                if not m:
                    debug(f"Size not found in search-article: {title_raw}")
                    continue
                size_item = extract_size(m.group(1).strip())
                mb = shared_state.convert_to_mb(size_item)
                size = mb * 1024 * 1024

                published = parse_published_datetime(article)

                payload = urlsafe_b64encode(
                    f"{title}|{source}|{mirror}|{mb}|{password}|{imdb_id}"
                    .encode("utf-8")
                ).decode("utf-8")
                link = f"{shared_state.values['internal_address']}/download/?payload={payload}"

            except Exception as e:
                info(f"Error parsing {hostname.upper()} search item: {e}")
                continue

            releases.append({
                "details": {
                    "title": title,
                    "hostname": hostname.lower(),
                    "imdb_id": imdb_id,
                    "link": link,
                    "mirror": mirror,
                    "size": size,
                    "date": published,
                    "source": source
                },
                "type": "protected"
            })

    except Exception as e:
        info(f"Error loading {hostname.upper()} search page: {e}")

    elapsed = time.time() - start_time
    debug(f"Search time: {elapsed:.2f}s ({hostname})")
    return releases
