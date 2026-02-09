# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import json

import quasarr.providers.html_images as images
from quasarr.providers.html_templates import render_centered_html
from quasarr.providers.log import get_log_entries, get_log_stats


def setup_debug_routes(app):
    # ------------------------------------------------------------------
    # JSON API – used by the dashboard JS and available for external use
    # ------------------------------------------------------------------

    @app.get('/debug/api/logs')
    def api_logs():
        from bottle import request, response
        response.content_type = 'application/json'

        limit = int(request.query.get('limit', 200))
        level = request.query.get('level', '') or None
        source = request.query.get('source', '') or None
        search = request.query.get('search', '') or None
        since_id = int(request.query.get('since_id', 0))

        entries = get_log_entries(
            limit=limit,
            level=level,
            source=source,
            search=search,
            since_id=since_id,
        )
        return json.dumps({"entries": entries})

    @app.get('/debug/api/stats')
    def api_stats():
        from bottle import response
        response.content_type = 'application/json'
        return json.dumps(get_log_stats())

    # ------------------------------------------------------------------
    # HTML dashboard
    # ------------------------------------------------------------------

    @app.get('/debug/')
    @app.get('/debug')
    def debug_dashboard():
        content = f"""
        <h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr Debug</h1>

        <div class="debug-controls">
            <div class="filter-row">
                <select id="levelFilter">
                    <option value="">All levels</option>
                    <option value="ERROR">ERROR</option>
                    <option value="WARNING">WARNING</option>
                    <option value="INFO">INFO</option>
                    <option value="DEBUG">DEBUG</option>
                </select>
                <select id="sourceFilter">
                    <option value="">All sources</option>
                    <option value="zt">ZT (search)</option>
                    <option value="zt-dl">ZT (download)</option>
                    <option value="api">API</option>
                </select>
                <input id="searchFilter" type="text" placeholder="Search..." />
                <button class="btn-primary small" onclick="fetchLogs()">Filter</button>
                <button id="autoRefreshBtn" class="btn-secondary small" onclick="toggleAutoRefresh()">Auto-refresh: OFF</button>
                <button class="btn-secondary small" onclick="location.href='/'">Back</button>
            </div>
        </div>

        <div id="statsBar" class="stats-bar"></div>

        <div id="logContainer" class="log-container">
            <div class="log-loading">Loading...</div>
        </div>

        <!-- Modal for entry details -->
        <div id="detailModal" class="modal" onclick="if(event.target===this)closeModal()">
            <div class="modal-content">
                <button class="modal-close" onclick="closeModal()">&times;</button>
                <pre id="detailContent"></pre>
            </div>
        </div>

        <style>
            .debug-controls {{
                margin: 15px 0;
            }}
            .filter-row {{
                display: flex;
                gap: 8px;
                flex-wrap: wrap;
                justify-content: center;
                align-items: center;
            }}
            .filter-row select, .filter-row input {{
                width: auto;
                min-width: 120px;
                padding: 6px 10px;
                font-size: 0.875rem;
                border-radius: 6px;
            }}
            .filter-row button.small {{
                padding: 6px 12px;
                font-size: 0.875rem;
            }}
            .stats-bar {{
                display: flex;
                gap: 12px;
                justify-content: center;
                flex-wrap: wrap;
                margin: 12px 0;
                font-size: 0.8rem;
            }}
            .stats-bar .stat {{
                padding: 4px 10px;
                border-radius: 12px;
                font-weight: 600;
            }}
            .stat-searches {{ background: #e3f2fd; color: #1565c0; }}
            .stat-found {{ background: #e8f5e9; color: #2e7d32; }}
            .stat-filtered {{ background: #fff3e0; color: #e65100; }}
            .stat-errors {{ background: #ffebee; color: #c62828; }}
            .stat-total {{ background: var(--code-bg); color: var(--fg-color); }}

            @media (prefers-color-scheme: dark) {{
                .stat-searches {{ background: #0d47a1; color: #bbdefb; }}
                .stat-found {{ background: #1b5e20; color: #c8e6c9; }}
                .stat-filtered {{ background: #bf360c; color: #ffe0b2; }}
                .stat-errors {{ background: #b71c1c; color: #ffcdd2; }}
            }}

            .log-container {{
                max-height: 70vh;
                overflow-y: auto;
                text-align: left;
                font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
                font-size: 0.8rem;
                line-height: 1.5;
                border: 1px solid var(--card-shadow);
                border-radius: 8px;
                background: var(--code-bg);
            }}
            .log-entry {{
                padding: 6px 12px;
                border-bottom: 1px solid rgba(128,128,128,0.15);
                cursor: pointer;
                transition: background 0.1s;
                display: flex;
                gap: 8px;
                align-items: baseline;
            }}
            .log-entry:hover {{
                background: rgba(128,128,128,0.1);
            }}
            .log-ts {{
                color: var(--secondary);
                white-space: nowrap;
                flex-shrink: 0;
                font-size: 0.75rem;
            }}
            .log-level {{
                font-weight: 700;
                white-space: nowrap;
                flex-shrink: 0;
                min-width: 55px;
                text-align: center;
                padding: 1px 6px;
                border-radius: 4px;
                font-size: 0.7rem;
            }}
            .log-level-DEBUG {{ color: #90a4ae; }}
            .log-level-INFO {{ color: #42a5f5; }}
            .log-level-WARNING {{ background: #fff3e0; color: #e65100; }}
            .log-level-ERROR {{ background: #ffebee; color: #c62828; }}
            @media (prefers-color-scheme: dark) {{
                .log-level-WARNING {{ background: #4e342e; color: #ffab91; }}
                .log-level-ERROR {{ background: #4e1a1a; color: #ef9a9a; }}
            }}
            .log-source {{
                color: #7e57c2;
                font-weight: 600;
                white-space: nowrap;
                flex-shrink: 0;
            }}
            .log-msg {{
                flex: 1;
                word-break: break-word;
                overflow-wrap: anywhere;
            }}
            .log-data-badge {{
                background: rgba(128,128,128,0.2);
                color: var(--secondary);
                font-size: 0.65rem;
                padding: 1px 5px;
                border-radius: 3px;
                flex-shrink: 0;
            }}
            .log-loading {{
                text-align: center;
                padding: 40px;
                color: var(--secondary);
            }}
            .modal {{
                display: none;
                position: fixed;
                top: 0; left: 0; right: 0; bottom: 0;
                background: rgba(0,0,0,0.5);
                z-index: 1000;
                justify-content: center;
                align-items: center;
            }}
            .modal.active {{
                display: flex;
            }}
            .modal-content {{
                background: var(--card-bg);
                border-radius: 12px;
                padding: 24px;
                max-width: 700px;
                width: 90%;
                max-height: 80vh;
                overflow-y: auto;
                position: relative;
                box-shadow: 0 8px 32px rgba(0,0,0,0.3);
            }}
            .modal-close {{
                position: absolute;
                top: 8px; right: 12px;
                background: none;
                border: none;
                font-size: 1.5rem;
                cursor: pointer;
                color: var(--fg-color);
                margin: 0;
                padding: 4px;
            }}
            #detailContent {{
                white-space: pre-wrap;
                word-break: break-word;
                font-size: 0.8rem;
                margin-top: 12px;
            }}
        </style>

        <script>
            let autoRefresh = false;
            let refreshTimer = null;
            let lastId = 0;

            function fetchStats() {{
                fetch('/debug/api/stats')
                    .then(r => r.json())
                    .then(stats => {{
                        const bar = document.getElementById('statsBar');
                        bar.innerHTML = `
                            <span class="stat stat-total">${{stats.total}} total</span>
                            <span class="stat stat-searches">${{stats.searches}} searches</span>
                            <span class="stat stat-found">${{stats.results_found}} found</span>
                            <span class="stat stat-filtered">${{stats.results_filtered}} filtered</span>
                            <span class="stat stat-errors">${{stats.error}} errors</span>
                        `;
                    }});
            }}

            function fetchLogs() {{
                const level = document.getElementById('levelFilter').value;
                const source = document.getElementById('sourceFilter').value;
                const search = document.getElementById('searchFilter').value;

                const params = new URLSearchParams();
                if (level) params.set('level', level);
                if (source) params.set('source', source);
                if (search) params.set('search', search);
                params.set('limit', '300');

                fetch('/debug/api/logs?' + params.toString())
                    .then(r => r.json())
                    .then(data => {{
                        renderLogs(data.entries);
                        if (data.entries.length > 0) {{
                            lastId = Math.max(...data.entries.map(e => e.id));
                        }}
                    }});

                fetchStats();
            }}

            function renderLogs(entries) {{
                const container = document.getElementById('logContainer');
                if (!entries.length) {{
                    container.innerHTML = '<div class="log-loading">No log entries found</div>';
                    return;
                }}

                let html = '';
                for (const entry of entries) {{
                    const ts = entry.timestamp ? entry.timestamp.split('T')[1].substring(0, 8) : '';
                    const date = entry.timestamp ? entry.timestamp.split('T')[0] : '';
                    const src = entry.source ? entry.source.toUpperCase() : '';
                    const hasData = entry.data && Object.keys(entry.data).length > 0;
                    const dataAttr = hasData ? ' data-detail="' + escapeAttr(JSON.stringify(entry, null, 2)) + '"' : '';
                    const badge = hasData ? '<span class="log-data-badge">+data</span>' : '';

                    html += `<div class="log-entry" onclick="showDetail(this)"${{dataAttr}}>
                        <span class="log-ts" title="${{date}}">${{ts}}</span>
                        <span class="log-level log-level-${{entry.level}}">${{entry.level}}</span>
                        <span class="log-source">${{src}}</span>
                        <span class="log-msg">${{escapeHtml(entry.message)}}</span>
                        ${{badge}}
                    </div>`;
                }}
                container.innerHTML = html;
            }}

            function escapeHtml(text) {{
                const d = document.createElement('div');
                d.textContent = text || '';
                return d.innerHTML;
            }}

            function escapeAttr(text) {{
                return (text || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            }}

            function showDetail(el) {{
                const raw = el.getAttribute('data-detail');
                if (!raw) return;
                try {{
                    const obj = JSON.parse(raw);
                    document.getElementById('detailContent').textContent = JSON.stringify(obj, null, 2);
                    document.getElementById('detailModal').classList.add('active');
                }} catch(e) {{
                    document.getElementById('detailContent').textContent = raw;
                    document.getElementById('detailModal').classList.add('active');
                }}
            }}

            function closeModal() {{
                document.getElementById('detailModal').classList.remove('active');
            }}

            function toggleAutoRefresh() {{
                autoRefresh = !autoRefresh;
                const btn = document.getElementById('autoRefreshBtn');
                btn.textContent = autoRefresh ? 'Auto-refresh: ON' : 'Auto-refresh: OFF';
                btn.className = autoRefresh ? 'btn-primary small' : 'btn-secondary small';

                if (autoRefresh) {{
                    refreshTimer = setInterval(fetchLogs, 5000);
                }} else {{
                    clearInterval(refreshTimer);
                    refreshTimer = null;
                }}
            }}

            document.getElementById('searchFilter').addEventListener('keydown', function(e) {{
                if (e.key === 'Enter') fetchLogs();
            }});

            // Initial load
            fetchLogs();
        </script>
        """
        return render_centered_html(content)
