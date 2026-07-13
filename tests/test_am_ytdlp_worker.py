import json
import os
import sys
from types import SimpleNamespace

from quasarr.api.am_monitor import (
    _job_payload,
    _monitor_page,
    get_sonarr_responses,
    record_sonarr_response,
)
from quasarr.downloads import _package_id, _am_package_id
from quasarr.downloads.packages.package_snapshot import PackageSnapshotter, public_download_slots
from quasarr.downloads.sources import am as download_am
from quasarr.downloads.ytdlp_worker import (
    DEFAULT_OUTPUT_DIR,
    MAX_INTER_JOB_DELAY,
    MIN_INTER_JOB_DELAY,
    RATE_LIMIT_BACKOFF_SECONDS,
    RATE_LIMIT_MAX_RETRIES,
    YtdlpWorker,
    _apply_ownership,
    _completed_output_exists,
    _is_rate_limited,
    _nearest_ownership,
    enqueue_job,
    get_all_jobs,
    get_output_dir,
    get_rate_limit_backoff_seconds,
    get_rate_limit_max_retries,
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

    def delete(self, key):
        self.rows.pop(key, None)
        return True


class FakeState:
    def __init__(self, output_dir=""):
        self.db = MemoryDB()
        self.dbs = {"ytdlp": self.db}
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
        assert table in {"ytdlp", "players", "failed", "protected"}
        # "ytdlp" garde la même instance que self.db (utilisée par les tests) ;
        # les autres tables ont leur propre stockage isolé.
        return self.dbs.setdefault(table, self.db if table == "ytdlp" else MemoryDB())

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
    page = _monitor_page()
    assert "setInterval(refreshMonitor, 1000)" in page
    assert "Open download link" in page
    assert 'rel="noopener noreferrer"' in page
    assert "Last responses sent to Sonarr" in page
    assert 'id="sonarr-queue-payload"' in page
    assert 'id="sonarr-history-payload"' in page

    legacy_failed = _job_payload("pkg-failed", {
        "title": "Show.S01E02",
        "status": "failed",
        "candidate_index": 1,
        "candidates": ["https://vidmoly.to/embed-demo.html"],
    })
    assert legacy_failed["candidate"] == "https://vidmoly.to/embed-demo.html"


def test_sonarr_response_monitor_keeps_exact_queue_and_history_payloads():
    app = SimpleNamespace(config={})
    queue = {"queue": {"paused": False, "slots": [{"nzo_id": "pkg-1"}]}}
    history = {"history": {"paused": False, "slots": [{"status": "Failed"}]}}

    record_sonarr_response(app, "queue", queue, "Sonarr/4.0")
    record_sonarr_response(app, "history", history, "Sonarr/4.0")
    queue["queue"]["slots"].clear()

    captured = get_sonarr_responses(app)
    assert captured["queue"]["payload"]["queue"]["slots"] == [{"nzo_id": "pkg-1"}]
    assert captured["history"]["payload"] == history
    assert captured["queue"]["requester"] == "Sonarr/4.0"


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


def test_episode_label_parser_accepts_only_exact_episode_number():
    assert am._strict_episode_number("EPISODE 8") == 8
    assert am._strict_episode_number(" episode 12 ") == 12
    assert am._strict_episode_number("EPISODE RÉCAPITULATIF") is None
    assert am._strict_episode_number("EPISODE 8 RÉCAPITULATIF") is None
    assert am._strict_episode_number("RÉCAPITULATIF EPISODE 8") is None
    assert am._strict_episode_number("EPISODE 8.5") is None
    assert am._strict_episode_number("8") is None


def test_release_title_inserts_year_to_defeat_homonym_alias():
    # « Island » (anime, TVDB 346799) partage son nom avec un drama coréen
    # (TVDB 397727) dont l'alias de scene mapping détournait la release. En
    # plaçant l'année juste après le titre, Sonarr lit un CleanTitle
    # « island2018 » : aucune clé de scene mapping ne correspond, le mapping est
    # court-circuité et la release retombe sur la bonne série.
    assert am._release_title(
        "Island", 2018, "S01E01", "VOSTFR.1080p.WEB.x264-ANIMESAMA", "Sendvid"
    ) == "Island.2018.S01E01.VOSTFR.1080p.WEB.x264-ANIMESAMA.Sendvid"


def test_release_title_year_optional_for_films_and_missing_metadata():
    # Film : pas de tag SxxExx, mais l'année reste insérée.
    assert am._release_title("Island", 2018, None, "VOSTFR.1080p", "Sendvid") \
        == "Island.2018.VOSTFR.1080p.Sendvid"
    # Année introuvable (TMDB muet) : on conserve l'ancien format sans millésime.
    assert am._release_title("Island", None, "S01E01", "VOSTFR.1080p", "Sendvid") \
        == "Island.S01E01.VOSTFR.1080p.Sendvid"


def test_get_year_reads_tmdb_air_or_release_date(monkeypatch):
    from quasarr.providers import imdb_metadata

    monkeypatch.setattr(imdb_metadata, "_tmdb_find",
                        lambda imdb_id, language='fr-FR': ({"first_air_date": "2018-07-06"}, "tv"))
    assert imdb_metadata.get_year(None, "tt8737996") == 2018

    monkeypatch.setattr(imdb_metadata, "_tmdb_find",
                        lambda imdb_id, language='fr-FR': ({"release_date": "1999-03-31"}, "movie"))
    assert imdb_metadata.get_year(None, "tt0000001") == 1999

    # Date absente ou résultat vide -> None (le titre reste sans année).
    monkeypatch.setattr(imdb_metadata, "_tmdb_find",
                        lambda imdb_id, language='fr-FR': ({"first_air_date": ""}, "tv"))
    assert imdb_metadata.get_year(None, "tt0000002") is None

    monkeypatch.setattr(imdb_metadata, "_tmdb_find",
                        lambda imdb_id, language='fr-FR': (None, None))
    assert imdb_metadata.get_year(None, "tt0000003") is None


def test_fairy_tail_hors_serie_folder_does_not_block_absolute_fallback():
    # Layout réel d'anime-sama pour Fairy Tail : une seule saison numérotée
    # (« saison1 », qui contient les 328 épisodes en absolu) plus des dossiers
    # annexes, dont « saison1hs » (100 Years Quest). Ce dernier commence par
    # « saison » mais n'est PAS une saison numérotée : il ne doit pas empêcher
    # le repli vers le dossier unique pour une saison Sonarr absente.
    declarations = [
        ("Saison 1", "saison1/vostfr"),
        ("Film", "film/vostfr"),
        ("OAV", "oav/vostfr"),
        ("100 Years Quest Saison 1", "saison1hs/vostfr"),
        ("Kai", "kai/vostfr"),
    ]

    # Saison présente telle quelle -> dossier exact.
    assert am._season_path_for_language(declarations, False, 1, "vostfr") == "saison1/vostfr"
    # Saison 5 (Sonarr) absente comme dossier -> repli sur l'unique saison
    # numérotée ; la conversion S/E -> absolu fera le reste.
    assert am._season_path_for_language(declarations, False, 5, "vostfr") == "saison1/vostfr"
    assert am._select_season_path(declarations, False, 5) == ("saison1/vostfr", "vostfr")


def test_multiple_real_seasons_do_not_fall_back_to_wrong_folder():
    # Quand anime-sama expose bien plusieurs saisons numérotées, une saison
    # demandée mais absente ne doit PAS être devinée (une autre langue peut
    # l'avoir) : on ne retombe sur le dossier unique que s'il n'y en a qu'un.
    declarations = [
        ("Saison 1", "saison1/vostfr"),
        ("Saison 2", "saison2/vostfr"),
    ]
    assert am._season_path_for_language(declarations, False, 2, "vostfr") == "saison2/vostfr"
    assert am._season_path_for_language(declarations, False, 5, "vostfr") is None


def test_empty_player_entry_keeps_later_episode_indexes_stable():
    eps = am._parse_episodes_js(
        "var eps1 = ['https://video/episode-1', '', 'https://video/episode-3'];"
    )

    assert eps["eps1"] == [
        "https://video/episode-1",
        "",
        "https://video/episode-3",
    ]
    assert am._candidates_for_index(eps, 1) == []
    assert am._candidates_for_index(eps, 2) == ["https://video/episode-3"]


def test_solo_leveling_recap_consumes_index_without_becoming_episode_8():
    page = '''
      <script>resetListe(); finirListe(1);</script>
      <script>
        resetListe();
        creerListe(1, 7); newSP("Récapitulatif");
        finirListe(8);
      </script>
    '''

    mapping = am._parse_episode_index_map(page, total_items=13)

    assert mapping == {
        1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6,
        8: 8, 9: 9, 10: 10, 11: 11, 12: 12,
    }
    assert 7 not in mapping.values()  # index 7 = épisode récapitulatif


def test_commented_episode_list_example_cannot_replace_real_configuration():
    page = '''
      <script>
        resetListe();
        finirListe(1);
      </script>
      <!--
        template:
        resetListe();
        creerListe(debut, fin); newSP(special);
        finirListe(debut de la fin);
      -->
      <script>
        /*
          resetListe();
          creerListe(debut, fin);
          finirListe(debut de la fin);
        */
      </script>
    '''

    assert am._parse_episode_index_map(page, total_items=4) == {
        1: 0, 2: 1, 3: 2, 4: 3,
    }


def test_imdb_confirmed_episode_overflows_into_next_anime_sama_folder(monkeypatch):
    first_eps = {"eps1": [f"https://first/{number}" for number in range(1, 13)]}
    second_eps = {"eps1": [f"https://second/{number}" for number in range(1, 13)]}
    indices = {number: number - 1 for number in range(1, 13)}
    declarations = [
        ("Saison 1", "saison1/vostfr"),
        ("Saison 2", "saison2/vostfr"),
    ]
    monkeypatch.setattr(
        am,
        "_absolute_season_plan",
        lambda _state, imdb_id, season: (
            [(number, number) for number in range(1, 25)]
            if (imdb_id, season) == ("tt5095466", 1) else []
        ),
    )
    monkeypatch.setattr(
        am,
        "_fetch_episodes",
        lambda *_args: (second_eps, indices, "anime-sama.to"),
    )

    resolved = am._find_overflow_episode_source(
        object(), "tt5095466", 1, 13,
        declarations, "vostfr", "saison1/vostfr",
        first_eps, indices, "anime-sama.to", "gakusen-toshi-asterisk", {},
    )

    path, eps_map, episode_indices, local_episode, host = resolved
    assert path == "saison2/vostfr"
    assert eps_map == second_eps
    assert episode_indices == indices
    assert local_episode == 1
    assert host == "anime-sama.to"

    resolved_last = am._find_overflow_episode_source(
        object(), "tt5095466", 1, 24,
        declarations, "vostfr", "saison1/vostfr",
        first_eps, indices, "anime-sama.to", "gakusen-toshi-asterisk", {},
    )
    assert resolved_last[0] == "saison2/vostfr"
    assert resolved_last[3] == 12


def test_overflow_is_rejected_when_imdb_metadata_has_no_requested_episode(monkeypatch):
    monkeypatch.setattr(am, "_absolute_season_plan", lambda *_args: [(number, number) for number in range(1, 13)])

    assert am._find_overflow_episode_source(
        object(), "tt-example", 1, 13,
        [("Saison 1", "saison1/vostfr"), ("Saison 2", "saison2/vostfr")],
        "vostfr", "saison1/vostfr",
        {"eps1": ["url"] * 12}, {number: number - 1 for number in range(1, 13)},
        "anime-sama.to", "show", {},
    ) is None


# Layout réel de Fire Force sur anime-sama : la saison 3 de TheTVDB (25 ép.) est
# scindée en deux dossiers « Partie 1 » (saison3, 12 ép.) et « Partie 2 »
# (saison3-2, 13 ép.). saison1hs = hors-série (œuvre distincte), jamais une suite.
_FIRE_FORCE_DECLS = [
    ("Saison 1", "saison1/vostfr"),
    ("Saison 2", "saison2/vostfr"),
    ("Saison 3 Partie 1", "saison3/vostfr"),
    ("Saison 3 Partie 2", "saison3-2/vostfr"),
]


def test_split_cour_part_folder_is_ordered_after_its_first_part():
    # saison3-2 doit suivre saison3 (et non être ignorée) ; un hors-série reste exclu.
    decls = _FIRE_FORCE_DECLS + [("100 Years HS", "saison3hs/vostfr")]
    assert am._numbered_season_paths(decls, "vostfr") == [
        "saison1/vostfr", "saison2/vostfr", "saison3/vostfr", "saison3-2/vostfr",
    ]
    # La sélection de saison trouve toujours la Partie 1 pour la saison 3.
    assert am._season_path_for_language(_FIRE_FORCE_DECLS, False, 3, "vostfr") == "saison3/vostfr"


def test_fire_force_s3e13_overflows_into_part_two_folder(monkeypatch):
    # S3E13 (Sonarr/TheTVDB) = 1er épisode de la Partie 2 : 12 ép. en saison3,
    # puis débordement dans saison3-2 à l'épisode 1.
    part_two_eps = {"eps2": [f"https://vidmoly.to/embed-{n}.html" for n in range(1, 14)]}
    part_two_indices = {number: number - 1 for number in range(1, 14)}

    monkeypatch.setattr(am, "_absolute_season_plan",
                        lambda _state, imdb_id, season:
                        [(n, n) for n in range(1, 26)] if season == 3 else [])
    monkeypatch.setattr(am, "_fetch_episodes",
                        lambda *_args: (part_two_eps, part_two_indices, "anime-sama.to"))

    resolved = am._find_overflow_episode_source(
        object(), "tt5095466", 3, 13,
        _FIRE_FORCE_DECLS, "vostfr", "saison3/vostfr",
        {"eps1": ["u"] * 12}, {number: number - 1 for number in range(1, 13)},
        "anime-sama.to", "fire-force", {},
    )

    assert resolved is not None, "S3E13 doit déborder vers la Partie 2"
    path, _eps_map, episode_indices, local_episode, _host = resolved
    assert path == "saison3-2/vostfr"
    assert local_episode == 1
    assert episode_indices[local_episode] == 0  # 1er épisode de la Partie 2


# Données Fire Force réutilisées : Partie 1 (12 ép.) + Partie 2 (13 ép.) = 25.
_FF_S3_IDX = {n: n - 1 for n in range(1, 13)}
_FF_S3_EPS = {"eps1": [f"https://s3/{n}" for n in range(1, 13)]}
_FF_S32_IDX = {n: n - 1 for n in range(1, 14)}
_FF_S32_EPS = {"eps2": [f"https://vidmoly.to/embed-{n}.html" for n in range(1, 14)]}


def test_plan_located_episode_direct_then_overflow(monkeypatch):
    # Épisode présent dans le dossier -> mapping direct, sans aucune requête.
    def _no_fetch(*_a, **_k):
        raise AssertionError("aucune requête ne doit être faite pour un mapping direct")
    monkeypatch.setattr(am, "_fetch_episodes", _no_fetch)
    direct = am._season_relative_plan(
        object(), "tt5095466", 3, 5, True,
        _FIRE_FORCE_DECLS, "vostfr", "saison3/vostfr", _FF_S3_EPS, _FF_S3_IDX,
        "anime-sama.to", "fire-force", {},
    )
    assert direct == [(5, "saison3/vostfr", _FF_S3_EPS, _FF_S3_IDX, 5, "anime-sama.to")]

    # Épisode 13 -> débordement vers saison3-2 épisode local 1.
    monkeypatch.setattr(am, "_absolute_season_plan",
                        lambda _s, _imdb, season: [(n, n) for n in range(1, 26)] if season == 3 else [])
    monkeypatch.setattr(am, "_fetch_episodes",
                        lambda _s, _am, _slug, path, _h: (_FF_S32_EPS, _FF_S32_IDX, "anime-sama.to"))
    overflow = am._season_relative_plan(
        object(), "tt5095466", 3, 13, True,
        _FIRE_FORCE_DECLS, "vostfr", "saison3/vostfr", _FF_S3_EPS, _FF_S3_IDX,
        "anime-sama.to", "fire-force", {},
    )
    assert overflow == [(13, "saison3-2/vostfr", _FF_S32_EPS, _FF_S32_IDX, 1, "anime-sama.to")]


def test_plan_located_last_episode_of_split_season_maps_to_final_part(monkeypatch):
    # Limite haute : S3E25 (dernier de la saison TheTVDB) = dernier épisode de la
    # Partie 2. 12 ép. en saison3, reste 13 -> saison3-2 épisode local 13.
    monkeypatch.setattr(am, "_absolute_season_plan",
                        lambda _s, _imdb, season: [(n, n) for n in range(1, 26)] if season == 3 else [])
    monkeypatch.setattr(am, "_fetch_episodes",
                        lambda _s, _am, _slug, path, _h: (_FF_S32_EPS, _FF_S32_IDX, "anime-sama.to"))

    located = am._season_relative_plan(
        object(), "tt5095466", 3, 25, True,
        _FIRE_FORCE_DECLS, "vostfr", "saison3/vostfr", _FF_S3_EPS, _FF_S3_IDX,
        "anime-sama.to", "fire-force", {},
    )
    assert located == [(25, "saison3-2/vostfr", _FF_S32_EPS, _FF_S32_IDX, 13, "anime-sama.to")]
    # L'index local 13 pointe bien sur la dernière entrée du dossier Partie 2.
    assert _FF_S32_IDX[located[0][4]] == 12


def test_plan_located_full_season_spans_split_cour_folders(monkeypatch):
    # Recherche « saison 3 » entière : les 25 épisodes doivent sortir, répartis
    # sur les deux dossiers (auparavant seuls les 12 de la Partie 1 sortaient).
    monkeypatch.setattr(am, "_absolute_season_plan",
                        lambda _s, _imdb, season: [(n, n) for n in range(1, 26)] if season == 3 else [])
    monkeypatch.setattr(am, "_fetch_episodes",
                        lambda _s, _am, _slug, path, _h: (_FF_S32_EPS, _FF_S32_IDX, "anime-sama.to"))

    located = am._season_relative_plan(
        object(), "tt5095466", 3, None, True,
        _FIRE_FORCE_DECLS, "vostfr", "saison3/vostfr", _FF_S3_EPS, _FF_S3_IDX,
        "anime-sama.to", "fire-force", {},
    )

    assert [ep for ep, *_ in located] == list(range(1, 26))
    assert located[11] == (12, "saison3/vostfr", _FF_S3_EPS, _FF_S3_IDX, 12, "anime-sama.to")
    assert located[12] == (13, "saison3-2/vostfr", _FF_S32_EPS, _FF_S32_IDX, 1, "anime-sama.to")
    assert located[24] == (25, "saison3-2/vostfr", _FF_S32_EPS, _FF_S32_IDX, 13, "anime-sama.to")


def test_plan_located_non_aligned_reads_absolute_from_single_folder(monkeypatch):
    # Fairy Tail : pas de dossier saison5, tout est en absolu dans saison1.
    ft_idx = {n: n - 1 for n in range(1, 329)}
    ft_eps = {"eps1": [f"https://s1/{n}" for n in range(1, 329)]}
    monkeypatch.setattr(am, "_absolute_episode",
                        lambda _s, _imdb, season, ep: 176 if (season, ep) == (5, 1) else None)

    located = am._season_relative_plan(
        object(), "tt1", 5, 1, False,
        [("Saison 1", "saison1/vostfr")], "vostfr",
        "saison1/vostfr", ft_eps, ft_idx, "anime-sama.to", "fairy-tail", {},
    )
    # S5E1 (Sonarr) -> épisode absolu 176 lu dans le dossier unique.
    assert located == [(1, "saison1/vostfr", ft_eps, ft_idx, 176, "anime-sama.to")]


# ---- Méthode principale : regroupement par numéro absolu (contrôle de total) ----

def _folder(prefix, n):
    """Fabrique (eps_map, indices) d'un dossier de n épisodes contigus."""
    return {"eps1": [f"https://{prefix}/{i}" for i in range(1, n + 1)]}, {i: i - 1 for i in range(1, n + 1)}


def _fetch_from(counts):
    """Faux _fetch_episodes_cached renvoyant le dossier correspondant au chemin."""
    data = {path: _folder(path.split("/")[0], n) for path, n in counts.items()}

    def _fetch(_s, _am, _slug, path, _h):
        eps, idx = data[path]
        return eps, idx, "anime-sama.to"
    return _fetch


_DS_DECLS = [
    ("Saison 1", "saison1/vostfr"),
    ("Film - Train de l'infini", "film1/vostfr"),
    ("Épisode - Train de l'infini", "saison1hs/vostfr"),
    ("Saison 2", "saison2/vostfr"),
    ("Saison 3", "saison3/vostfr"),
    ("Saison 4", "saison4/vostfr"),
    ("Film - La Forteresse Infinie", "film2/vostfr"),
]
_DS_COUNTS = {
    "saison1/vostfr": 26, "saison1hs/vostfr": 7, "saison2/vostfr": 11,
    "saison3/vostfr": 11, "saison4/vostfr": 8,
}


def test_is_episode_arc_label_matches_only_episode_prefix():
    assert am._is_episode_arc_label("Épisode - Train de l'infini")   # accents ok
    assert am._is_episode_arc_label("episode - autre chose")
    assert not am._is_episode_arc_label("100 Years Quest Saison 1")
    assert not am._is_episode_arc_label("Film - Train de l'infini")
    assert not am._is_episode_arc_label("Saison 2")


def test_ordered_episode_folders_includes_episode_arc_hors_serie_only():
    # Demon Slayer : saison1hs (« Épisode - Train de l'infini ») réinséré à sa
    # position de diffusion et compté comme saison ; film/OAV exclus.
    assert am._ordered_episode_folders(_DS_DECLS, "vostfr") == [
        "saison1/vostfr", "saison1hs/vostfr", "saison2/vostfr", "saison3/vostfr", "saison4/vostfr",
    ]
    # Fairy Tail : saison1hs (« 100 Years Quest ») N'EST PAS un « Épisode - ... »
    # -> exclu (c'est une œuvre distincte), il ne reste que la saison numérotée.
    fairy_tail = [
        ("Saison 1", "saison1/vostfr"),
        ("Film", "film/vostfr"),
        ("100 Years Quest Saison 1", "saison1hs/vostfr"),
    ]
    assert am._ordered_episode_folders(fairy_tail, "vostfr") == ["saison1/vostfr"]


def test_absolute_grouping_fire_force_uses_numbered_folders(monkeypatch):
    # Aucun hors-série : la concaténation = dossiers numérotés (saison3-2 incluse).
    counts = {"saison1/vostfr": 24, "saison2/vostfr": 24,
              "saison3/vostfr": 12, "saison3-2/vostfr": 13}  # 73
    monkeypatch.setattr(am, "_series_total_episodes", lambda _s, _i: 73)
    monkeypatch.setattr(am, "_fetch_episodes_cached", _fetch_from(counts))
    abs_map = {(2, 3): 27, (3, 1): 49, (3, 13): 61, (3, 25): 73}
    monkeypatch.setattr(am, "_absolute_episode", lambda _s, _i, s, e: abs_map.get((s, e)))
    sel_eps, sel_idx = _folder("saison3", 12)

    def loc(season, ep):
        r = am._absolute_grouping_plan(
            object(), "tt", season, ep, _FIRE_FORCE_DECLS, "vostfr",
            "saison3/vostfr", sel_eps, sel_idx, "anime-sama.to", "fire-force", {})
        return (r[0][1], r[0][4]) if r else None

    assert loc(2, 3) == ("saison2/vostfr", 3)
    assert loc(3, 1) == ("saison3/vostfr", 1)
    assert loc(3, 13) == ("saison3-2/vostfr", 1)     # bascule dans la Partie 2
    assert loc(3, 25) == ("saison3-2/vostfr", 13)


def test_absolute_grouping_demon_slayer_counts_hors_serie_as_a_season(monkeypatch):
    # Cœur du problème : l'arc Mugen Train (TheTVDB S2) est rangé en saison1hs,
    # et anime-sama saison2 = Entertainment (TheTVDB S3), etc. En comptant le
    # hors-série comme une saison dans la concaténation, l'absolu retombe juste.
    monkeypatch.setattr(am, "_series_total_episodes", lambda _s, _i: 63)
    monkeypatch.setattr(am, "_fetch_episodes_cached", _fetch_from(_DS_COUNTS))
    # Absolus TheTVDB : S1 1-26, S2/Mugen 27-33, S3/Enter 34-44, S4 45-55, S5 56-63.
    abs_map = {(2, 3): 29, (3, 5): 38, (3, 11): 44, (4, 1): 45, (5, 1): 56, (5, 8): 63}
    monkeypatch.setattr(am, "_absolute_episode", lambda _s, _i, s, e: abs_map.get((s, e)))
    sel_eps, sel_idx = _folder("saison2", 11)

    def loc(season, ep):
        r = am._absolute_grouping_plan(
            object(), "tt", season, ep, _DS_DECLS, "vostfr",
            "saison2/vostfr", sel_eps, sel_idx, "anime-sama.to", "demon-slayer", {})
        return (r[0][1], r[0][4]) if r else None

    assert loc(2, 3) == ("saison1hs/vostfr", 3)   # arc Mugen Train -> hors-série
    assert loc(3, 5) == ("saison2/vostfr", 5)      # Entertainment -> saison2 anime-sama
    assert loc(3, 11) == ("saison2/vostfr", 11)
    assert loc(4, 1) == ("saison3/vostfr", 1)      # Swordsmith -> saison3 anime-sama
    assert loc(5, 1) == ("saison4/vostfr", 1)      # Piliers (saison 5 Sonarr) -> saison4
    assert loc(5, 8) == ("saison4/vostfr", 8)


def test_absolute_grouping_full_season_demon_slayer(monkeypatch):
    monkeypatch.setattr(am, "_series_total_episodes", lambda _s, _i: 63)
    monkeypatch.setattr(am, "_fetch_episodes_cached", _fetch_from(_DS_COUNTS))
    # Saison 3 Sonarr (Entertainment) = absolus 34..44.
    monkeypatch.setattr(am, "_absolute_season_plan",
                        lambda _s, _i, season: [(e, 33 + e) for e in range(1, 12)] if season == 3 else [])
    sel_eps, sel_idx = _folder("saison2", 11)

    located = am._absolute_grouping_plan(
        object(), "tt", 3, None, _DS_DECLS, "vostfr",
        "saison2/vostfr", sel_eps, sel_idx, "anime-sama.to", "demon-slayer", {})
    assert [(ep, p, l) for ep, p, _e, _i, l, _a in located] == \
        [(e, "saison2/vostfr", e) for e in range(1, 12)]


def test_absolute_grouping_skipped_when_total_mismatches(monkeypatch):
    # Contrôle de total : si anime-sama (63) ne colle pas avec les métadonnées,
    # on renvoie None -> repli saison-relatif. Ici on annonce 70 ≠ 63.
    monkeypatch.setattr(am, "_series_total_episodes", lambda _s, _i: 70)
    monkeypatch.setattr(am, "_fetch_episodes_cached", _fetch_from(_DS_COUNTS))
    monkeypatch.setattr(am, "_absolute_episode", lambda *_a: 40)
    sel_eps, sel_idx = _folder("saison2", 11)

    assert am._absolute_grouping_plan(
        object(), "tt", 3, 5, _DS_DECLS, "vostfr", "saison2/vostfr",
        sel_eps, sel_idx, "anime-sama.to", "demon-slayer", {}) is None


def test_absolute_grouping_returns_none_without_metadata(monkeypatch):
    # Sans total (TheTVDB/TMDB indisponibles) -> None -> repli saison-relatif.
    monkeypatch.setattr(am, "_series_total_episodes", lambda _s, _i: None)
    sel_eps, sel_idx = _folder("saison2", 11)

    assert am._absolute_grouping_plan(
        object(), "tt", 3, 5, _DS_DECLS, "vostfr", "saison2/vostfr",
        sel_eps, sel_idx, "anime-sama.to", "demon-slayer", {}) is None


def test_preferred_series_language_when_no_exact_season_folder():
    # Demon Slayer saison 5 : aucun dossier « saison5 » exact, mais des dossiers
    # d'épisodes en vostfr -> on résout via le regroupement absolu en vostfr.
    assert am._select_season_path(_DS_DECLS, False, 5) == (None, None)
    assert am._preferred_series_language(_DS_DECLS) == "vostfr"


def test_demon_slayer_season5_resolves_via_absolute_grouping(monkeypatch):
    # Cœur du bug « saison 5 ne marche pas » : Sonarr saison 5 (Entraînement des
    # Piliers) = anime-sama saison4, sans dossier saison5. Sans dossier de
    # départ (season_path=None), le regroupement absolu retrouve le bon dossier.
    monkeypatch.setattr(am, "_series_total_episodes", lambda _s, _i: 63)
    monkeypatch.setattr(am, "_fetch_episodes_cached", _fetch_from(_DS_COUNTS))
    abs_map = {(5, 1): 56, (5, 8): 63}
    monkeypatch.setattr(am, "_absolute_episode", lambda _s, _i, s, e: abs_map.get((s, e)))

    lang = am._preferred_series_language(_DS_DECLS)

    def loc(ep):
        r = am._plan_located_episodes(
            object(), "tt", 5, ep, False, _DS_DECLS, lang,
            None, {}, {}, "anime-sama.to", "demon-slayer", {})
        return (r[0][1], r[0][4]) if r else None

    assert loc(1) == ("saison4/vostfr", 1)   # 1er épisode de la saison 5 Sonarr
    assert loc(8) == ("saison4/vostfr", 8)   # dernier


_OP_KAI_DECLS = [
    ("Saga 1 (East Blue)", "saison1/vostfr"),
    ("Saga 12 (Elbaf)", "saison12/vostfr"),
    ("Films", "film/vostfr"),
    ("Kai - Saga 1 (East Blue)", "kai/vostfr"),
    ("Kai - Saga 2 (Alabasta)", "kai2/vostfr"),
]


def test_kai_folders_detects_per_saga_and_single():
    # One Piece : kai par saga (kai = saga 1, kai2 = saga 2).
    assert am._kai_folders(_OP_KAI_DECLS, "vostfr") == {1: "kai/vostfr", 2: "kai2/vostfr"}
    # Fairy Tail : un seul dossier « kai ».
    assert am._kai_folders([("Kai", "kai/vostfr"), ("Saison 1", "saison1/vostfr")], "vostfr") == {1: "kai/vostfr"}
    # Aucune version Kai.
    assert am._kai_folders([("Saison 1", "saison1/vostfr")], "vostfr") == {}


def test_kai_per_saga_real_absolute_then_gap_then_regular(monkeypatch):
    # Modèle One Piece : Sonarr en absolu réel. kai=3 films (saga 1), kai2=2
    # (saga 2) -> T=5 ; saison1=10, saison2=8 -> M=18 (arcs condensés).
    data = {
        "kai/vostfr": _folder("k1", 3), "kai2/vostfr": _folder("k2", 2),
        "saison1/vostfr": _folder("s1", 10), "saison2/vostfr": _folder("s2", 8),
    }
    monkeypatch.setattr(am, "_fetch_episodes_cached",
                        lambda _s, _a, _sl, path, _h: (data[path][0], data[path][1], "anime-sama.to"))
    # Le kai par-saga lit l'absolu de TheTVDB (pas de repli TMDB). Ici : (1, e) -> e.
    monkeypatch.setattr(am, "tvdb_absolute_number",
                        lambda _s, _i, season, ep: ep if season == 1 else None)
    kai_map = {1: "kai/vostfr", 2: "kai2/vostfr"}

    def kai(ep):
        return am._kai_located_episodes(
            object(), "tt", 1, ep, kai_map, "vostfr", "anime-sama.to", "one-piece", {})

    h, l = kai(1);  assert h and (l[0][1], l[0][4]) == ("kai/vostfr", 1)    # 1er film kai
    h, l = kai(4);  assert h and (l[0][1], l[0][4]) == ("kai2/vostfr", 1)   # film 4 -> kai2 film 1
    h, l = kai(5);  assert h and (l[0][1], l[0][4]) == ("kai2/vostfr", 2)   # dernier film kai (T=5)
    h, l = kai(6);  assert h and l == []       # 6 <= M=18 -> arc condensé -> rien
    h, l = kai(18); assert h and l == []       # encore condensé
    h, l = kai(19); assert h is False          # > M -> « la fin » -> flux normal (Elbaf)


def test_kai_single_lays_films_sequentially_across_seasons(monkeypatch):
    # Kai unique (Fairy Tail : un seul dossier kai, 60 films) ; TheTVDB S1=48, S2=48.
    kai_eps, kai_idx = _folder("kai", 60)
    monkeypatch.setattr(am, "_fetch_episodes_cached",
                        lambda _s, _a, _sl, path, _h: (kai_eps, kai_idx, "anime-sama.to"))
    monkeypatch.setattr(am, "_tmdb_offset",
                        lambda _s, _i, season: {1: (0, 48), 2: (48, 48)}.get(season, (None, None)))
    kai_map = {1: "kai/vostfr"}

    def kai(season, ep):
        handled, loc = am._kai_located_episodes(
            object(), "tt", season, ep, kai_map, "vostfr", "anime-sama.to", "fairy-tail", {})
        assert handled is True         # kai unique -> toujours géré (jamais d'épisode normal)
        return (loc[0][1], loc[0][4]) if loc else None

    assert kai(1, 1) == ("kai/vostfr", 1)
    assert kai(1, 48) == ("kai/vostfr", 48)
    assert kai(2, 1) == ("kai/vostfr", 49)    # déborde dans la saison 2
    assert kai(2, 12) == ("kai/vostfr", 60)    # dernier film
    assert kai(2, 13) is None                  # film 61 > 60 -> rien


def test_log_survives_non_utf8_console(monkeypatch):
    # Bug trouvé en test live : sous Windows (console cp1252), un titre japonais
    # (ワンピース) dans un log faisait planter TOUTE la recherche. Un log ne doit
    # jamais lever — on remplace les caractères non encodables.
    from quasarr.providers import log

    class Cp1252Stream:
        encoding = "cp1252"

        def write(self, s):
            s.encode("cp1252")  # lève UnicodeEncodeError sur du japonais
            return len(s)

        def flush(self):
            pass

    monkeypatch.setattr(log.sys, "stdout", Cp1252Stream())
    log.info("TMDB title original='ワンピース'")   # ne doit pas lever
    log.info("スラッシュ", source="am")


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


def test_enqueue_clears_stale_failed_entry_for_same_package_id():
    state = FakeState()
    # Une tentative précédente a laissé une ligne "failed" pour ce package_id
    # (re-grab Sonarr → même sha256(title|url) → même id).
    state.get_db("failed").update_store("pkg-dup", json.dumps({"title": "Ep", "error": "boom"}))
    state.get_db("protected").update_store("pkg-dup", json.dumps({"title": "Ep"}))

    job = enqueue_job(state, "pkg-dup", "Ep", ["https://one"], "tt1", 450)

    assert job["status"] == "queued"
    # Plus aucun doublon de nzo_id : la queue (ytdlp) et l'history (failed) ne
    # peuvent plus exposer le même id à Sonarr en même temps.
    assert state.get_db("failed").retrieve("pkg-dup") is None
    assert state.get_db("protected").retrieve("pkg-dup") is None


def test_am_package_id_tracks_resolved_embed_url():
    """L'id anime-sama suit l'URL d'embed résolue, pas la page de saison.

    Un vrai Sonarr blackliste définitivement un nzo_id vu "Failed" et ignore
    toute entrée de queue le réutilisant. En basant l'id sur l'embed résolu :
    embed inchangé (cassé) → même id → reste blacklisté (pas de re-test inutile) ;
    embed changé (épisode re-publié) → nouvel id → Sonarr re-teste."""
    title = "Solo.Leveling.S01E08.VOSTFR.1080p.WEB.x264-ANIMESAMA.Vidmoly"
    page_url = "https://anime-sama.to/catalogue/solo-leveling/saison1/vostfr/#episode=8&player=Vidmoly"
    base = _package_id("tv", title, page_url)

    id_a = _am_package_id(base, title, ["https://vidmoly.to/embed-AAA.html"])
    id_b = _am_package_id(base, title, ["https://vidmoly.to/embed-BBB.html"])

    # Le préfixe catégorie est conservé (le filtre Sonarr tv/docs passe toujours).
    assert id_a.startswith("SABnzbd_tv_")
    # Embed inchangé → id stable.
    assert id_a == _am_package_id(base, title, ["https://vidmoly.to/embed-AAA.html"])
    # Embed changé → id différent (nouvelle tentative visible côté Sonarr).
    assert id_a != id_b
    # Différent de l'id basé sur la page (URL stable même quand le lecteur casse).
    assert id_a != base


def test_handle_am_download_id_is_derived_from_embed(monkeypatch):
    """handle_am enfile le job (et renvoie le nzo_id) sous l'id basé sur l'embed."""
    from quasarr import downloads as dl

    state = FakeState()
    embed = "https://video.sibnet.ru/shell.php?videoid=42"
    monkeypatch.setattr(dl, "get_am_download_links", lambda *a, **k: [embed])
    monkeypatch.setattr(dl, "send_discord_message", lambda *a, **k: None)

    title = "Show.S01E01.VOSTFR.1080p.WEB.x264-ANIMESAMA.Sibnet"
    page_url = "https://anime-sama.to/catalogue/show/saison1/vostfr/#episode=1&player=Sibnet"
    base = _package_id("tv", title, page_url)

    result = dl.handle_am(state, title, "pw", base, "tt1", page_url, None, 450)

    expected = _am_package_id(base, title, [embed])
    assert result["success"] is True
    assert result["package_id"] == expected
    # Le job yt-dlp est indexé par l'id basé sur l'embed, pas par l'id de page.
    assert state.get_db("ytdlp").retrieve(expected) is not None
    assert state.get_db("ytdlp").retrieve(base) is None


def test_rss_date_is_stable_within_day_for_blocklist_matching(monkeypatch):
    """La pubDate d'une release doit être stable au fil des recherches d'une même
    journée. Sinon Sonarr voit une release échouée/blacklistée comme "nouvelle"
    au retry automatique et la re-grabbe (reproduit contre un vrai Sonarr)."""
    import datetime as real_dt

    class FakeDatetime:
        current = real_dt.datetime(2026, 3, 4, 9, 30, 15, tzinfo=real_dt.timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(am, "datetime", FakeDatetime)

    first = am._rss_date()
    FakeDatetime.current = real_dt.datetime(2026, 3, 4, 22, 47, 3, tzinfo=real_dt.timezone.utc)
    later_same_day = am._rss_date()

    assert first == later_same_day                 # stable within the day
    assert first == "Wed, 04 Mar 2026 00:00:00 +0000"  # truncated to midnight

    FakeDatetime.current = real_dt.datetime(2026, 3, 5, 1, 0, 0, tzinfo=real_dt.timezone.utc)
    assert am._rss_date() != first                 # advances with the calendar day


def test_completed_job_is_requeued_after_sonarr_moved_its_file(tmp_path):
    state = FakeState(str(tmp_path))
    first = enqueue_job(state, "pkg-retry", "Episode 1", ["https://old"], "tt1", 450)
    first.update({
        "status": "completed",
        "storage": os.fspath(tmp_path / "Episode.1.mp4"),
    })
    state.db.update_store("pkg-retry", json.dumps(first))

    retried = enqueue_job(
        state,
        "pkg-retry",
        "Episode 1",
        ["https://new"],
        "tt1",
        450,
    )

    assert _completed_output_exists(first) is False
    assert retried["status"] == "queued"
    assert retried["candidates"] == ["https://new"]


def test_completed_job_is_kept_while_its_file_still_exists(tmp_path):
    state = FakeState(str(tmp_path))
    media = tmp_path / "Episode.1.mp4"
    media.write_bytes(b"video")
    first = enqueue_job(state, "pkg-existing", "Episode 1", ["https://old"], "tt1", 450)
    first.update({"status": "completed", "storage": os.fspath(media)})
    state.db.update_store("pkg-existing", json.dumps(first))

    duplicate = enqueue_job(
        state,
        "pkg-existing",
        "Episode 1",
        ["https://new"],
        "tt1",
        450,
    )

    assert _completed_output_exists(first) is True
    assert duplicate["status"] == "completed"
    assert duplicate["candidates"] == ["https://old"]


def test_startup_moves_already_completed_flat_file_back_into_folder(tmp_path):
    state = FakeState(str(tmp_path))
    flat_media = tmp_path / "Episode.1.mp4"
    flat_media.write_bytes(b"video")
    job = enqueue_job(state, "pkg-flat-completed", "Episode 1", ["https://old"], "tt1", 450)
    job.update({
        "status": "completed",
        "storage": os.fspath(flat_media),
    })
    state.db.update_store("pkg-flat-completed", json.dumps(job))

    YtdlpWorker(state)._repair_existing_ownership()

    folder = tmp_path / "Episode.1"
    migrated = json.loads(state.db.retrieve("pkg-flat-completed"))
    assert not flat_media.exists()
    assert (folder / "Episode.1.mp4").read_bytes() == b"video"
    assert migrated["storage"] == os.fspath(folder)


def test_startup_restores_flat_anime_sama_file_even_without_database_job(tmp_path):
    state = FakeState(str(tmp_path))
    filename = "Aggretsuko.S01E03.FRENCH.1080p.WEB.x264-ANIMESAMA.Vidmoly.mp4"
    flat_media = tmp_path / filename
    flat_media.write_bytes(b"video")
    unrelated = tmp_path / "Other.Show.S01E01.mp4"
    unrelated.write_bytes(b"other")

    YtdlpWorker(state)._repair_existing_ownership()

    folder = tmp_path / filename.removesuffix(".mp4")
    assert not flat_media.exists()
    assert (folder / filename).read_bytes() == b"video"
    assert unrelated.read_bytes() == b"other"


def test_ytdlp_status_is_published_without_full_jdownloader_snapshot():
    state = FakeState()
    snapshotter = PackageSnapshotter(state)
    job = {
        "package_id": "pkg-fast",
        "title": "Fast S01E01",
        "status": "downloading",
        "category": "tv",
        "size_mb": 450,
    }

    state.db.update_store("pkg-fast", json.dumps(job))
    snapshotter.update_ytdlp_job(job)
    snapshot, _, _ = snapshotter.get()
    assert snapshot["queue"][0]["nzo_id"] == "pkg-fast"
    assert snapshot["queue"][0]["type"] == "downloader"
    assert snapshot["queue"][0]["filename"] == "[Downloading] Fast S01E01"
    assert snapshot["queue"][0]["_source"] == "ytdlp"

    job.update(status="completed", storage="/output/Fast.S01E01", bytes_loaded=1024)
    state.db.update_store("pkg-fast", json.dumps(job))
    snapshotter.update_ytdlp_job(job)
    snapshot, _, _ = snapshotter.get()
    assert snapshot["queue"] == []
    assert snapshot["history"][0]["status"] == "Completed"
    assert snapshot["history"][0]["storage"] == "/output/Fast.S01E01"

    job.update(status="failed", error="DownloadError: HTTP Error 403", storage="")
    state.db.update_store("pkg-fast", json.dumps(job))
    snapshotter.update_ytdlp_job(job)
    snapshot, _, _ = snapshotter.get()
    assert snapshot["history"][0]["status"] == "Failed"
    assert snapshot["history"][0]["fail_message"] == "DownloadError: HTTP Error 403"
    assert snapshot["history"][0]["storage"] == "/"


def test_completed_am_job_goes_directly_to_history_like_jdownloader():
    """Un job terminé passe directement en history (comme JDownloader) : pas de
    barrière "queue_seen" qui le laisserait stagner en queue côté Sonarr."""
    state = FakeState()
    job = enqueue_job(state, "pkg-instant", "Instant.S01E01", ["https://one"], "tt1", 450)
    job.update({
        "status": "completed",
        "storage": "/output/Instant.S01E01",
        "bytes_loaded": 2048,
        "bytes_total": 2048,
        "percent": 100,
    })
    state.db.update_store("pkg-instant", json.dumps(job))
    snapshotter = PackageSnapshotter(state)
    # Simule un ancien refresh JDownloader ayant publié un cache sans le job AM.
    snapshotter._snapshot = {"queue": [], "history": []}

    snapshot, _, _ = snapshotter.get()

    # Aucune apparition en queue : directement en history, prêt à l'import.
    assert snapshot["queue"] == []
    assert snapshot["history"][0]["nzo_id"] == "pkg-instant"
    assert snapshot["history"][0]["status"] == "Completed"
    assert snapshot["history"][0]["storage"] == "/output/Instant.S01E01"


def test_queued_am_payload_matches_jdownloader_shape_sent_to_sonarr():
    state = FakeState()
    job = enqueue_job(state, "pkg-queue-shape", "Show.S01E01", ["https://one"], "tt1", 450)
    snapshotter = PackageSnapshotter(state)

    snapshot, _, _ = snapshotter.get()
    public = public_download_slots(snapshot["queue"])[0]

    assert public == {
        "index": 0,
        "nzo_id": "pkg-queue-shape",
        "priority": "Normal",
        "filename": "[Paused] Show.S01E01",
        "cat": "tv",
        "mbleft": 450,
        "mb": 450,
        "status": "Downloading",
        "percentage": 0,
        "timeleft": "23:59:59",
        "type": "downloader",
        "uuid": job["uuid"],
    }


def test_failed_am_job_goes_directly_to_history_for_sonarr_blocklist():
    state = FakeState()
    job = enqueue_job(state, "pkg-failed-fast", "Failed.S01E01", ["https://one"], "tt1", 450)
    job.update({
        "status": "failed",
        "storage": "/output/Failed.S01E01",
        "error": "DownloadError: HTTP Error 403: Forbidden",
        "completed_at": 123,
    })
    state.db.update_store("pkg-failed-fast", json.dumps(job))
    snapshotter = PackageSnapshotter(state)

    snapshot, _, _ = snapshotter.get()

    assert snapshot["queue"] == []
    public = public_download_slots(snapshot["history"])[0]
    assert public == {
        "fail_message": "DownloadError: HTTP Error 403: Forbidden",
        "category": "tv",
        "storage": "/",
        "status": "Failed",
        "nzo_id": "pkg-failed-fast",
        "name": "Failed.S01E01",
        "bytes": 0,
        "percentage": 100,
        "type": "downloader",
        "uuid": "pkg-failed-fast",
    }


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


def test_startup_removes_folder_of_persisted_failed_job(tmp_path):
    state = FakeState(str(tmp_path))
    job = enqueue_job(state, "pkg-old-failure", "Old Failure", ["https://one"], "tt1", 450)
    job.update(status="failed", error="old failure")
    state.db.update_store("pkg-old-failure", json.dumps(job))
    folder = tmp_path / "Old.Failure"
    folder.mkdir()
    (folder / "Old.Failure.mp4.part").write_bytes(b"partial")

    YtdlpWorker(state)._migrate_legacy_jobs()

    assert not folder.exists()


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

    output_folder = tmp_path / "Show.S01E02"
    # Simule un .part créé à la racine par la courte version sans dossiers.
    partial = tmp_path / "Show.S01E02.mp4.part"
    partial.write_bytes(b"already downloaded")
    unrelated = tmp_path / "Another.Show.S01E01.mp4"
    unrelated.write_bytes(b"unrelated and deliberately larger")
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
            assert not partial.exists()
            assert (output_folder / "Show.S01E02.mp4.part").exists()
            final = self.options["outtmpl"].replace("%(ext)s", "mp4")
            with open(final, "wb") as stream:
                stream.write(b"complete file")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    worker._run_job(resumed)

    completed = json.loads(state.db.retrieve("pkg-resume"))
    assert completed["status"] == "completed"
    assert completed["candidate_index"] == 1
    assert completed["storage"] == os.fspath(output_folder)
    assert (output_folder / "Show.S01E02.mp4").exists()
    assert unrelated.exists()
    assert calls[0]["continuedl"] is True
    assert calls[0]["nopart"] is False
    assert calls[0]["overwrites"] is False
    assert statuses == ["downloading", "completed"]


def test_failed_download_removes_only_its_own_output_folder(tmp_path, monkeypatch):
    state = FakeState(str(tmp_path))
    job = enqueue_job(
        state,
        "pkg-failed-folder",
        "Failed S01E01",
        ["https://failed.invalid/video"],
        "tt1",
        450,
    )
    untouched = tmp_path / "Other.Show.S01E01"
    untouched.mkdir()
    (untouched / "keep.mp4").write_bytes(b"keep")

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, _links):
            partial = self.options["outtmpl"].replace("%(ext)s", "mp4.part")
            with open(partial, "wb") as stream:
                stream.write(b"partial")
            raise RuntimeError("download failed")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    YtdlpWorker(state, inter_job_delay=0)._run_job(job)

    failed = json.loads(state.db.retrieve("pkg-failed-folder"))
    assert failed["status"] == "failed"
    assert failed["storage"] == os.fspath(tmp_path / "Failed.S01E01")
    assert not (tmp_path / "Failed.S01E01").exists()
    assert (untouched / "keep.mp4").exists()


def test_sibnet_uses_ipv4_source_referer_and_retries_403(tmp_path, monkeypatch):
    state = FakeState(str(tmp_path))
    source_url = "https://anime-sama.to/catalogue/aggretsuko/saison1/vf/#episode=4&player=Sibnet"
    link = "https://video.sibnet.ru/shell.php?videoid=5452020"
    job = enqueue_job(
        state,
        "pkg-sibnet",
        "Aggretsuko.S01E04",
        [link],
        "tt8019444",
        450,
        source_url=source_url,
    )
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
            assert links == [link]
            if len(calls) < 3:
                raise RuntimeError("HTTP Error 403: Forbidden")
            final = self.options["outtmpl"].replace("%(ext)s", "mp4")
            with open(final, "wb") as stream:
                stream.write(b"complete file")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    worker = YtdlpWorker(state, inter_job_delay=0, random_uniform=lambda _low, _high: 0)
    worker._run_job(job)

    completed = json.loads(state.db.retrieve("pkg-sibnet"))
    assert completed["status"] == "completed"
    assert completed["source_url"] == source_url
    assert len(calls) == 3
    assert all(options["source_address"] == "0.0.0.0" for options in calls)
    assert all(options["http_headers"] == {"Referer": source_url} for options in calls)
    assert all("User-Agent" not in options["http_headers"] for options in calls)


class _NeverStop:
    """Faux threading.Event : jamais déclenché, mémorise les durées d'attente.

    Permet de simuler la temporisation 429 sans dormir réellement 10 minutes.
    """

    def __init__(self):
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return False

    def is_set(self):
        return False

    def set(self):
        pass

    def clear(self):
        pass


class _CfgState:
    """Mock minimal exposant une section YTDLP configurable clé par clé."""

    def __init__(self, **ytdlp):
        self._ytdlp = ytdlp
        self.values = {"config": self._config}

    def _config(self, section):
        assert section == "YTDLP"
        return SimpleNamespace(get=lambda key: self._ytdlp.get(key, ""))


def test_rate_limit_settings_read_from_ui_config():
    # Valeurs saisies dans l'UI honorées (minutes -> secondes).
    s = _CfgState(rate_limit_backoff_minutes="2", rate_limit_max_retries="3")
    assert get_rate_limit_backoff_seconds(s) == 120
    assert get_rate_limit_max_retries(s) == 3

    # Champs vides -> défauts.
    s = _CfgState()
    assert get_rate_limit_backoff_seconds(s) == RATE_LIMIT_BACKOFF_SECONDS
    assert get_rate_limit_max_retries(s) == RATE_LIMIT_MAX_RETRIES

    # Saisie invalide -> défauts.
    s = _CfgState(rate_limit_backoff_minutes="abc", rate_limit_max_retries="x")
    assert get_rate_limit_backoff_seconds(s) == RATE_LIMIT_BACKOFF_SECONDS
    assert get_rate_limit_max_retries(s) == RATE_LIMIT_MAX_RETRIES

    # 0 réessai = échec immédiat (autorisé) ; back-off <= 0 -> défaut.
    assert get_rate_limit_max_retries(_CfgState(rate_limit_max_retries="0")) == 0
    assert get_rate_limit_backoff_seconds(_CfgState(rate_limit_backoff_minutes="0")) \
        == RATE_LIMIT_BACKOFF_SECONDS


def test_is_rate_limited_detects_http_429():
    assert _is_rate_limited(RuntimeError(
        "ERROR: [generic] Unable to download webpage: HTTP Error 429: Too Many Requests"))
    assert _is_rate_limited(Exception("HTTP Error 429"))
    assert not _is_rate_limited(RuntimeError("HTTP Error 403: Forbidden"))
    assert not _is_rate_limited(RuntimeError("Unable to download webpage: HTTP Error 404"))


def test_http_429_holds_queue_and_retries_without_failing(tmp_path, monkeypatch):
    # anime-sama renvoie 429 : on doit tenir la file (pause) et réessayer le même
    # lecteur, sans jamais marquer le job "failed" (sinon Sonarr blackliste).
    state = FakeState(str(tmp_path))
    link = "https://anime-sama.invalid/embed"
    job = enqueue_job(
        state, "pkg-429", "Island.2018.S01E01", [link], "tt8737996", 450,
        source_url="https://anime-sama.to/catalogue/island/saison1/vostfr/",
    )

    saved_statuses = []
    calls = {"n": 0}

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, links):
            assert links == [link]  # toujours le même lecteur, jamais abandonné
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError(
                    "ERROR: [generic] Unable to download webpage: "
                    "HTTP Error 429: Too Many Requests"
                )
            final = self.options["outtmpl"].replace("%(ext)s", "mp4")
            with open(final, "wb") as stream:
                stream.write(b"complete file")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    worker = YtdlpWorker(state, inter_job_delay=0, random_uniform=lambda _low, _high: 0)
    fake_stop = _NeverStop()
    worker._stop = fake_stop
    original_save = worker._save
    monkeypatch.setattr(worker, "_save",
                        lambda job: (saved_statuses.append(job.get("status")), original_save(job))[1])

    worker._run_job(job)

    completed = json.loads(state.db.retrieve("pkg-429"))
    assert completed["status"] == "completed"          # jamais "failed"
    assert "failed" not in saved_statuses              # aucune sauvegarde en échec
    assert calls["n"] == 3                              # 2 backoffs puis succès
    # Deux pauses ~10 min ont tenu la file (worker mono-thread → 0 autre DL).
    assert fake_stop.waits.count(RATE_LIMIT_BACKOFF_SECONDS) == 2
    assert not completed.get("rate_limited")           # marqueur nettoyé à la fin


