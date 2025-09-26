# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import traceback
import xml.sax.saxutils as sax_utils
from base64 import urlsafe_b64decode
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse, parse_qs
from xml.etree import ElementTree

from bottle import abort, request

from quasarr.downloads import download
from quasarr.downloads import packages
from quasarr.downloads.packages import get_packages, delete_package
from quasarr.providers import shared_state
from quasarr.providers.log import info, debug
from quasarr.providers.version import get_version
from quasarr.search import get_search_results
from quasarr.storage.config import Config


def require_api_key(func):
    @wraps(func)
    def decorated(*args, **kwargs):
        api_key = Config('API').get('key')
        if not request.query.apikey:
            return abort(401, "Missing API key")
        if request.query.apikey != api_key:
            return abort(403, "Invalid API key")
        return func(*args, **kwargs)

    return decorated

from urllib.parse import urlparse, parse_qs

def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    if "p" in query and "id" in query:
        category = query["p"][0]   # ex: film, serie, anime
        item_id = query["id"][0]   # ex: 34098-alvin...
        return f"{parsed.scheme}://{parsed.netloc}/{category}/{item_id}"
    
    return url

def setup_arr_routes(app):
    @app.get('/download/')
    def fake_nzb_file():
        payload = request.query.payload
        decoded_payload = urlsafe_b64decode(payload).decode("utf-8").split("|")
        if len(decoded_payload) >= 5 :
            title = decoded_payload[0]
            url = decoded_payload[1]
            if "zone-telechargement" in url:
                url =normalize_url(url=url)
            mirror = decoded_payload[2]
            size_mb = decoded_payload[3]
            if len(decoded_payload) == 5:
                imdb_id = decoded_payload[4]
                info(f'<nzb><file title="{title}" url="{url}" mirror="{mirror}" size_mb="{size_mb}" password="password" imdb_id="{imdb_id}"/></nzb>')
                return f'''<nzb>
                    <file title="{title}"
                        url="{url}"
                        mirror="{mirror}" size_mb="{size_mb}" password="password" imdb_id="{imdb_id}" />
                </nzb>'''
            elif (len(decoded_payload) == 6):
                password = decoded_payload[4]
                imdb_id = decoded_payload[5]
                info(f'<nzb><file title="{title}" url="{url}" mirror="{mirror}" size_mb="{size_mb}" password="{password}" imdb_id="{imdb_id}"/></nzb>')
                return f'<nzb><file title="{title}" url="{url}" mirror="{mirror}" size_mb="{size_mb}" password="{password}" imdb_id="{imdb_id}"/></nzb>'
            else:
                raise Exception("Le playload ne contient pas le bon nombre de paramètrès")

        else:
                raise Exception("Le playload ne contient pas le bon nombre de paramètrès")

    @app.post('/api')
    @require_api_key
    def download_fake_nzb_file():
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
        info("nzo_ids "+str(nzo_ids))
        return {
            "status": True,
            "nzo_ids": nzo_ids
        }

    @app.get('/api')
    @app.get('/api/<mirror>')
    @require_api_key
    def quasarr_api(mirror=None):
        info("api")

        api_type = 'arr_download_client' if request.query.mode else 'arr_indexer' if request.query.t else None

        if api_type == 'arr_download_client':
            # This builds a mock SABnzbd API response based on the My JDownloader integration
            try:
                mode = request.query.mode
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
                            "movies",
                            "tv",
                            "docs"
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
                                    "name": "movies",
                                    "order": 1,
                                    "dir": "",
                                },
                                {
                                    "name": "tv",
                                    "order": 2,
                                    "dir": "",
                                },
                                {
                                    "name": "docs",
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
                    info("addurl")
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
                    info("queue")
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
                            # "queue": {
                            #     "status": "Downloading",
                            #     "speedlimit": "9",
                            #     "speedlimit_abs": "4718592.0",
                            #     "paused": "false",
                            #     "noofslots_total": 2,
                            #     "noofslots": 2,
                            #     "limit": 10,
                            #     "start": 0,
                            #     "timeleft": "0:16:44",
                            #     "speed": "1.3 M",
                            #     "kbpersec": "1296.02",
                            #     "size": "1.2 GB",
                            #     "sizeleft": "1.2 GB",
                            #     "mb": "1277.65",
                            #     "mbleft": "1271.58",
                            #     "slots": [
                            #         {
                            #             "status": "Downloading",
                            #             "index": 0,
                            #             "password": "",
                            #             "avg_age": "2895d",
                            #             "time_added": 1469172000,
                            #             "script": "None",
                            #             "direct_unpack": "10/30",
                            #             "mb": "1277.65",
                            #             "mbleft": "1271.59",
                            #             "mbmissing": "0.0",
                            #             "size": "1.2 GB",
                            #             "sizeleft": "1.2 GB",
                            #             "filename": "Maison.de.Retraite.2.2024.FRENCH.1080p.WEB.H264-FW.mkv.6.Go",
                            #             "labels": [],
                            #             "priority": "Normal",
                            #             "cat": "movies",
                            #             "timeleft": "0:16:44",
                            #             "percentage": "0",
                            #             "nzo_id": "SABnzbd_nzo_5097382902943060034",
                            #             "unpackopts": "3"
                            #         },
                            #         {
                            #             "status": "Paused",
                            #             "index": 1,
                            #             "password": "",
                            #             "avg_age": "2895d",
                            #             "time_added": 1469171000,
                            #             "script": "None",
                            #             "direct_unpack": "null",
                            #             "mb": "1277.76",
                            #             "mbleft": "1277.76",
                            #             "mbmissing": "0.0",
                            #             "size": "1.2 GB",
                            #             "sizeleft": "1.2 GB",
                            #             "filename": "TV.Show.S04E12.720p.HDTV.x264",
                            #             "labels": [
                            #                 "TOO LARGE",
                            #                 "DUPLICATE"
                            #             ],
                            #             "priority": "Normal",
                            #             "cat": "movies",
                            #             "timeleft": "0:00:00",
                            #             "percentage": "0",
                            #             "nzo_id": "SABnzbd_nzo_7633437793811053511",
                            #             "unpackopts": "3"
                            #         }
                            #     ],
                            #     "diskspace1": "161.16",
                            #     "diskspace2": "161.16",
                            #     "diskspacetotal1": "465.21",
                            #     "diskspacetotal2": "465.21",
                            #     "diskspace1_norm": "161.2 G",
                            #     "diskspace2_norm": "161.2 G",
                            #     "have_warnings": "0",
                            #     "pause_int": "0",
                            #     "left_quota": "0 ",
                            #     "version": "3.x.x",
                            #     "finish": 2,
                            #     "cache_art": "16",
                            #     "cache_size": "6 MB",
                            #     "finishaction": "null",
                            #     "paused_all": "false",
                            #     "quota": "0 ",
                            #     "have_quota": "false",
                            # }
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

                if mode == 'caps':
                    info(f"Providing indexer capability information to {request_from}")
                    return '''<?xml version="1.0" encoding="UTF-8"?>
                                <caps>
                                  <server 
                                    version="1.33.7" 
                                    title="Quasarr" 
                                    url="https://quasarr.indexer/" 
                                    email="support@quasarr.indexer" 
                                  />
                                  <limits max="9999" default="9999" />
                                  <registration available="no" open="no" />
                                  <searching>
                                    <search available="yes" supportedParams="q" />
                                    <tv-search available="yes" supportedParams="imdbid,season,ep" />
                                    <movie-search available="yes" supportedParams="imdbid" />
                                  </searching>
                                  <categories>
                                    <category id="5000" name="TV" />
                                    <category id="2000" name="Movies" />
                                    <category id="7000" name="Books">
                                  </category>
                                  </categories>
                                </caps>'''
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

                    items = ""
                    for release in releases:
                        release = release.get("details", {})

                        # Ensure clean XML output
                        title = sax_utils.escape(release.get("title", ""))
                        source = sax_utils.escape(release.get("source", ""))

                        if not "lazylibrarian" in request_from.lower():
                            # title = f'[{release.get("hostname", "").upper()}] {title}'
                            title = f'{title}'

                        items += f'''
                        <item>
                            <title>{title}</title>
                            <guid isPermaLink="True">{release.get("link", "")}</guid>
                            <link>{release.get("link", "")}</link>
                            <comments>{source}</comments>
                            <pubDate>{release.get("date", datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"))}</pubDate>
                            <enclosure url="{release.get("link", "")}" length="{release.get("size", 0)}" type="application/x-nzb" />
                        </item>'''
                    #     items += f'''
                    #                         <item>
                    #             <title>Alvin.and.the.Chipmunks.3.2011.DUTCH.1080p.BluRay.x264-BLUEYES</title>
                    #             <guid isPermaLink="true">https://nzbgeek.info/geekseek.php?guid=cd2a2d5973007f132d3cf61c2865f58f</guid>
                    #             <link>https://nzbgeek.info/geekseek.php?guid=cd2a2d5973007f132d3cf61c2865f58f</link>
                    #             <comments>https://nzbgeek.info/geekseek.php?guid=cd2a2d5973007f132d3cf61c2865f58f</comments>
                    #             <pubDate>Sun, 06 Jul 2025 05:33:42 +0000</pubDate>
                    #             <category>Movies > Foreign</category>
                    #             <description>
                    #                 <![CDATA[<div><table style="width:100%;"><tr><td width="100%"><ul><li>ID: cd2a2d5973007f132d3cf61c2865f58f</li><li>Name: <a href="https://nzbgeek.info/geekseek.php?guid=cd2a2d5973007f132d3cf61c2865f58f">Alvin.and.the.Chipmunks.3.2011.DUTCH.1080p.BluRay.x264-BLUEYES</a></li><li>Size: 4.9 GB </li><li>Attributes: Category: <a href="https://nzbgeek.info/geekseek.php?c=2010">Movies > Foreign</a></li><li>PostDate: Sun, 06 Jul 2025 05:33:42 +0000</li><li>Imdb Info<ul><li>IMDB Link: <a href="http://www.imdb.com/title/tt00952640">Alvin and the Chipmunks</a></li><li>Rating: <span style="font-weight:bold; color:#FFAA2A;">5.2</span></li><li>Plot: In a tree farm, three musically inclined chipmunks, Alvin, Simon and Theodore, find their tree cut down and sent to Los Angeles Once there, they meet the frustrated songwriter David Seville, and despite a poor house wrecking first impression, they impress him with their singing talent Seeing the opportunity for success, both human and chipmunks make a pact for them to sing his songs While that ambition proves a frustrating struggle with the difficult trio, the dream does come true after all However, that success presents its own trials as their unscrupulous record executive, Ian Hawke, plans to break up this family to exploit the boys Can Dave and the Chipmunks discover what they really value amid the superficial glamor around them</li><li>Year: 2007</li><li>Genre: Animation, Adventure, Comedy, Family, Fantasy, Music</li><li>Director: Tim Hill</li><li>Actors: Jason Lee, David Cross, Cameron Richardson, Jane Lynch</li><li>Runtime: 92 min</li></ul></li><br><font size="2" face="Verdana" color="#999999">10</font>&nbsp;<img height="15" src="https://api.nzbgeek.info/covers/grabs.png">&nbsp;&nbsp;<font size="2" face="Verdana" color="#0000FF">0</font>&nbsp;<img height="15" src="https://api.nzbgeek.info/covers/comments.png">&nbsp;&nbsp;<font size="2" face="Verdana" color="#008000">0</font>&nbsp;<img height="15" src="https://api.nzbgeek.info/covers/thumbup.png">&nbsp;&nbsp;<font size="2" face="Verdana" color="#FF0000">0</font>&nbsp;<img height="15" src="https://api.nzbgeek.info/covers/thumbdown.png">&nbsp;&nbsp;</ul></td><td width="120px" align="right" valign="top"><img style="margin-left:10px;margin-bottom:10px;float:right;" src="https://api.nzbgeek.info/covers/movies/00952640-cover.jpg" width="120" border="0" alt="Alvin and the Chipmunks" /></td></tr></table></div><div style="clear:both;">]]>
                    # </description>
                    # <enclosure url="https://api.nzbgeek.info/api?t=get&amp;id=cd2a2d5973007f132d3cf61c2865f58f&amp;apikey=PPGgSu65YDQ7Thsa5Jtu6Gj5PV3Ai1DL" length="5264594000" type="application/x-nzb"/>
                    # <newznab:attr name="category" value="2000"/>
                    # <newznab:attr name="category" value="2010"/>
                    # <newznab:attr name="size" value="5264594000"/>
                    # <newznab:attr name="guid" value="cd2a2d5973007f132d3cf61c2865f58f"/>
                    # <newznab:attr name="imdbtitle" value="Alvin and the Chipmunks"/>
                    # <newznab:attr name="imdb" value="00952640"/>
                    # <newznab:attr name="imdbtagline" value="The Original Entourage"/>
                    # <newznab:attr name="imdbplot" value="In a tree farm, three musically inclined chipmunks, Alvin, Simon and Theodore, find their tree cut down and sent to Los Angeles Once there, they meet the frustrated songwriter David Seville, and despite a poor house wrecking first impression, they impress him with their singing talent Seeing the opportunity for success, both human and chipmunks make a pact for them to sing his songs While that ambition proves a frustrating struggle with the difficult trio, the dream does come true after all However, that success presents its own trials as their unscrupulous record executive, Ian Hawke, plans to break up this family to exploit the boys Can Dave and the Chipmunks discover what they really value amid the superficial glamor around them"/>
                    # <newznab:attr name="imdbscore" value="5.2"/>
                    # <newznab:attr name="genre" value="Animation, Adventure, Comedy, Family, Fantasy, Music"/>
                    # <newznab:attr name="imdbyear" value="2007"/>
                    # <newznab:attr name="imdbdirector" value="Tim Hill"/>
                    # <newznab:attr name="imdbactors" value="Jason Lee, David Cross, Cameron Richardson, Jane Lynch"/>
                    # <newznab:attr name="coverurl" value="https://api.nzbgeek.info/covers/movies/00952640-cover.jpg"/>
                    # <newznab:attr name="runtime" value="92 min"/>
                    # <newznab:attr name="language" value="Dutch"/>
                    # <newznab:attr name="grabs" value="10"/>
                    # <newznab:attr name="comments" value="0"/>
                    # <newznab:attr name="password" value="0"/>
                    # <newznab:attr name="usenetdate" value="Sun, 06 Jul 2025 05:33:42 +0000"/>
                    # <newznab:attr name="thumbsup" value="0"/>
                    # <newznab:attr name="thumbsdown" value="0"/>
                    # </item>
                    # '''

                    return f'''<?xml version="1.0" encoding="UTF-8"?>
                                <rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
                                    <channel>
                                    <title>Quasarr Demo Indexer</title>
                                    <description>Flux demo Newznab/Torznab pour Radarr</description>
                                    <language>fr-fr</language>
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
