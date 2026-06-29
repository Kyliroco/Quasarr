import json
import os
import sys
from types import SimpleNamespace

from quasarr.downloads import _package_id
from quasarr.downloads.ytdlp_worker import (
    DEFAULT_OUTPUT_DIR,
    MAX_INTER_JOB_DELAY,
    MIN_INTER_JOB_DELAY,
    YtdlpWorker,
    _apply_ownership,
    _nearest_ownership,
    enqueue_job,
    get_all_jobs,
    get_output_dir,
)
from quasarr.search.sources import am


class MemoryDB:
    def __init__(self):
        self.rows = {}

    def retrieve(self, key):
        return self.rows.get(key)

    def retrieve_all_titles(self):
        return [[key, value] for key, value in self.rows.items()] or None

    def update_store(self, key, value):
        self.rows[key] = value
        return True


class FakeState:
    def __init__(self, output_dir=""):
        self.db = MemoryDB()
        self.output_dir = output_dir
        self.values = {
            "config": self.config,
            "dbfile": "unused.db",
            "user_agent": "Quasarr tests",
        }

    def config(self, section):
        assert section == "YTDLP"
        return SimpleNamespace(get=lambda key: self.output_dir)

    def get_db(self, table):
        assert table in {"ytdlp", "players"}
        return self.db

    @staticmethod
    def sanitize_title(title):
        return title.replace(" ", ".")


def test_default_output_and_random_delay_range():
    state = FakeState()
    worker = YtdlpWorker(state)

    assert get_output_dir(state) == DEFAULT_OUTPUT_DIR == "/output"
    assert worker.inter_job_delay == (MIN_INTER_JOB_DELAY, MAX_INTER_JOB_DELAY) == (0.8, 5.0)
    assert _package_id("tv", "Show S01E01", "https://example/video") == (
        "SABnzbd_tv_75c33c029cc120c489f5f9a3"
    )


def test_am_page_load_uses_random_jitter(monkeypatch):
    waits = []
    calls = []
    response = object()
    monkeypatch.setattr(am.random, "uniform", lambda low, high: 2.4)
    monkeypatch.setattr(am.time, "sleep", waits.append)
    monkeypatch.setattr(
        am.requests,
        "request",
        lambda method, url, **kwargs: calls.append((method, url, kwargs)) or response,
    )

    assert am._am_request("GET", "https://anime.invalid/page", timeout=10) is response
    assert waits == [2.4]
    assert calls == [("GET", "https://anime.invalid/page", {"timeout": 10})]


def test_output_tree_inherits_parent_ownership(tmp_path, monkeypatch):
    output = tmp_path / "output"
    folder = output / "Episode"
    output.mkdir()
    folder.mkdir()
    media = folder / "episode.mp4"
    media.write_bytes(b"video")
    calls = []

    monkeypatch.setattr(os, "chown", lambda path, uid, gid: calls.append((os.fspath(path), uid, gid)), raising=False)
    monkeypatch.setattr(os, "lchown", lambda path, uid, gid: calls.append((os.fspath(path), uid, gid)), raising=False)
    ownership = _nearest_ownership(output)
    _apply_ownership(folder, (568, 1000))

    assert ownership == (os.stat(output).st_uid, os.stat(output).st_gid)
    assert set(calls) == {
        (os.fspath(folder), 568, 1000),
        (os.fspath(media), 568, 1000),
    }


def test_enqueue_is_fifo_and_does_not_overwrite_active_job(monkeypatch):
    state = FakeState()
    ticks = iter([100, 200, 300])
    monkeypatch.setattr("quasarr.downloads.ytdlp_worker.time.time_ns", lambda: next(ticks))

    first = enqueue_job(state, "pkg-b", "Episode 1", ["https://one"], "tt1", 450)
    second = enqueue_job(state, "pkg-a", "Episode 2", ["https://two"], "tt1", 450)
    duplicate = enqueue_job(state, "pkg-b", "Episode 1", ["https://changed"], "tt1", 450)

    assert [package_id for package_id, _job in get_all_jobs(state)] == ["pkg-b", "pkg-a"]
    assert duplicate == first
    assert duplicate["candidates"] == ["https://one"]
    assert second["status"] == "queued"


def test_orphan_resume_keeps_candidate_and_partial_file(tmp_path, monkeypatch):
    state = FakeState(str(tmp_path))
    job = enqueue_job(
        state,
        "pkg-resume",
        "Show S01E02",
        ["https://failed.invalid/video", "https://resume.invalid/video"],
        "tt1",
        450,
    )
    job["status"] = "downloading"
    job["candidate_index"] = 1
    state.db.update_store(job["package_id"], json.dumps(job))

    statuses = []
    worker = YtdlpWorker(
        state,
        inter_job_delay=0,
        on_status_change=lambda changed: statuses.append(changed["status"]),
    )
    worker._reset_orphans()
    resumed = json.loads(state.db.retrieve("pkg-resume"))
    assert resumed["status"] == "queued"
    assert resumed["candidate_index"] == 1

    folder = tmp_path / "Show.S01E02"
    folder.mkdir()
    partial = folder / "Show.S01E02.mp4.part"
    partial.write_bytes(b"already downloaded")
    calls = []

    class FakeYoutubeDL:
        def __init__(self, options):
            calls.append(options)
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, links):
            assert links == ["https://resume.invalid/video"]
            assert partial.exists()
            final = self.options["outtmpl"].replace("%(ext)s", "mp4")
            with open(final, "wb") as stream:
                stream.write(b"complete file")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    worker._run_job(resumed)

    completed = json.loads(state.db.retrieve("pkg-resume"))
    assert completed["status"] == "completed"
    assert completed["candidate_index"] == 1
    assert completed["storage"] == os.fspath(folder)
    assert calls[0]["continuedl"] is True
    assert calls[0]["nopart"] is False
    assert calls[0]["overwrites"] is False
    assert statuses == ["downloading", "completed"]
