import json
import os
import sys
from types import SimpleNamespace

from quasarr.api.am_monitor import _job_payload, _monitor_page
from quasarr.downloads import _package_id
from quasarr.downloads.packages.package_snapshot import PackageSnapshotter
from quasarr.downloads.sources import am as download_am
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


def test_am_monitor_payload_exposes_live_metrics():
    payload = _job_payload("pkg-live", {
        "title": "Show.S01E01",
        "status": "downloading",
        "size_mb": 450,
        "bytes_loaded": 100,
        "bytes_total": 400,
        "percent": 25,
        "speed_bps": 50,
        "eta": 6,
        "active_candidate": "https://video.sibnet.ru/shell.php?videoid=1",
        "candidates": ["https://video.sibnet.ru/shell.php?videoid=1"],
    }, queue_position=None)

    assert payload["player"] == "Sibnet"
    assert payload["speed_bps"] == 50
    assert payload["bytes_loaded"] == 100
    assert payload["bytes_total"] == 400
    assert payload["percent"] == 25
    assert payload["eta"] == 6
    assert "setInterval(refreshMonitor, 1000)" in _monitor_page()


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


def test_ytdlp_status_is_published_without_full_jdownloader_snapshot():
    snapshotter = PackageSnapshotter(FakeState())
    job = {
        "package_id": "pkg-fast",
        "title": "Fast S01E01",
        "status": "downloading",
        "category": "tv",
        "size_mb": 450,
    }

    snapshotter.update_ytdlp_job(job)
    snapshot, _, _ = snapshotter.get()
    assert snapshot["queue"][0]["nzo_id"] == "pkg-fast"

    job.update(status="completed", storage="/output/Fast.S01E01", bytes_loaded=1024)
    snapshotter.update_ytdlp_job(job)
    snapshot, _, _ = snapshotter.get()
    assert snapshot["queue"] == []
    assert snapshot["history"][0]["status"] == "Completed"
    assert snapshot["history"][0]["storage"] == "/output/Fast.S01E01"

    job.update(status="failed", error="DownloadError: HTTP Error 403", storage="")
    snapshotter.update_ytdlp_job(job)
    snapshot, _, _ = snapshotter.get()
    assert snapshot["history"][0]["status"] == "Failed"
    assert snapshot["history"][0]["fail_message"] == "DownloadError: HTTP Error 403"


def test_legacy_fallback_jobs_are_migrated_to_one_player():
    state = FakeState()
    legacy = enqueue_job(state, "pkg-legacy", "Episode", ["https://one", "https://two"], "tt1", 450)
    legacy["status"] = "downloading"
    legacy["candidate_index"] = 1
    legacy["error"] = "all embed candidates failed to download"
    state.db.update_store("pkg-legacy", json.dumps(legacy))

    YtdlpWorker(state)._migrate_legacy_jobs()
    migrated = json.loads(state.db.retrieve("pkg-legacy"))

    assert migrated["candidates"] == ["https://one"]
    assert migrated["status"] == "failed"
    assert migrated["error"] == "Requested anime-sama player failed (legacy job; exact error unavailable)"


def test_requested_am_player_is_the_only_download_candidate(monkeypatch):
    response = SimpleNamespace(
        text=(
            'var eps1 = ["https://video.sibnet.ru/shell.php?videoid=1"];\n'
            'var eps2 = ["https://sendvid.com/embed/abc"];'
        ),
        url="https://anime-sama.invalid/catalogue/show/saison1/vf/episodes.js",
        raise_for_status=lambda: None,
    )
    config = SimpleNamespace(get=lambda _key: "anime-sama.invalid")
    state = SimpleNamespace(values={"config": lambda _section: config, "user_agent": "test"})
    monkeypatch.setattr(download_am, "_am_request", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(download_am, "_update_hostname", lambda *_args: "anime-sama.invalid")
    monkeypatch.setattr(download_am, "is_player_enabled", lambda *_args: True)

    links = download_am.get_am_download_links(
        state,
        "https://anime-sama.invalid/catalogue/show/saison1/vf/#episode=1&player=Sibnet",
        None,
        "Show.S01E01",
    )

    assert links == [
        "https://video.sibnet.ru/shell.php?videoid=1",
    ]


def test_iframe_rewrite_rules_are_read_from_anime_sama_player_script():
    script = r'''
    function replacePlayerHost(url) {
      return url.replace(/vidmoly\.(to|net)/g, 'vidmoly.biz');
    }
    function unrelated(text) {
      return text.replace(/foo/g, 'bar');
    }
    const proto = HTMLIFrameElement.prototype;
    Object.defineProperty(proto, 'src', {
      set: function(value) {
        const newVal = replacePlayerHost(value);
        return descriptor.set.call(this, newVal);
      }
    });
    '''

    rules = download_am._parse_iframe_rewrite_rules(script)
    rewritten = download_am._apply_rewrite_rules(
        ["https://vidmoly.to/embed-wt261mi07b0z.html"], rules
    )

    assert len(rules) == 1
    assert rewritten == ["https://vidmoly.biz/embed-wt261mi07b0z.html"]


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
