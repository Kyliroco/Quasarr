# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

"""Pages « manquants introuvables sur le site ».

Une page par client *arr. Y figurent les titres monitorés, absents du disque et
absents de la blocklist — donc jamais trouvés, par opposition à ceux qui ont été
trouvés puis rejetés (eux sont dans la blocklist).
"""

import html as _html

from bottle import request

from quasarr.providers.arr_client import (
    ArrError,
    enrich_with_search_titles,
    get_arr_config,
    get_missing_movies,
    get_missing_series,
    is_configured,
    normalize_base_url,
    zt_search_url,
)
from quasarr.providers.html_templates import render_button, render_form, render_success
from quasarr.storage.config import Config

_PAGE_STYLE = """
<style>
  table.missing { width: 100%; border-collapse: collapse; margin-top: 12px; }
  table.missing th, table.missing td { padding: 6px 8px; text-align: left;
      border-bottom: 1px solid #ddd; vertical-align: top; font-size: 0.92em; }
  table.missing th { border-bottom: 2px solid #bbb; white-space: nowrap; }
  table.missing tr:hover { background: rgba(127,127,127,.08); }
  .muted { opacity: .65; }
  .count-badge { display: inline-block; padding: 1px 7px; border-radius: 9px;
      background: rgba(127,127,127,.22); font-size: .85em; }
  textarea.export { width: 100%; min-height: 130px; font-family: monospace;
      font-size: .85em; margin-top: 6px; }
</style>
"""


def _escape(value):
    return _html.escape(str(value if value is not None else ''))


def _render_rows(shared_state, items, zt_category, count_header):
    rows = ""
    for item in items:
        search_title = item.get('search_title') or item.get('arr_title') or ''
        link = zt_search_url(shared_state, search_title, zt_category)

        original = item.get('original_title') or ''
        original_cell = (
            f'<span class="muted">{_escape(original)}</span>' if original else
            '<span class="muted">—</span>'
        )

        if link:
            search_cell = (
                f'<a href="{_escape(link)}" target="_blank" rel="noopener">'
                f'{_escape(search_title)}</a>'
            )
        else:
            search_cell = _escape(search_title)

        count_cell = ""
        if count_header:
            count = item.get('count')
            count_cell = f'<td><span class="count-badge">{_escape(count)}</span></td>'

        rows += (
            "<tr>"
            f"<td>{_escape(item.get('arr_title'))}</td>"
            f"<td>{search_cell}</td>"
            f"<td>{original_cell}</td>"
            f"<td>{_escape(item.get('year'))}</td>"
            f"{count_cell}"
            "</tr>"
        )
    return rows


def _render_export(items, count_header):
    lines = []
    for item in items:
        year = item.get('year')
        suffix = f" ({year})" if year else ""
        title = item.get('search_title') or item.get('arr_title') or ''
        if count_header:
            lines.append(f"{title}{suffix} - {item.get('count')} episode(s)")
        else:
            lines.append(f"{title}{suffix}")
    return "\n".join(lines)


def _missing_page(shared_state, kind, zt_category, heading, count_header=None):
    home_button = render_button("Back", "secondary", {"onclick": "location.href='/'"})
    back = f"<p>{home_button}</p>"
    config_button = render_button(
        f"Configurer {kind}", "primary", {"onclick": "location.href='/arr'"},
    )

    if not is_configured(kind):
        body = (
            f"<p>{kind} n'est pas encore connecté à Quasarr.</p>"
            f"<p>{config_button}</p>" + back
        )
        return render_form(heading, body)

    try:
        items = get_missing_movies() if kind == 'Radarr' else get_missing_series()
    except ArrError as exc:
        body = (
            f"<p><strong>Impossible de lire {kind}.</strong></p>"
            f"<p><code>{_escape(exc)}</code></p>"
            f"<p>{config_button}</p>" + back
        )
        return render_form(heading, body)

    enrich_with_search_titles(shared_state, items)

    if not items:
        body = (
            f"<p>Rien à signaler : {kind} n'attend aucun titre qui ne soit "
            f"déjà passé par la blocklist.</p>" + back
        )
        return render_form(heading, body)

    count_col = f"<th>{_escape(count_header)}</th>" if count_header else ""
    table = f'''
    <p>{len(items)} titre(s) réclamé(s) par {kind}, absents du disque et
       <strong>jamais</strong> apparus dans la blocklist — ils n'ont donc jamais
       été trouvés. « Cherché par Quasarr » est le titre réellement envoyé au
       site (titre TMDB localisé) : cliquez-le pour vérifier sur ZT.</p>
    <table class="missing">
      <tr>
        <th>Titre {_escape(kind)}</th>
        <th>Cherché par Quasarr</th>
        <th>Titre original</th>
        <th>Année</th>
        {count_col}
      </tr>
      {_render_rows(shared_state, items, zt_category, count_header)}
    </table>

    <h3>Export</h3>
    <textarea class="export" readonly id="exportBox">{_escape(_render_export(items, count_header))}</textarea>
    <p>{render_button("Copier la liste", "secondary", {"onclick": "copyExport()"})}</p>

    <script>
      function copyExport() {{
        const box = document.getElementById('exportBox');
        box.select();
        document.execCommand('copy');
      }}
    </script>
    {_PAGE_STYLE}
    {back}
    '''
    return render_form(heading, table)


