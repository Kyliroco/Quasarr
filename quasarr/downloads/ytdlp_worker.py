# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

"""Téléchargement des releases anime-sama via yt-dlp (au lieu de JDownloader).

Les jobs sont stockés dans la table SQLite ``ytdlp`` (clé = package_id, valeur =
blob JSON). Un thread de fond (``YtdlpWorker``) traite les jobs ``queued`` un par
un avec yt-dlp, met à jour la progression dans la même ligne, puis marque
``completed`` / ``failed``. ``package_snapshot`` lit cette table pour exposer la
queue/history à Sonarr/Radarr exactement comme pour les paquets JDownloader.
"""

import json
import os
import threading
import time

from quasarr.providers.log import info, debug, error

YTDLP_TABLE = "ytdlp"


def get_output_dir(shared_state):
    """Dossier de sortie yt-dlp (configurable via l'UI, défaut <config>/downloads)."""
    configured = shared_state.values["config"]("YTDLP").get("output_dir")
    if configured:
        return configured
    config_dir = os.path.dirname(shared_state.values["dbfile"])
    return os.path.join(config_dir, "downloads")


def _category_from_package_id(package_id):
    pid = str(package_id or "")
    if "movies" in pid:
        return "movies"
    if "docs" in pid:
        return "docs"
    return "tv"


def enqueue_job(shared_state, package_id, title, candidates, imdb_id, size_mb):
    """Crée (ou remplace) un job yt-dlp en attente."""
    try:
        size_mb_int = int(float(size_mb))
    except (TypeError, ValueError):
        size_mb_int = 0

    job = {
        "package_id": package_id,
        "title": title,
        "candidates": list(candidates or []),
        "imdb_id": imdb_id,
        "category": _category_from_package_id(package_id),
        "size_mb": size_mb_int,
        "status": "queued",
        "bytes_total": 0,
        "bytes_loaded": 0,
        "eta": None,
        "percent": 0,
        "storage": "",
        "error": "",
        "added": int(time.time()),
    }
    shared_state.get_db(YTDLP_TABLE).update_store(package_id, json.dumps(job))
    info(f'Queued yt-dlp download for "{title}" ({len(job["candidates"])} candidate link(s))')
    return job


def get_all_jobs(shared_state):
    """Retourne [(package_id, job_dict), ...] pour le snapshot."""
    rows = shared_state.get_db(YTDLP_TABLE).retrieve_all_titles() or []
    jobs = []
    for package_id, raw in rows:
        try:
            jobs.append((package_id, json.loads(raw)))
        except Exception:
            continue
    return jobs


def _format_eta(seconds):
    if seconds is None or seconds < 0:
        return "23:59:59"
    seconds = int(seconds)
    return f"{seconds // 3600:02}:{(seconds % 3600) // 60:02}:{seconds % 60:02}"


class YtdlpWorker:
    def __init__(self, shared_state, poll_interval=3):
        self.shared_state = shared_state
        self.poll_interval = max(1, int(poll_interval))
        self._stop = threading.Event()
        self._thread = None

    # ---------- API publique ----------

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._reset_orphans()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="QuasarrYtdlpWorker", daemon=True)
        self._thread.start()
        info("[yt-dlp] worker started")
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    # ---------- Boucle ----------

    def _loop(self):
        while not self._stop.is_set():
            try:
                job = self._next_queued()
                if job:
                    self._run_job(job)
                    continue
            except Exception as exc:
                error(f"[yt-dlp] worker loop error: {exc}")
            if self._stop.wait(self.poll_interval):
                break

    def _reset_orphans(self):
        """Au démarrage, relance les téléchargements interrompus (statut downloading)."""
        for package_id, job in get_all_jobs(self.shared_state):
            if job.get("status") == "downloading":
                job["status"] = "queued"
                self._save(job)

    def _next_queued(self):
        for _package_id, job in get_all_jobs(self.shared_state):
            if job.get("status") == "queued":
                return job
        return None

    # ---------- Persistance ----------

    def _save(self, job):
        self.shared_state.get_db(YTDLP_TABLE).update_store(
            job["package_id"], json.dumps(job)
        )

    # ---------- Téléchargement ----------

    def _run_job(self, job):
        title = job.get("title", "download")
        job["status"] = "downloading"
        self._save(job)

        try:
            import yt_dlp
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = f"yt-dlp not installed: {exc}"
            self._save(job)
            error(f"[yt-dlp] cannot import yt_dlp: {exc}")
            return

        safe_name = self.shared_state.sanitize_title(title) or "download"
        out_folder = os.path.join(get_output_dir(self.shared_state), safe_name)
        try:
            os.makedirs(out_folder, exist_ok=True)
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = f"cannot create output folder: {exc}"
            self._save(job)
            error(f'[yt-dlp] cannot create "{out_folder}": {exc}')
            return

        last_save = {"t": 0.0}

        def progress_hook(d):
            status = d.get("status")
            if status == "downloading":
                total = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
                loaded = int(d.get("downloaded_bytes") or 0)
                job["bytes_total"] = total
                job["bytes_loaded"] = loaded
                job["eta"] = d.get("eta")
                job["percent"] = int(100 * loaded / total) if total else 0
                now = time.time()
                if now - last_save["t"] > 1.5:
                    last_save["t"] = now
                    self._save(job)

        ydl_opts = {
            "outtmpl": os.path.join(out_folder, safe_name + ".%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "retries": 3,
            "fragment_retries": 3,
            "concurrent_fragment_downloads": 4,
            "merge_output_format": "mp4",
            "progress_hooks": [progress_hook],
            # On laisse les extracteurs yt-dlp gérer leur propre Referer : forcer
            # celui d'anime-sama casserait certains hébergeurs (sendvid, vidmoly…).
            "http_headers": {
                "User-Agent": self.shared_state.values.get("user_agent", ""),
            },
        }

        for index, link in enumerate(job.get("candidates", []), start=1):
            info(f'[yt-dlp] ({index}/{len(job["candidates"])}) "{title}" via {link}')
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([link])
            except Exception as exc:
                debug(f"[yt-dlp] candidate failed ({link}): {exc}")
                continue

            downloaded = self._largest_file(out_folder)
            if downloaded:
                job["status"] = "completed"
                job["storage"] = out_folder
                job["bytes_loaded"] = os.path.getsize(downloaded)
                job["bytes_total"] = job["bytes_loaded"]
                job["percent"] = 100
                job["eta"] = 0
                job["error"] = ""
                self._save(job)
                info(f'[yt-dlp] completed "{title}" -> {downloaded}')
                return

        job["status"] = "failed"
        job["error"] = "all embed candidates failed to download"
        self._save(job)
        info(f'[yt-dlp] failed "{title}" (no working candidate)')

    @staticmethod
    def _largest_file(folder):
        best = None
        best_size = -1
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.endswith(".part") or name.endswith(".ytdl"):
                    continue
                path = os.path.join(root, name)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                if size > best_size:
                    best, best_size = path, size
        return best