def test_http_429_gives_up_after_max_retries_to_avoid_stuck_queue(tmp_path, monkeypatch):
    # Garde-fou : si l'hébergeur nous limite durablement, on finit par abandonner
    # après RATE_LIMIT_MAX_RETRIES cycles pour ne pas bloquer la file à vie.
    state = FakeState(str(tmp_path))
    job = enqueue_job(
        state, "pkg-429-max", "Show.S01E01",
        ["https://anime-sama.invalid/embed"], "tt1", 450,
    )

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, _links):
            raise RuntimeError("HTTP Error 429: Too Many Requests")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    worker = YtdlpWorker(state, inter_job_delay=0, random_uniform=lambda _low, _high: 0)
    fake_stop = _NeverStop()
    worker._stop = fake_stop

    worker._run_job(job)

    failed = json.loads(state.db.retrieve("pkg-429-max"))
    assert failed["status"] == "failed"
    assert fake_stop.waits.count(RATE_LIMIT_BACKOFF_SECONDS) == RATE_LIMIT_MAX_RETRIES
    assert "429" in failed["error"] or "Too Many Requests" in failed["error"]


def test_iframe_rewrite_rules_extract_vidmoly_domain_swap():
    # La règle vidmoly.(to|net) -> vidmoly.biz déclarée par anime-sama doit être
    # extraite puis appliquée ; les autres hôtes restent intacts.
    videos_js = (
        "function replaceVidmoly(url) {\n"
        "  return url.replace(/vidmoly\\.(to|net)/g, 'vidmoly.biz');\n"
        "}\n"
        "const proto = HTMLIFrameElement.prototype;\n"
        "x = replaceVidmoly(value);\n"
    )
    rules = download_am._parse_iframe_rewrite_rules(videos_js)
    assert download_am._apply_rewrite_rules(
        ["https://vidmoly.to/embed-abc.html",
         "https://vidmoly.net/embed-def.html",
         "https://sendvid.com/embed/x"],
        rules,
    ) == [
        "https://vidmoly.biz/embed-abc.html",
        "https://vidmoly.biz/embed-def.html",
        "https://sendvid.com/embed/x",
    ]


