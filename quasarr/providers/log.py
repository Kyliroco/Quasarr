# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import datetime
import os
import threading
import traceback
from collections import deque

# ---------------------------------------------------------------------------
# Ring-buffer that stores the last N structured log entries in memory.
# Thread-safe so it can be used from the Bottle request threads,
# the JDownloader process relay, and any background workers.
# ---------------------------------------------------------------------------

_MAX_ENTRIES = 500
_buffer_lock = threading.Lock()
_log_buffer: deque = deque(maxlen=_MAX_ENTRIES)
_event_id_counter = 0


def _next_id():
    global _event_id_counter
    _event_id_counter += 1
    return _event_id_counter


def timestamp():
    return datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")


def _now_iso():
    return datetime.datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Core internal logger – every public helper funnels through here.
# ---------------------------------------------------------------------------

def _emit(level: str, message: str, source: str = "", **extra):
    """Write a log entry to stdout and to the in-memory ring buffer.

    Parameters
    ----------
    level : str
        One of "DEBUG", "INFO", "WARNING", "ERROR".
    message : str
        Human-readable log line.
    source : str, optional
        Module or subsystem that produced the entry (e.g. "zt", "api", "download").
    **extra
        Arbitrary structured data attached to the entry.  Anything JSON-serialisable
        is fine – titles, URLs, payloads, filter reasons, etc.
    """
    ts = _now_iso()
    entry = {
        "id": _next_id(),
        "timestamp": ts,
        "level": level,
        "source": source,
        "message": message,
    }
    if extra:
        entry["data"] = {k: v for k, v in extra.items() if v is not None}

    with _buffer_lock:
        _log_buffer.append(entry)

    # Always print to stdout for docker logs / terminal visibility
    prefix = f"[{ts.split('T')[0]} {ts.split('T')[1][:8]}]"
    tag = f" [{source.upper()}]" if source else ""
    level_tag = f" {level}" if level not in ("INFO",) else ""
    print(f"{prefix}{tag}{level_tag} {message}", flush=True)


# ---------------------------------------------------------------------------
# Public helpers – drop-in replacements for the old info() / debug().
# ---------------------------------------------------------------------------

def info(message: str, source: str = "", **extra):
    _emit("INFO", message, source=source, **extra)


def debug(message: str, source: str = "", **extra):
    if os.getenv("DEBUG"):
        _emit("DEBUG", message, source=source, **extra)


def warning(message: str, source: str = "", **extra):
    _emit("WARNING", message, source=source, **extra)


def error(message: str, source: str = "", include_traceback: bool = True, **extra):
    if include_traceback:
        tb = traceback.format_exc()
        if tb and tb.strip() != "NoneType: None":
            extra["traceback"] = tb
    _emit("ERROR", message, source=source, **extra)


# ---------------------------------------------------------------------------
# Structured event logger – for key decision points (filtering, title
# construction, payload building, etc.) that you want to inspect later
# in the debug dashboard.
# ---------------------------------------------------------------------------

def log_event(event_type: str, source: str = "", level: str = "DEBUG", **data):
    """Log a structured event with typed data.

    This produces both a human-readable stdout line *and* a rich entry in the
    ring buffer.  Use it at key decision points so the debug dashboard can
    display exactly what happened and why.

    Examples
    --------
    >>> log_event("release_filtered", source="zt",
    ...           title="My.Movie.2024", reason="episode 3 not found")
    >>> log_event("payload_built", source="zt",
    ...           title="My.Movie.2024.1080p", payload_decoded="title|url|mirror|5000|tt123")
    >>> log_event("api_request", source="api",
    ...           method="tvsearch", requester="Sonarr", imdb_id="tt123", season="1", episode="3")
    """
    msg_parts = [event_type]
    for key, value in data.items():
        if value is not None:
            msg_parts.append(f"{key}={value}")
    message = " | ".join(msg_parts)

    if level == "DEBUG" and not os.getenv("DEBUG"):
        # Still store in buffer even without DEBUG so the dashboard can show it
        ts = _now_iso()
        entry = {
            "id": _next_id(),
            "timestamp": ts,
            "level": "DEBUG",
            "source": source,
            "message": message,
            "event_type": event_type,
            "data": {k: v for k, v in data.items() if v is not None},
        }
        with _buffer_lock:
            _log_buffer.append(entry)
        return

    _emit(level, message, source=source, event_type=event_type, **data)


# ---------------------------------------------------------------------------
# Buffer access for the debug dashboard / API.
# ---------------------------------------------------------------------------

def get_log_entries(limit: int = 200,
                    level: str = None,
                    source: str = None,
                    search: str = None,
                    since_id: int = 0):
    """Return recent log entries, newest first.

    Parameters
    ----------
    limit : int
        Maximum number of entries to return.
    level : str, optional
        Filter by level (e.g. "ERROR", "WARNING").
    source : str, optional
        Filter by source substring (case-insensitive).
    search : str, optional
        Free-text search across message + data values (case-insensitive).
    since_id : int
        Only return entries whose id is strictly greater than this value.
        Useful for incremental polling from the dashboard.
    """
    with _buffer_lock:
        snapshot = list(_log_buffer)

    if since_id:
        snapshot = [e for e in snapshot if e["id"] > since_id]

    if level:
        level_upper = level.upper()
        snapshot = [e for e in snapshot if e["level"] == level_upper]

    if source:
        source_lower = source.lower()
        snapshot = [e for e in snapshot if source_lower in (e.get("source") or "").lower()]

    if search:
        search_lower = search.lower()

        def _matches(entry):
            if search_lower in entry.get("message", "").lower():
                return True
            for v in (entry.get("data") or {}).values():
                if search_lower in str(v).lower():
                    return True
            return False

        snapshot = [e for e in snapshot if _matches(e)]

    # Newest first
    snapshot.reverse()
    return snapshot[:limit]


def get_log_stats():
    """Return summary counters for the dashboard header."""
    with _buffer_lock:
        snapshot = list(_log_buffer)

    stats = {
        "total": len(snapshot),
        "debug": 0,
        "info": 0,
        "warning": 0,
        "error": 0,
        "searches": 0,
        "results_found": 0,
        "results_filtered": 0,
        "downloads": 0,
    }
    for entry in snapshot:
        lvl = entry.get("level", "").lower()
        if lvl in stats:
            stats[lvl] += 1

        event_type = entry.get("event_type") or (entry.get("data") or {}).get("event_type")
        if event_type == "search_request":
            stats["searches"] += 1
        elif event_type == "release_accepted":
            stats["results_found"] += 1
        elif event_type == "release_filtered":
            stats["results_filtered"] += 1
        elif event_type in ("download_request", "download_attempt"):
            stats["downloads"] += 1

    return stats
