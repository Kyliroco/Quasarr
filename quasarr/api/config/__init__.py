# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import html as _html
from datetime import datetime

from bottle import request

from quasarr.providers.html_templates import render_form, render_button, render_success, render_fail
from quasarr.providers.players import get_players, set_player_enabled, format_speed
from quasarr.storage.config import Config
from quasarr.storage.setup import hostname_form_html, save_hostnames


def setup_config(app, shared_state):
    @app.get('/hostnames')
    def hostnames_ui():
        message = """<p>
            At least one hostname must be kept.
        </p>"""
        back_button = f'''<p>
                        {render_button("Back", "secondary", {"onclick": "location.href='/'"})}
                    </p>'''
        return render_form("Hostnames", hostname_form_html(shared_state, message) + back_button)

    @app.post("/api/hostnames")
    def hostnames_api():
        return save_hostnames(shared_state, timeout=1, first_run=False)

    @app.get('/ytdlp')
    def ytdlp_ui():
        from quasarr.downloads.ytdlp_worker import (
            get_output_dir, get_max_speed_bps,
            get_rate_limit_backoff_seconds, get_rate_limit_max_retries,
        )
        current = Config('YTDLP').get('output_dir') or ''
        effective = get_output_dir(shared_state)
        current_speed = Config('YTDLP').get('max_speed_mbps') or ''
        max_speed_bps = get_max_speed_bps(shared_state)
        speed_state = f"{max_speed_bps / (1024 * 1024):.2f} MB/s" if max_speed_bps else "unlimited"
        current_backoff = Config('YTDLP').get('rate_limit_backoff_minutes') or ''
        current_retries = Config('YTDLP').get('rate_limit_max_retries') or ''
        backoff_state = f"{get_rate_limit_backoff_seconds(shared_state) / 60:.0f} min"
        retries_state = str(get_rate_limit_max_retries(shared_state))
        form = f'''
        <p>Folder where yt-dlp (anime-sama) saves finished files. Radarr/Sonarr import
        from here, so it must be reachable by them (mind Docker path mappings).</p>
        <form action="/api/ytdlp" method="post">
            <label for="output_dir">Download folder</label>
            <input type="text" id="output_dir" name="output_dir"
                   placeholder="{effective}" autocorrect="off" autocomplete="off" value="{current}"><br>
            <label for="max_speed_mbps">Max download speed (MB/s, empty = unlimited)</label>
            <input type="text" id="max_speed_mbps" name="max_speed_mbps"
                   placeholder="unlimited" autocorrect="off" autocomplete="off" value="{current_speed}"><br>
            <label for="rate_limit_backoff_minutes">On HTTP 429 (rate limit): wait this many minutes, then retry the
                same player. The queue is held during the wait and no Sonarr failure is raised (empty = 10)</label>
            <input type="text" id="rate_limit_backoff_minutes" name="rate_limit_backoff_minutes"
                   placeholder="10" autocorrect="off" autocomplete="off" value="{current_backoff}"><br>
            <label for="rate_limit_max_retries">On HTTP 429: number of wait-and-retry cycles before giving up
                (empty = 6, 0 = fail immediately)</label>
            <input type="text" id="rate_limit_max_retries" name="rate_limit_max_retries"
                   placeholder="6" autocorrect="off" autocomplete="off" value="{current_retries}"><br>
            {render_button("Save", "primary", {"type": "submit"})}
        </form>
        <p>Currently effective: folder <code>{effective}</code>, speed <code>{speed_state}</code>,
        429 back-off <code>{backoff_state}</code> × <code>{retries_state}</code> retries</p>

        <hr>
        <p><strong>Maintenance</strong></p>
        <p>Clear all anime-sama (yt-dlp) downloads: removes every queued, active,
        completed and failed AM job from Quasarr's database. Use this to get rid of
        stale or ghost entries reported to Sonarr/Radarr. Does not delete files
        already on disk. Stop active AM downloads first if any.</p>
        <form action="/api/ytdlp/clear" method="post"
              onsubmit="return confirm('Clear ALL anime-sama downloads from Quasarr? This cannot be undone.');">
            {render_button("Clear AM downloads", "secondary", {"type": "submit"})}
        </form>

        <p>{render_button("Back", "secondary", {"onclick": "location.href='/'"})}</p>
        '''
        return render_form("yt-dlp download settings", form)

    @app.post('/api/ytdlp')
    def ytdlp_api():
        output_dir = (request.forms.get('output_dir') or '').strip()
        max_speed = (request.forms.get('max_speed_mbps') or '').strip()
        backoff = (request.forms.get('rate_limit_backoff_minutes') or '').strip()
        retries = (request.forms.get('rate_limit_max_retries') or '').strip()
        Config('YTDLP').save('output_dir', output_dir)
        Config('YTDLP').save('max_speed_mbps', max_speed)
        Config('YTDLP').save('rate_limit_backoff_minutes', backoff)
        Config('YTDLP').save('rate_limit_max_retries', retries)
        speed_msg = f"max speed {max_speed} MB/s" if max_speed else "unlimited speed"
        backoff_msg = f"429 back-off {backoff or '10'} min x {retries or '6'} retries"
        if output_dir:
            return render_success(f'yt-dlp set: folder "{output_dir}", {speed_msg}, {backoff_msg}', 3)
        return render_success(f'yt-dlp set: default folder, {speed_msg}, {backoff_msg}', 3)

    @app.post('/api/ytdlp/clear')
    def ytdlp_clear_api():
        from quasarr.downloads.ytdlp_worker import YTDLP_TABLE
        database = shared_state.get_db(YTDLP_TABLE)
        rows = database.retrieve_all_titles() or []
        for package_id, _raw in rows:
            database.delete(package_id)
        # Republie immédiatement un snapshot sans les jobs AM supprimés.
        try:
            request.app.config['snapshotter'].refresh_ytdlp_jobs()
        except Exception:
            pass
        return render_success(f'Cleared {len(rows)} anime-sama download(s)', 3)

    @app.get('/players')
    def players_ui():
        players = get_players(shared_state)
        if not players:
            body = ("<p>No anime-sama player discovered yet. Run a search in "
                    "Sonarr/Radarr first — players are added automatically as they "
                    "are found.</p>")
        else:
            rows = ""
            for name in sorted(players, key=str.lower):
                e = players[name]
                checked = "checked" if e.get("enabled", True) else ""
                season = e.get("season")
                episode = e.get("episode")
                anime = _html.escape(str(e.get("anime") or "?"))
                if season in (0, "0", None) and episode in (0, "0", None):
                    where = f"{anime} (film)"
                else:
                    where = f"{anime} S{int(season):02d}E{int(episode):02d}"
                try:
                    seen = datetime.fromtimestamp(int(e.get("first_seen", 0))).strftime("%Y-%m-%d")
                except Exception:
                    seen = "?"
                safe = _html.escape(name)
                speed = format_speed(e.get("avg_speed"))
                samples = e.get("speed_samples", 0)
                speed_cell = f"{speed}" + (f" ({samples})" if samples else "")
                rows += (f'<tr><td style="text-align:center;">'
                         f'<input type="checkbox" name="player_{safe}" {checked}></td>'
                         f'<td>{safe}</td><td>{where}</td><td>{seen}</td>'
                         f'<td style="text-align:right;">{speed_cell}</td></tr>')
            body = f'''
            <p>Enable/disable anime-sama players. Disabled players are no longer
            proposed to Sonarr/Radarr nor downloaded. The list grows automatically
            as new players are discovered. "Avg speed" is the mean yt-dlp download
            speed measured for that player (number of samples in parentheses).</p>
            <form action="/api/players" method="post">
              <div style="overflow-x:auto;">
                <table class="players-table">
                  <tr><th>On</th><th>Player</th><th>First seen</th><th>Date</th><th>Avg speed</th></tr>
                  {rows}
                </table>
              </div>
              <style>
                .players-table {{
                  margin: 0 auto;
                  border-collapse: collapse;
                }}
                .players-table th,
                .players-table td {{
                  padding: 0.35rem 1rem;
                  white-space: nowrap;
                }}
              </style>
              <br>{render_button("Save", "primary", {"type": "submit"})}
            </form>'''
        back = f'''<p>{render_button("Back", "secondary", {"onclick": "location.href='/'"})}</p>'''
        return render_form("anime-sama players", body + back)

    @app.post('/api/players')
    def players_api():
        players = get_players(shared_state)
        for name in players:
            enabled = request.forms.get(f"player_{name}") is not None
            set_player_enabled(shared_state, name, enabled)
        return render_success("Players updated", 3)
