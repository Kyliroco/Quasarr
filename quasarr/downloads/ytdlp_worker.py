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
import random
import threading
import time

from quasarr.providers.log import info, debug, error

YTDLP_TABLE = "ytdlp"
DEFAULT_OUTPUT_DIR = "/output"
MIN_INTER_JOB_DELAY = 0.8
MAX_INTER_JOB_DELAY = 5.0


def get_output_dir(shared_state):
    """Dossier de sortie yt-dlp (configurable via l'UI, défaut ``/output``)."""
    configured = shared_state.values["config"]("YTDLP").get("output_dir")
    return configured or DEFAULT_OUTPUT_DIR


def _nearest_ownership(path):
    """UID/GID du plus proche parent existant, si la plateforme le permet."""
    if not hasattr(os, "chown"):
        return None
    current = os.path.abspath(path)
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    try:
        stat = os.stat(current)
        return stat.st_uid, stat.st_gid
    except OSError:
        return None


def _apply_ownership(path, ownership):
    """Applique récursivement un UID/GID sans suivre les liens symboliques."""
    if not ownership or not hasattr(os, "chown") or not os.path.exists(path):
        return
    uid, gid = ownership
    chown = getattr(os, "lchown", os.chown)
    paths = [path]
    for root, dirs, files in os.walk(path):
        paths.extend(os.path.join(root, name) for name in dirs)
        paths.extend(os.path.join(root, name) for name in files)
    for item in paths:
        try:
            chown(item, uid, gid)
        except OSError as exc:
            debug(f'[yt-dlp] could not set ownership on "{item}": {exc}')


def _category_from_package_id(package_id):
    pid = str(package_id or "")
    if "movies" in pid:
        return "movies"
    if "docs" in pid:
        return "docs"
    return "tv"


def enqueue_job(shared_state, package_id, title, candidates, imdb_id, size_mb):
    """Crée un job persistant sans écraser un téléchargement déjà connu."""
    database = shared_state.get_db(YTDLP_TABLE)
    existing_raw = database.retrieve(package_id)
    if existing_raw:
        try:
            existing = json.loads(existing_raw)
            if existing.get("status") in {"queued", "downloading", "completed"}:
                debug(f'[yt-dlp] keeping existing {existing.get("status")} job "{title}"')
                return existing
        except (TypeError, ValueError):
            pass

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
        "added_ns": time.time_ns(),
        "candidate_index": 0,
    }
    database.update_store(package_id, json.dumps(job))
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
    return sorted(
        jobs,
        key=lambda row: (
            int(row[1].get("added_ns") or (int(row[1].get("added") or 0) * 1_000_000_000)),
            row[0],
        ),
    )


def _format_eta(seconds):
    if seconds is None or seconds < 0:
        return "23:59:59"
    seconds = int(seconds)
    return f"{seconds // 3600:02}:{(seconds % 3600) // 60:02}:{seconds % 60:02}"