def setup_missing_routes(app, shared_state):
    @app.get('/arr')
    def arr_config_ui():
        radarr_url, radarr_key = get_arr_config('Radarr')
        sonarr_url, sonarr_key = get_arr_config('Sonarr')
        form = f'''
        <p>Connexion <strong>en lecture seule</strong> à Radarr et Sonarr, pour
           lister les titres réclamés qui n'ont jamais été trouvés. Quasarr ne
           modifie rien chez eux. Laissez vide pour désactiver.</p>
        <form action="/api/arr" method="post">
            <label for="radarr_url">URL Radarr</label>
            <input type="text" id="radarr_url" name="radarr_url" placeholder="http://192.168.1.30:7878"
                   autocorrect="off" autocomplete="off" value="{_escape(radarr_url)}"><br>
            <label for="radarr_api_key">Clé API Radarr</label>
            <input type="password" id="radarr_api_key" name="radarr_api_key"
                   autocorrect="off" autocomplete="off" value="{_escape(radarr_key)}"><br>
            <label for="sonarr_url">URL Sonarr</label>
            <input type="text" id="sonarr_url" name="sonarr_url" placeholder="http://192.168.1.30:8989"
                   autocorrect="off" autocomplete="off" value="{_escape(sonarr_url)}"><br>
            <label for="sonarr_api_key">Clé API Sonarr</label>
            <input type="password" id="sonarr_api_key" name="sonarr_api_key"
                   autocorrect="off" autocomplete="off" value="{_escape(sonarr_key)}"><br>
            {render_button("Save", "primary", {"type": "submit"})}
        </form>
        <p>La clé API se trouve dans Radarr/Sonarr sous
           <em>Settings &rarr; General &rarr; API Key</em>.</p>
        <p>{render_button("Back", "secondary", {"onclick": "location.href='/'"})}</p>
        '''
        return render_form("Connexion Radarr / Sonarr", form)

    @app.post('/api/arr')
    def arr_config_api():
        def _clean(name):
            # Normalise dès la saisie : schéma manquant, "/api" collé en trop...
            return normalize_base_url(request.forms.get(name))

        Config('Radarr').save('url', _clean('radarr_url'))
        Config('Radarr').save('api_key', (request.forms.get('radarr_api_key') or '').strip())
        Config('Sonarr').save('url', _clean('sonarr_url'))
        Config('Sonarr').save('api_key', (request.forms.get('sonarr_api_key') or '').strip())

        connected = [kind for kind in ('Radarr', 'Sonarr') if is_configured(kind)]
        if connected:
            return render_success(f"Connecté à : {', '.join(connected)}", 3)
        return render_success("Connexions Radarr/Sonarr effacées", 3)

    @app.get('/missing/movies')
    def missing_movies_ui():
        return _missing_page(
            shared_state, 'Radarr', 'films',
            "Films manquants jamais trouvés",
        )

    @app.get('/missing/series')
    def missing_series_ui():
        return _missing_page(
            shared_state, 'Sonarr', 'series',
            "Séries manquantes jamais trouvées",
            count_header="Épisodes manquants",
        )
