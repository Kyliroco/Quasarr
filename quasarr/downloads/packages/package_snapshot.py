# package_snapshot.py
# Poll toutes les 30 s les endpoints MyJDownloader une seule fois,
# construit un snapshot "downloads" et le met en cache pour réponses instantanées.

import threading
import time
from typing import Any, Dict, Optional, Tuple
from collections import defaultdict
from urllib.parse import urlparse

from quasarr.providers.log import info, debug
from quasarr.providers.myjd_api import TokenExpiredException, RequestTimeoutException, MYJDException

POLL_INTERVAL_SECONDS = 30


def _format_eta(seconds: int) -> str:
    if seconds is None or seconds < 0:
        return "23:59:59"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02}:{m:02}:{s:02}"


class PackageSnapshotter:
    def __init__(self, shared_state, interval: int = POLL_INTERVAL_SECONDS):
        self.shared_state = shared_state
        self.interval = max(5, int(interval))  # garde-fou
        self._lock = threading.RLock()
        self._snapshot: Dict[str, Any] = {"queue": [], "history": []}
        self._last_updated: float = 0.0
        self._last_error: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---------- Public API ----------

    def start(self) -> "PackageSnapshotter":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="QuasarrPackagePoller", daemon=True)
        self._thread.start()
        info(f"[Snapshotter] started with interval={self.interval}s")
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def get(self) -> Tuple[Dict[str, Any], float, Optional[str]]:
        """Réponse instantanée — retourne (snapshot, last_updated_epoch, last_error)."""
        with self._lock:
            return self._snapshot, self._last_updated, self._last_error

    def force_refresh(self) -> None:
        """Optionnel: rafraîchir à la demande (non bloquant côté requête HTTP)."""
        try:
            snapshot = self._build_snapshot()
            with self._lock:
                self._snapshot = snapshot
                self._last_updated = time.time()
                self._last_error = None
        except Exception as e:
            with self._lock:
                self._last_error = f"{type(e).__name__}: {e}"
            debug(f"[Snapshotter] force_refresh error: {e}")

    # ---------- Thread loop ----------

    def _loop(self):
        self.force_refresh()  # premier snapshot direct
        while not self._stop.is_set():
            start = time.time()
            self.force_refresh()
            elapsed = time.time() - start
            wait_time = max(0, self.interval - elapsed)
            if self._stop.wait(wait_time):
                break


    # ---------- Core snapshot builder (unique point lent) ----------

    def _build_snapshot(self) -> Dict[str, Any]:
        # 1) Lire bases locales (rapide)
        packages = []
        protected = self.shared_state.get_db("protected").retrieve_all_titles() or []
        for package_id, raw in protected:
            import json
            data = json.loads(raw)
            details = {
                "title": data["title"],
                "urls": data["links"],
                "size_mb": data["size_mb"],
                "password": data["password"],
            }
            packages.append({"details": details, "location": "queue", "type": "protected", "package_id": package_id})

        failed = self.shared_state.get_db("failed").retrieve_all_titles() or []
        for package_id, raw in failed:
            import json
            try:
                data = json.loads(raw)
                if isinstance(data, str):
                    data = json.loads(data)
            except Exception:
                data = {"title": "<unknown>", "error": "Unknown error"}
            details = {"name": data.get("title", "<unknown>"), "bytesLoaded": 0, "saveTo": "/"}
            packages.append({
                "details": details, "location": "history", "type": "failed",
                "error": data.get("error", "Unknown error"), "comment": package_id, "uuid": package_id
            })

        # 2) Snapshot MyJD (un seul aller-retour par endpoint)
        try:
            lg_pkgs = self.shared_state.get_device().linkgrabber.query_packages() or []
            lg_links = self.shared_state.get_device().linkgrabber.query_links() or []
        except (TokenExpiredException, RequestTimeoutException, MYJDException) as e:
            lg_pkgs, lg_links = [], []
            debug(f"[Snapshotter] linkgrabber query failed: {e}")

        try:
            dl_pkgs = self.shared_state.get_device().downloads.query_packages() or []
            dl_links = self.shared_state.get_device().downloads.query_links() or []
        except (TokenExpiredException, RequestTimeoutException, MYJDException) as e:
            dl_pkgs, dl_links = [], []
            debug(f"[Snapshotter] downloads query failed: {e}")

        # 3) Indexation par packageUUID (O(P+L))
        lg_links_by_pkg = defaultdict(list)
        for ln in lg_links:
            lg_links_by_pkg[ln.get("packageUUID")].append(ln)
        dl_links_by_pkg = defaultdict(list)
        for ln in dl_links:
            dl_links_by_pkg[ln.get("packageUUID")].append(ln)

        def summarize_links(links_for_pkg):
            all_finished = True
            eta = None
            error = None
            mirrors = defaultdict(list)
            for link in links_for_pkg:
                base_domain = urlparse(link.get("url", "")).netloc
                mirrors[base_domain].append(link)

            has_full_online_mirror = any(
                all(ln.get("availability", "").lower() == "online" for ln in group)
                for group in mirrors.values()
            )

            offline_ids = [ln.get("uuid") for ln in links_for_pkg if ln.get("availability", "").lower() == "offline"]
            offline_mirror_linkids = offline_ids if has_full_online_mirror else []

            for ln in links_for_pkg:
                if ln.get("availability", "").lower() == "offline" and not has_full_online_mirror:
                    error = "Links offline for all mirrors"
                if (ln.get("statusIconKey") or "").lower() == "false":
                    error = "File error in package"
                finished = ln.get("finished", False)
                extr = (ln.get("extractionStatus") or "").lower()
                link_eta = int((ln.get("eta") or 0) // 1000)
                if not finished:
                    all_finished = False
                elif extr and extr != "successful":
                    if extr == "error":
                        error = ln.get("status", "")
                    elif extr == "running" and link_eta > 0:
                        if eta is None or link_eta > eta:
                            eta = link_eta
                    all_finished = False
            return all_finished, eta, error, offline_mirror_linkids

        # 4) Linkgrabber + batch cleanup
        batch_offline_ids = []
        batch_pkg_ids = []

        for pkg in lg_pkgs:
            uuid = pkg.get("uuid")
            links = lg_links_by_pkg.get(uuid, [])
            all_finished, eta, err, offline_ids = summarize_links(links)
            if offline_ids:
                batch_offline_ids.extend(offline_ids)
                batch_pkg_ids.append(uuid)
            packages.append({
                "details": pkg, "location": ("history" if err else "queue"),
                "type": "linkgrabber", "comment": _first_comment(links), "uuid": uuid, "error": err
            })

        if batch_offline_ids and batch_pkg_ids:
            try:
                self.shared_state.get_device().linkgrabber.cleanup(
                    "DELETE_OFFLINE", "REMOVE_LINKS_ONLY", "SELECTED",
                    batch_offline_ids, batch_pkg_ids
                )
            except (TokenExpiredException, RequestTimeoutException, MYJDException) as e:
                debug(f"[Snapshotter] cleanup failed (will retry next cycle): {e}")

        # 5) Downloader
        for pkg in dl_pkgs:
            uuid = pkg.get("uuid")
            links = dl_links_by_pkg.get(uuid, [])
            all_finished, eta, err, _ = summarize_links(links)
            if not all_finished and eta:
                pkg["eta"] = eta
            packages.append({
                "details": pkg, "location": ("history" if err or all_finished else "queue"),
                "type": "downloader", "comment": _first_comment(links), "uuid": uuid, "error": err
            })

        # 6) Construire l’objet downloads (instantané)
        downloads = {"queue": [], "history": []}
        q_idx = 0
        h_idx = 0
        for pkg in packages:
            loc = pkg["location"]
            typ = pkg["type"]
            if loc == "queue":
                timeleft = "23:59:59"
                if typ == "linkgrabber":
                    d = pkg["details"]
                    name = f"[Linkgrabber] {d.get('name', '<unknown>')}"
                    mb_total = int(d.get("bytesTotal", 0)) / (1024 * 1024)
                    mb_left = mb_total
                    package_id = pkg.get("comment")
                    cat = _cat_from_id(package_id)
                    pkg_uuid = pkg.get("uuid")
                elif typ == "downloader":
                    d = pkg["details"]
                    eta = d.get("eta")
                    bytes_total = int(d.get("bytesTotal", 0))
                    bytes_loaded = int(d.get("bytesLoaded", 0))
                    mb_total = bytes_total / (1024 * 1024)
                    mb_left = max(0, (bytes_total - bytes_loaded) / (1024 * 1024)) if bytes_total else 0
                    status = "Paused" if eta is None else ("Extracting" if mb_left == 0 else "Downloading")
                    timeleft = "23:59:59" if eta is None else _format_eta(int(eta))
                    name = f"[{status}] {d.get('name', '<unknown>')}"
                    package_id = pkg.get("comment")
                    cat = _cat_from_id(package_id)
                    pkg_uuid = pkg.get("uuid")
                else:  # protected
                    d = pkg["details"]
                    name = f"[CAPTCHA not solved!] {d['title']}"
                    mb_total = mb_left = d["size_mb"]
                    package_id = pkg.get("package_id")
                    cat = _cat_from_id(package_id)
                    pkg_uuid = None

                if package_id:
                    mb_left_i = int(mb_left) if isinstance(mb_left, (int, float)) else 0
                    mb_total_i = int(mb_total) if isinstance(mb_total, (int, float)) else 0
                    pct = 0 if mb_total_i == 0 else int(100 * (mb_total_i - mb_left_i) / mb_total_i)
                    downloads["queue"].append({
                        "index": q_idx, "nzo_id": package_id, "priority": "Normal",
                        "filename": name, "cat": cat, "mbleft": mb_left_i, "mb": mb_total_i,
                        "status": "Downloading", "percentage": pct, "timeleft": timeleft,
                        "type": typ, "uuid": pkg_uuid
                    })
                    q_idx += 1

            elif loc == "history":
                d = pkg["details"]
                name = d.get("name", "<unknown>")
                size = int(d.get("bytesLoaded", 0))
                storage = d.get("saveTo", "/")
                package_id = pkg.get("comment")
                cat = _cat_from_id(package_id)
                error = pkg.get("error")
                status = "Failed" if error else "Completed"
                downloads["history"].append({
                    "fail_message": error or "", "category": cat, "storage": storage,
                    "status": status, "nzo_id": package_id, "name": name, "bytes": size,
                    "percentage": 100, "type": "downloader", "uuid": pkg.get("uuid")
                })
                h_idx += 1

        # 7) Démarrage automatique (pas de re-requête)
        try:
            if not self.shared_state.get_device().linkgrabber.is_collecting():
                packages_to_start = []
                links_to_start = []
                for pkg in lg_pkgs:
                    uuid = pkg.get("uuid")
                    comment = _first_comment(lg_links_by_pkg.get(uuid, []))
                    if comment and str(comment).startswith("Quasarr_") and uuid:
                        link_ids = [ln.get("uuid") for ln in lg_links_by_pkg.get(uuid, []) if ln.get("uuid")]
                        if link_ids:
                            packages_to_start.append(uuid)
                            links_to_start.extend(link_ids)
                if packages_to_start and links_to_start:
                    self.shared_state.get_device().linkgrabber.move_to_downloadlist(links_to_start, packages_to_start)
                    info(f"Started {len(packages_to_start)} package download"
                         f"{'s' if len(packages_to_start) > 1 else ''} from linkgrabber")
        except (TokenExpiredException, RequestTimeoutException, MYJDException) as e:
            debug(f"[Snapshotter] autostart skipped: {e}")
        info("Fin de snapshot")
        return downloads


def _first_comment(links_for_pkg):
    for ln in (links_for_pkg or []):
        c = ln.get("comment")
        if c:
            return c
    return None


def _cat_from_id(package_id: Optional[str]) -> str:
    if not package_id:
        return "not_quasarr"
    pid = str(package_id)
    if "movies" in pid:
        return "movies"
    if "docs" in pid:
        return "docs"
    return "tv"