class YtdlpWorker:
    def __init__(self, shared_state, poll_interval=3,
                 inter_job_delay=(MIN_INTER_JOB_DELAY, MAX_INTER_JOB_DELAY),
                 random_uniform=None, on_status_change=None):
        self.shared_state = shared_state
        self.poll_interval = max(0.1, float(poll_interval))
        # Le worker bloquant garantit un seul téléchargement actif. Les grabs
        # reçus en parallèle restent persistés dans la file avec status=queued.
        if isinstance(inter_job_delay, (int, float)):
            delay = max(0.0, float(inter_job_delay))
            self.inter_job_delay = (delay, delay)
        else:
            low, high = inter_job_delay
            low, high = max(0.0, float(low)), max(0.0, float(high))
            self.inter_job_delay = (min(low, high), max(low, high))
        self._random_uniform = random_uniform or random.uniform
        self._on_status_change = on_status_change
        self._stop = threading.Event()
        self._thread = None

    # ---------- API publique ----------

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._reset_orphans()
        self._repair_existing_ownership()
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
                    # Pause anti-blocage aléatoire uniquement si un job attend.
                    if self._next_queued():
                        delay = self._random_uniform(*self.inter_job_delay)
                        debug(f"[yt-dlp] waiting {delay:.2f}s before next queued download")
                        if self._stop.wait(delay):
                            break
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
                job["resumed"] = True
                self._save(job)

    def _next_queued(self):
        for _package_id, job in get_all_jobs(self.shared_state):
            if job.get("status") == "queued":
                return job
        return None

    def _repair_existing_ownership(self):
        """Répare au démarrage les dossiers des jobs persistés précédemment."""
        output_dir = get_output_dir(self.shared_state)
        ownership = _nearest_ownership(output_dir)
        if not ownership:
            return
        for _package_id, job in get_all_jobs(self.shared_state):
            safe_name = self.shared_state.sanitize_title(job.get("title", "")) or "download"
            folder = job.get("storage") or os.path.join(output_dir, safe_name)
            _apply_ownership(folder, ownership)

    # ---------- Persistance ----------

    def _save(self, job):
        self.shared_state.get_db(YTDLP_TABLE).update_store(
            job["package_id"], json.dumps(job)
        )

    def _notify_status_change(self, job):
        if not self._on_status_change:
            return
        try:
            self._on_status_change(dict(job))
        except Exception as exc:
            debug(f"[yt-dlp] status notification failed: {exc}")

    # ---------- Téléchargement ----------

    def _run_job(self, job):
        title = job.get("title", "download")
        job["status"] = "downloading"
        self._save(job)
        self._notify_status_change(job)

        try:
            import yt_dlp
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = f"yt-dlp not installed: {exc}"
            self._save(job)
            self._notify_status_change(job)
            error(f"[yt-dlp] cannot import yt_dlp: {exc}")
            return

        safe_name = self.shared_state.sanitize_title(title) or "download"
        output_dir = get_output_dir(self.shared_state)
        ownership = _nearest_ownership(output_dir)
        out_folder = os.path.join(output_dir, safe_name)
        try:
            os.makedirs(out_folder, exist_ok=True)
            _apply_ownership(out_folder, ownership)
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = f"cannot create output folder: {exc}"
            self._save(job)
            self._notify_status_change(job)
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
                    current_file = d.get("filename") or d.get("tmpfilename")
                    if current_file:
                        _apply_ownership(current_file, ownership)
                    self._save(job)

        ydl_opts = {
            "outtmpl": os.path.join(out_folder, safe_name + ".%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            # yt-dlp conserve le .part/.ytdl et reprend les octets/fragments
            # existants après un redémarrage de Quasarr.
            "continuedl": True,
            "nopart": False,
            "overwrites": False,
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

        from quasarr.providers.players import record_player_speed
        from quasarr.search.sources.am import _host_tag

        candidates = job.get("candidates", [])
        last_error = ""
        start_index = max(0, min(int(job.get("candidate_index") or 0), len(candidates)))
        for candidate_index in range(start_index, len(candidates)):
            link = candidates[candidate_index]
            job["candidate_index"] = candidate_index
            job["active_candidate"] = link
            self._save(job)
            info(f'[yt-dlp] ({candidate_index + 1}/{len(candidates)}) "{title}" via {link}')
            started = time.time()
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([link])
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                error(
                    f'[yt-dlp] candidate failed for "{title}" via {link}: {last_error}',
                    source="ytdlp",
                    include_traceback=False,
                )
                # Les fragments d'un lecteur ne doivent pas être repris avec
                # un autre. Tant que le processus redémarre sur le même index,
                # ils sont conservés ; ils sont supprimés seulement à l'abandon.
                job["candidate_index"] = candidate_index + 1
                job["active_candidate"] = ""
                job["bytes_total"] = 0
                job["bytes_loaded"] = 0
                job["eta"] = None
                job["percent"] = 0
                self._save(job)
                self._remove_partial_files(out_folder)
                continue

            downloaded = self._largest_file(out_folder)
            if downloaded:
                size = os.path.getsize(downloaded)
                # Sonarr ne voit le job terminé qu'après correction de tous les
                # fichiers créés par le processus Docker root.
                _apply_ownership(out_folder, ownership)
                elapsed = max(0.001, time.time() - started)
                try:
                    record_player_speed(self.shared_state, _host_tag(link), size / elapsed)
                except Exception as exc:
                    debug(f"[yt-dlp] could not record speed: {exc}")
                job["status"] = "completed"
                job["storage"] = out_folder
                job["bytes_loaded"] = size
                job["bytes_total"] = size
                job["percent"] = 100
                job["eta"] = 0
                job["error"] = ""
                job["active_candidate"] = ""
                self._save(job)
                self._notify_status_change(job)
                info(f'[yt-dlp] completed "{title}" -> {downloaded}')
                return

        job["status"] = "failed"
        job["error"] = last_error or "all embed candidates failed to download"
        _apply_ownership(out_folder, ownership)
        self._save(job)
        self._notify_status_change(job)
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

    @staticmethod
    def _remove_partial_files(folder):
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if not (name.endswith(".part") or name.endswith(".ytdl")):
                    continue
                try:
                    os.remove(os.path.join(root, name))
                except OSError:
                    pass