def test_vidmoly_to_link_is_rewritten_to_biz_before_download(monkeypatch):
    # Bug réel (Fire Force S2E3, lecteur Vidmoly) : anime-sama sert vidmoly.to
    # (qui redirige vers des pubs) mais le réécrit vers vidmoly.biz via
    # /js/contenu/videos.js. Le script ne s'appelle plus "script_videos" : sans
    # la détection du nouveau nom, on gardait le lien vidmoly.to -> pubs.
    download_am._REWRITE_CACHE.clear()
    page_html = (
        '<html><head>'
        '<script type="text/javascript" src="episodes.js?filever=4427"></script>'
        '<script defer src="/js/contenu/videos.js?v=1783185768"></script>'
        '</head></html>'
    )
    videos_js = (
        "/* Changer lien vidmoly */\n"
        "function replaceVidmoly(url) {\n"
        "  return url.replace(/vidmoly\\.(to|net)/g, 'vidmoly.biz');\n"
        "}\n"
        "const proto = HTMLIFrameElement.prototype;\n"
        "const original = Object.getOwnPropertyDescriptor(proto, 'src');\n"
        "Object.defineProperty(proto, 'src', {\n"
        "  set(value) { original.set.call(this, value ? replaceVidmoly(value) : value); },\n"
        "});\n"
    )

    class Resp:
        def __init__(self, text, url):
            self.text = text
            self.url = url

        def raise_for_status(self):
            return None

    def fake_request(_method, url, **_kwargs):
        if "videos.js" in url:
            return Resp(videos_js, url)
        return Resp(page_html, "https://anime-sama.to/catalogue/fire-force/saison2/vostfr/")

    monkeypatch.setattr(download_am, "_am_request", fake_request)
    try:
        rewritten = download_am._apply_site_iframe_rewrites(
            None,
            "https://anime-sama.to/catalogue/fire-force/saison2/vostfr/#episode=3&player=Vidmoly",
            {"User-Agent": "x"},
            [
                "https://vidmoly.to/embed-0uzet8a8x0o1.html",
                "https://video.sibnet.ru/shell.php?videoid=4670220",
            ],
        )
    finally:
        download_am._REWRITE_CACHE.clear()

    assert rewritten == [
        "https://vidmoly.biz/embed-0uzet8a8x0o1.html",         # .to -> .biz (plus de pub)
        "https://video.sibnet.ru/shell.php?videoid=4670220",   # autres hôtes intacts
    ]
