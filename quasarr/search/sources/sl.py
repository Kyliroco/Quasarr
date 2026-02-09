# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import datetime
import html
import re
import time
import xml.etree.ElementTree as ET
from base64 import urlsafe_b64encode
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from quasarr.providers.imdb_metadata import get_localized_title
from quasarr.providers.log import info, debug

hostname = "sl"
supported_mirrors = ["nitroflare", "ddownload"]  # ignoring captcha-protected multiup/mirrorace for now


def extract_size(text):
    match = re.match(r"([\d\.]+)\s*([KMGT]B)", text, re.IGNORECASE)
    if match:
        size = match.group(1)
        unit = match.group(2).upper()
        return {"size": size, "sizeunit": unit}
    else:
        raise ValueError(f"Invalid size format: {text}")


def parse_pubdate_to_iso(pubdate_str):
    """
    Parse an RFC-822 pubDate from RSS into an ISO8601 string with timezone.
    """
    dt = datetime.datetime.strptime(pubdate_str, '%a, %d %b %Y %H:%M:%S %z')
    return dt.isoformat()


def sl_feed(shared_state, start_time, request_from, mirror=None):
    releases = []

    sl = shared_state.values["config"]("Hostnames").get(hostname.lower())
    password = sl

    if "lazylibrarian" in request_from.lower():
        feed_type = "ebooks"
    elif "radarr" in request_from.lower():
        feed_type = "movies"
    else:
        feed_type = "tv-shows"

    if mirror and mirror not in supported_mirrors:
        debug(f'Mirror "{mirror}" not supported by "{hostname.upper()}". Supported: {supported_mirrors}. Skipping!')
        return releases

    url = f'https://{sl}/{feed_type}/feed/'
    headers = {'User-Agent': shared_state.values['user_agent']}

    try:
        xml_text = requests.get(url, headers=headers, timeout=10).text
        root = ET.fromstring(xml_text)

        for item in root.find('channel').findall('item'):
            try:
                title = item.findtext('title').strip()
                if 'lazylibrarian' in request_from.lower():
                    # lazylibrarian can only detect specific date formats / issue numbering for magazines
                    title = shared_state.normalize_magazine_title(title)

                source = item.findtext('link').strip()

                desc = item.findtext('description') or ''

                size_match = re.search(r"Size:\s*([\d\.]+\s*(?:GB|MB|KB|TB))", desc, re.IGNORECASE)
                if not size_match:
                    debug(f"Size not found in RSS item: {title}")
                    continue
                size_info = size_match.group(1).strip()
                size_item = extract_size(size_info)
                mb = shared_state.convert_to_mb(size_item)
                size = mb * 1024 * 1024

                pubdate = item.findtext('pubDate').strip()
                published = parse_pubdate_to_iso(pubdate)

                m = re.search(r"https?://www\.imdb\.com/title/(tt\d+)", desc)
                imdb_id = m.group(1) if m else None

                payload = urlsafe_b64encode(
                    f"{title}|{source}|{mirror}|{mb}|{password}|{imdb_id}".encode("utf-8")
                ).decode("utf-8")
                link = f"{shared_state.values['internal_address']}/download/?payload={payload}"

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
                info(f"Error parsing {hostname.upper()} feed item: {e}")
                continue

    except Exception as e:
        info(f"Error loading {hostname.upper()} feed: {e}")

    elapsed = time.time() - start_time
    debug(f"Time taken: {elapsed:.2f}s ({hostname})")
    return releases


def sl_search(shared_state, start_time, request_from, search_string, mirror=None, season=None, episode=None):
    releases = []
    sl = shared_state.values["config"]("Hostnames").get(hostname.lower())
    password = sl

    if "lazylibrarian" in request_from.lower():
        feed_type = "ebooks"
    elif "radarr" in request_from.lower():
        feed_type = "movies"
    else:
        feed_type = "tv-shows"

    if mirror and mirror not in supported_mirrors:
        debug(f'Mirror "{mirror}" not supported by "{hostname.upper()}". Supported: {supported_mirrors}. Skipping!')
        return releases

    try:
        imdb_id = shared_state.is_imdb_id(search_string)
        if imdb_id:
            search_string = get_localized_title(shared_state, imdb_id, 'en') or ''
            search_string = html.unescape(search_string)
            if not search_string:
                info(f"Could not extract title from IMDb-ID {imdb_id}")
                return releases

        # Perform HTML search (faster than feed)
        q = quote_plus(search_string)
        url = f'https://{sl}/{feed_type}/?s={q}'
        headers = {"User-Agent": shared_state.values['user_agent']}
        html_text = requests.get(url, headers=headers, timeout=10).text

        soup = BeautifulSoup(html_text, 'html.parser')
        posts = soup.find_all('div', class_=lambda c: c and c.startswith('post-'))

        for post in posts:
            try:
                # Title and link
                a = post.find('h1').find('a')
                title = a.get_text(strip=True)

                valid, _reject_reason = shared_state.is_valid_release(title,
                                                                    request_from,
                                                                    search_string,
                                                                    season,
                                                                    episode)
                if not valid:
                    continue

                if 'lazylibrarian' in request_from.lower():
                    # lazylibrarian can only detect specific date formats / issue numbering for magazines
                    title = shared_state.normalize_magazine_title(title)

                source = a['href']

                # Published date
                time_tag = post.find('span', {'class': 'localtime'})
                published = None
                if time_tag and time_tag.has_attr('data-lttime'):
                    published = time_tag['data-lttime']
                # Fallback: now
                published = published or datetime.datetime.utcnow().isoformat() + '+00:00'

                # No description in HTML search: set size zero and no IMDb
                size = 0
                imdb_id = None

                # Build payload and link
                payload = urlsafe_b64encode(
                    f"{title}|{source}|{mirror}|0|{password}|{imdb_id}".encode('utf-8')
                ).decode('utf-8')
                link = f"{shared_state.values['internal_address']}/download/?payload={payload}"

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
                info(f"Error parsing {hostname.upper()} search item: {e}")
                continue

    except Exception as e:
        info(f"Error loading {hostname.upper()} search page: {e}")

    elapsed = time.time() - start_time
    debug(f"Search time: {elapsed:.2f}s ({hostname})")
    return releases
