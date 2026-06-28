# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

from bottle import request

from quasarr.providers.html_templates import render_form, render_button, render_success, render_fail
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
