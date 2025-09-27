# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337
import json
from collections import defaultdict
from urllib.parse import urlparse

from quasarr.providers.log import info, debug
from quasarr.providers.myjd_api import TokenExpiredException, RequestTimeoutException, MYJDException
from bottle import request, response

def get_links_comment_from_list(package, links_for_pkg):
    # liens déjà filtrés pour ce package
    for link in links_for_pkg:
        c = link.get("comment")
        if c:
            return c
    return None



def get_links_status_from_list(links_for_pkg):
    all_finished = True
    eta = None
    error = None

    mirrors = defaultdict(list)
    for link in links_for_pkg:
        url = link.get("url", "")
        base_domain = urlparse(url).netloc
        mirrors[base_domain].append(link)

    has_mirror_all_online = any(
        all(ln.get('availability', '').lower() == 'online' for ln in mirror_links)
        for mirror_links in mirrors.values()
    )

    offline_links = [ln for ln in links_for_pkg if ln.get('availability', '').lower() == 'offline']
    offline_ids = [ln.get('uuid') for ln in offline_links]
    offline_mirror_linkids = offline_ids if has_mirror_all_online else []

    for ln in links_for_pkg:
        if ln.get('availability', "").lower() == "offline" and not has_mirror_all_online:
            error = "Links offline for all mirrors"
        if ln.get('statusIconKey', '').lower() == "false":
            error = "File error in package"

        finished = ln.get('finished', False)
        extraction_status = (ln.get('extractionStatus') or '').lower()
        link_eta = (ln.get('eta', 0) or 0) // 1000

        if not finished:
            all_finished = False
        elif extraction_status and extraction_status != 'successful':
            if extraction_status == 'error':
                error = ln.get('status', '')
            elif extraction_status == 'running' and link_eta > 0:
                if eta is None or link_eta > eta:
                    eta = link_eta
            all_finished = False

    return {"all_finished": all_finished, "eta": eta, "error": error, "offline_mirror_linkids": offline_mirror_linkids}

def get_links_matching_package_uuid(package, package_links):
    package_uuid = package.get("uuid")
    link_ids = []

    if not isinstance(package_links, list):
        debug("Error - expected a list of package_links, got: %r" % type(package_links).__name__)
        return link_ids

    if package_uuid:
        for link in package_links:
            if link.get("packageUUID") == package_uuid:
                link_ids.append(link.get("uuid"))
    else:
        info("Error - package uuid missing in delete request!")
    return link_ids


def format_eta(seconds):
    if seconds < 0:
        return "23:59:59"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        return f"{hours:02}:{minutes:02}:{seconds:02}"


def get_packages():
    snap = request.app.config['snapshotter']         # ou: request.app.snapshotter
    downloads, _, _ = snap.get()
    return downloads



def delete_package(shared_state, package_id):
    try:
        deleted_title = ""

        packages = get_packages()
        for package_location in packages:
            for package in packages[package_location]:
                if package["nzo_id"] == package_id:
                    if package["type"] == "linkgrabber":
                        ids = get_links_matching_package_uuid(package,
                                                              shared_state.get_device().linkgrabber.query_links())
                        if ids:
                            shared_state.get_device().linkgrabber.cleanup(
                                "DELETE_ALL",
                                "REMOVE_LINKS_AND_DELETE_FILES",
                                "SELECTED",
                                ids,
                                [package["uuid"]]
                            )
                            break
                    elif package["type"] == "downloader":
                        ids = get_links_matching_package_uuid(package,
                                                              shared_state.get_device().downloads.query_links())
                        if ids:
                            shared_state.get_device().downloads.cleanup(
                                "DELETE_ALL",
                                "REMOVE_LINKS_AND_DELETE_FILES",
                                "SELECTED",
                                ids,
                                [package["uuid"]]
                            )
                            break

                    # no state check, just clean up whatever exists with the package id
                    shared_state.get_db("failed").delete(package_id)
                    shared_state.get_db("protected").delete(package_id)

                    if package_location == "queue":
                        package_name_field = "filename"
                    else:
                        package_name_field = "name"

                    try:
                        deleted_title = package[package_name_field]
                    except KeyError:
                        pass

                    # Leave the loop
                    break

        if deleted_title:
            info(f'Deleted package "{deleted_title}" with ID "{package_id}"')
        else:
            info(f'Deleted package "{package_id}"')
    except:
        info(f"Failed to delete package {package_id}")
        return False
    return True
