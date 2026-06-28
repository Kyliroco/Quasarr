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
        from quasarr.downloads.ytdlp_worker import get_output_dir
        current = Config('YTDLP').get('output_dir') or ''
        effective = get_output_dir(shared_state)
        form = f'''
        <p>Folder where yt-dlp (anime-sama) saves finished files. Radarr/Sonarr import
        from here, so it must be reachable by them (mind Docker path mappings).</p>
        <form action="/api/ytdlp" method="post">
            <label for="output_dir">Download folder</label>
            <input type="text" id="output_dir" name="output_dir"
                   placeholder="{effective}" autocorrect="off" autocomplete="off" value="{current}"><br>
            {render_button("Save", "primary", {"type": "submit"})}
        </form>
        <p>Currently effective: <code>{effective}</code></p>
        <p>{render_button("Back", "secondary", {"onclick": "location.href='/'"})}</p>
        '''
        return render_form("yt-dlp download folder", form)

    @app.post('/api/ytdlp')
    def ytdlp_api():
        output_dir = (request.forms.get('output_dir') or '').strip()
        Config('YTDLP').save('output_dir', output_dir)
        if output_dir:
            return render_success(f'yt-dlp download folder set to: "{output_dir}"', 3)
        return render_success('yt-dlp download folder reset to default', 3)

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
              <table style="margin:0 auto; border-collapse:collapse;">
                <tr><th>On</th><th>Player</th><th>First seen</th><th>Date</th><th>Avg speed</th></tr>
                {rows}
              </table>
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
