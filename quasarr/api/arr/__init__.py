# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import traceback
import xml.sax.saxutils as sax_utils
from base64 import urlsafe_b64decode
from datetime import datetime
from functools import wraps
from textwrap import dedent
from urllib.parse import urlparse, parse_qs
from xml.etree import ElementTree

from bottle import abort, request

from quasarr.downloads import download
from quasarr.downloads.packages import get_packages, delete_package
from quasarr.providers import shared_state
from quasarr.providers.log import info, debug
from quasarr.providers.version import get_version
from quasarr.search import get_search_results
from quasarr.storage.config import Config


NEWZNAB_CATEGORY_NAMES = {
    "2000": "Movies",
    "5000": "TV",
    "7000": "Books",
}


def _expand_category_ids(category_ids):
    """Return the provided ids plus their top-level Newznab parents."""

    expanded = set()

    for category_id in category_ids:
        if not category_id:
            continue

        category_id = str(category_id)
        expanded.add(category_id)

        try:
            numeric_id = int(category_id)
        except (TypeError, ValueError):
            continue

        parent_id = numeric_id - (numeric_id % 1000)
        expanded.add(f"{parent_id:0{len(category_id)}d}")

    return expanded


def _build_caps_xml(base_url, version, *, last_update=None):
    info("Lancement build_caps_xml")
    normalized = (base_url or "").rstrip("/") or "http://localhost:9696"
    server_url = f"{normalized}/"
    image_url = f"{normalized}/static/logo.png"
    last_update = last_update or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    return dedent(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server version="{version}" title="Quasarr" strapline="Maison Energy indexer bridge"
      email="support@quasarr.app" url="{server_url}"
      image="{image_url}" />
  <limits max="100" default="50" />
  <retention days="0" />
  <registration available="no" open="no" />
  <searching>
    <search available="yes" supportedParams="q" />
    <tv-search available="yes" supportedParams="imdbid,season,ep" />
    <movie-search available="yes" supportedParams="imdbid" />
    <audio-search available="no" supportedParams="q" />
    <book-search available="no" supportedParams="q" />
  </searching>
  <categories>
    <category id="2000" name="Movies"/>
    <category id="5000" name="TV"/>
  </categories>
</caps>"""
    ).strip()


def _derive_newznab_category(request_from, mode, release_category=None):
    """Return the Newznab category id/name tuple for a release."""
    info("Lancement _derive_newznab_category")

    if release_category:
        category_id = str(release_category)
        return category_id, NEWZNAB_CATEGORY_NAMES.get(category_id)

    rf = (request_from or "").lower()
    mode = (mode or "").lower()

    if mode == "movie" or "radarr" in rf:
        return "2000", NEWZNAB_CATEGORY_NAMES.get("2000")
    if mode == "tvsearch" or "sonarr" in rf:
        return "5000", NEWZNAB_CATEGORY_NAMES.get("5000")
    if mode in {"book", "search"} or "lazylibrarian" in rf:
        return "7000", NEWZNAB_CATEGORY_NAMES.get("7000")
    return None, None


def _format_newznab_attrs(category_id, imdb_id, size):
    info("Lancement _format_newznab_attrs")

    attrs = []
    if category_id:
        attrs.append(f'<newznab:attr name="category" value="{category_id}" />')
    if imdb_id:
        imdb_value = imdb_id[2:] if imdb_id.startswith("tt") else imdb_id
        if imdb_value:
            attrs.append(f'<newznab:attr name="imdbid" value="{imdb_value}" />')
    if size:
        attrs.append(f'<newznab:attr name="size" value="{size}" />')
    if not attrs:
        return ""
    indent = " " * 28
    return "\n".join(f"{indent}{line}" for line in attrs)


def _filter_releases_by_categories(releases, allowed_ids, request_from, mode):
    """Limit releases to those whose category matches the requested ids."""
    info("Lancement _filter_releases_by_categories")

    if not allowed_ids:
        return releases

    normalized_ids = _expand_category_ids(allowed_ids)

    filtered = []
    for release in releases:
        details = release.get("details", {})
        release_category = details.get("category")
        if release_category is None:
            continue

        category_id, _ = _derive_newznab_category(
            request_from,
            mode,
            release_category,
        )

        if category_id and category_id in normalized_ids:
            filtered.append(release)

    return filtered


def require_api_key(func):
    info("Lancement require_api_key")

    @wraps(func)
    def decorated(*args, **kwargs):
        api_key = Config('API').get('key')
        if not request.query.apikey:
            return abort(401, "Missing API key")
        if request.query.apikey != api_key:
            return abort(403, "Invalid API key")
        return func(*args, **kwargs)

    return decorated


def setup_arr_routes(app):
    info("Lancement setup_arr_routes")
    @app.get('/download/')
    def fake_nzb_file():
        info("Lancement fake_nzb_file")

        payload = request.query.payload
        decoded_payload = urlsafe_b64decode(payload).decode("utf-8").split("|")
        title = decoded_payload[0]
        url = decoded_payload[1]
        mirror = decoded_payload[2]
        size_mb = decoded_payload[3]
        password = decoded_payload[4]
        imdb_id = decoded_payload[5]
        return f'<nzb><file title="{title}" url="{url}" mirror="{mirror}" size_mb="{size_mb}" password="{password}" imdb_id="{imdb_id}"/></nzb>'

    @app.post('/api')
    @require_api_key
    def download_fake_nzb_file():
        info("Lancement download_fake_nzb_file")

        downloads = request.files.getall('name')
        nzo_ids = []  # naming structure for package IDs expected in newznab

        for upload in downloads:
            file_content = upload.file.read()
            root = ElementTree.fromstring(file_content)

            title = sax_utils.unescape(root.find(".//file").attrib["title"])

            url = root.find(".//file").attrib["url"]
            mirror = None if (mirror := root.find(".//file").attrib.get("mirror")) == "None" else mirror

            size_mb = root.find(".//file").attrib["size_mb"]
            password = root.find(".//file").attrib.get("password")
            imdb_id = root.find(".//file").attrib.get("imdb_id")

            info(f'Attempting download for "{title}"')
            request_from = request.headers.get('User-Agent')
            downloaded = download(shared_state, request_from, title, url, mirror, size_mb, password, imdb_id)
            try:
                success = downloaded["success"]
                package_id = downloaded["package_id"]
                title = downloaded["title"]

                if success:
                    info(f'"{title}" added successfully!')
                else:
                    info(f'"{title}" added unsuccessfully! See log for details.')
                nzo_ids.append(package_id)
            except KeyError:
                info(f'Failed to download "{title}" - no package_id returned')

        return {
            "status": True,
            "nzo_ids": nzo_ids
        }

    @app.get('/api')
    @app.get('/api/<mirror>')
    @require_api_key
    def quasarr_api(mirror=None):
        info("Lancement quasarr_api avec type :")

        api_type = 'arr_download_client' if request.query.mode else 'arr_indexer' if request.query.t else None
        info(api_type)
        if api_type == 'arr_download_client':
            # This builds a mock SABnzbd API response based on the My JDownloader integration
            try:
                mode = request.query.mode
                info("mode "+mode)
                if mode == "auth":
                    return {
                        "auth": "apikey"
                    }
                elif mode == "version":
                    return {
                        "version": f"Quasarr {get_version()}"
                    }
                elif mode == "get_cats":
                    return {
                        "categories": [
                            "*",
                            "Movies",
                            "TV",
                            "Docs"
                        ]
                    }
                elif mode == "get_config":
                    return {
                        "config": {
                            "misc": {
                                "quasarr": True,
                                "complete_dir": "/tmp/"
                            },
                            "categories": [
                                {
                                    "name": "*",
                                    "order": 0,
                                    "dir": "",
                                },
                                {
                                    "name": "Movies",
                                    "order": 1,
                                    "dir": "",
                                },
                                {
                                    "name": "TV",
                                    "order": 2,
                                    "dir": "",
                                },
                                {
                                    "name": "Docs",
                                    "order": 3,
                                    "dir": "",
                                },
                            ]
                        }
                    }
                elif mode == "fullstatus":
                    return {
                        "status": {
                            "quasarr": True
                        }
                    }
                elif mode == "addurl":
                    raw_name = getattr(request.query, "name", None)
                    if not raw_name:
                        abort(400, "missing or empty 'name' parameter")

                    payload = False
                    try:
                        parsed = urlparse(raw_name)
                        qs = parse_qs(parsed.query)
                        payload = qs.get("payload", [None])[0]
                    except Exception as e:
                        abort(400, f"invalid URL in 'name': {e}")
                    if not payload:
                        abort(400, "missing 'payload' parameter in URL")

                    title = url = mirror = size_mb = password = imdb_id = None
                    try:
                        decoded = urlsafe_b64decode(payload.encode()).decode()
                        parts = decoded.split("|")
                        if len(parts) != 6:
                            raise ValueError(f"expected 6 fields, got {len(parts)}")
                        title, url, mirror, size_mb, password, imdb_id = parts
                    except Exception as e:
                        abort(400, f"invalid payload format: {e}")

                    mirror = None if mirror == "None" else mirror

                    nzo_ids = []
                    info(f'Attempting download for "{title}"')
                    request_from = "lazylibrarian"

                    downloaded = download(
                        shared_state,
                        request_from,
                        title,
                        url,
                        mirror,
                        size_mb,
                        password or None,
                        imdb_id or None,
                    )

                    try:
                        success = downloaded["success"]
                        package_id = downloaded["package_id"]
                        title = downloaded.get("title", title)

                        if success:
                            info(f'"{title}" added successfully!')
                        else:
                            info(f'"{title}" added unsuccessfully! See log for details.')
                        nzo_ids.append(package_id)
                    except KeyError:
                        info(f'Failed to download "{title}" - no package_id returned')

                    return {
                        "status": True,
                        "nzo_ids": nzo_ids
                    }

                elif mode == "queue" or mode == "history":
                    if request.query.name and request.query.name == "delete":
                        package_id = request.query.value
                        deleted = delete_package(shared_state, package_id)
                        return {
                            "status": deleted,
                            "nzo_ids": [package_id]
                        }

                    packages = get_packages(shared_state)
                    if mode == "queue":
                        return {
                            "queue": {
                                "paused": False,
                                "slots": packages.get("queue", [])
                            }
                        }
                    elif mode == "history":
                        return {
                            "history": {
                                "paused": False,
                                "slots": packages.get("history", [])
                            }
                        }
            except Exception as e:
                info(f"Error loading packages: {e}")
                info(traceback.format_exc())
            info(f"[ERROR] Unknown download client request: {dict(request.query)}")
            return {
                "status": False
            }

        elif api_type == 'arr_indexer':
            # this builds a mock Newznab API response based on Quasarr search
            try:
                if mirror:
                    debug(f'Search will only return releases that match this mirror: "{mirror}"')

                mode = request.query.t
                request_from = request.headers.get('User-Agent')
                info("mode "+mode)

                if mode == 'caps':
                    info(f"Providing indexer capability information to {request_from}")
                    base_url = shared_state.values.get("internal_address", "http://localhost:9696")
                    caps_xml = _build_caps_xml(base_url, get_version())
                    return caps_xml
                elif mode in ['movie', 'tvsearch', 'book', 'search']:
                    releases = []

                    try:
                        offset = int(getattr(request.query, 'offset', 0))
                    except (AttributeError, ValueError):
                        offset = 0

                    if offset > 0:
                        debug(f"Ignoring offset parameter: {offset} - it leads to redundant requests")

                    else:
                        if mode == 'movie':
                            # supported params: imdbid
                            imdb_id = getattr(request.query, 'imdbid', '')

                            releases = get_search_results(shared_state, request_from,
                                                          imdb_id=imdb_id,
                                                          mirror=mirror
                                                          )

                        elif mode == 'tvsearch':
                            # supported params: imdbid, season, ep
                            imdb_id = getattr(request.query, 'imdbid', '')
                            season = getattr(request.query, 'season', None)
                            episode = getattr(request.query, 'ep', None)
                            releases = get_search_results(shared_state, request_from,
                                                          imdb_id=imdb_id,
                                                          mirror=mirror,
                                                          season=season,
                                                          episode=episode
                                                          )
                        elif mode == 'book':
                            author = getattr(request.query, 'author', '')
                            title = getattr(request.query, 'title', '')
                            search_phrase = " ".join(filter(None, [author, title]))
                            releases = get_search_results(shared_state, request_from,
                                                          search_phrase=search_phrase,
                                                          mirror=mirror
                                                          )

                        elif mode == 'search':
                            if "lazylibrarian" in request_from.lower():
                                search_phrase = getattr(request.query, 'q', '')
                                releases = get_search_results(shared_state, request_from,
                                                              search_phrase=search_phrase,
                                                              mirror=mirror
                                                              )
                            else:
                                info(
                                    f'Ignoring search request from {request_from} - only imdbid searches are supported')
                                releases = [{}]  # sonarr expects this but we will not support non-imdbid searches

                    allowed_categories = set(
                        filter(None, getattr(request.query, 'cat', '').split(','))
                    )
                    if allowed_categories:
                        before_count = len(releases)
                        releases = _filter_releases_by_categories(
                            releases,
                            allowed_categories,
                            request_from,
                            mode,
                        )
                        debug(
                            f"Filtered releases by categories {sorted(allowed_categories)}: "
                            f"{before_count} -> {len(releases)}"
                        )

                    items = ""
                    for release in releases:
                        release = release.get("details", {})

                        # Ensure clean XML output
                        title = sax_utils.escape(release.get("title", ""))
                        source = sax_utils.escape(release.get("source", ""))

                        if not "lazylibrarian" in request_from.lower():
                            title = f'[{release.get("hostname", "").upper()}] {title}'

                        items += f'''
                        <item>
                            <title>{title}</title>
                            <guid isPermaLink="True">{release.get("link", "")}</guid>
                            <link>{release.get("link", "")}</link>
                            <comments>{source}</comments>
                            <pubDate>{release.get("date", datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"))}</pubDate>
                            <enclosure url="{release.get("link", "")}" length="{release.get("size", 0)}" type="application/x-nzb" />
                        </item>'''

                    return f'''<?xml version="1.0" encoding="UTF-8"?>
                                <rss>
                                    <channel>
                                        {items}
                                    </channel>
                                </rss>'''
            except Exception as e:
                info(f"Error loading search results: {e}")
                info(traceback.format_exc())
            info(f"[ERROR] Unknown indexer request: {dict(request.query)}")
            return '''<?xml version="1.0" encoding="UTF-8"?>
                        <rss>
                            <channel>
                                <title>Quasarr Indexer</title>
                                <description>Quasarr Indexer API</description>
                                <link>https://quasarr.indexer/</link>
                            </channel>
                        </rss>'''

        info(f"[ERROR] Unknown general request: {dict(request.query)}")
        return {"error": True}
