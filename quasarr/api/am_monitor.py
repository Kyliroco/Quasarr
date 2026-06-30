# -*- coding: utf-8 -*-

import copy
import threading
import time

from bottle import request

from quasarr.downloads.ytdlp_worker import get_all_jobs
from quasarr.providers.html_templates import render_button, render_form
from quasarr.search.sources.am import _host_tag

_SONARR_RESPONSES_KEY = "sonarr_download_client_responses"
_SONARR_RESPONSES_LOCK = threading.RLock()


def record_sonarr_response(app, mode, payload, requester=None):
    """Mémorise le JSON exact renvoyé au dernier appel queue/history."""
    with _SONARR_RESPONSES_LOCK:
        responses = dict(app.config.get(_SONARR_RESPONSES_KEY) or {})
        responses[mode] = {
            "updated_at": int(time.time()),
            "requester": requester or "unknown",
            "payload": copy.deepcopy(payload),
        }
        app.config[_SONARR_RESPONSES_KEY] = responses


def get_sonarr_responses(app):
    with _SONARR_RESPONSES_LOCK:
        return copy.deepcopy(app.config.get(_SONARR_RESPONSES_KEY) or {})


def _job_payload(package_id, job, queue_position=None):
    candidates = list(job.get("candidates") or [])
    candidate_index = int(job.get("candidate_index") or 0)
    active_candidate = job.get("active_candidate") or job.get("last_candidate") or ""
    selected_candidate = active_candidate
    if not selected_candidate and 0 <= candidate_index < len(candidates):
        selected_candidate = candidates[candidate_index]
    if not selected_candidate and candidates:
        selected_candidate = candidates[min(max(candidate_index, 0), len(candidates) - 1)]

    bytes_total = int(job.get("bytes_total") or 0)
    estimated_bytes = int(job.get("size_mb") or 0) * 1024 * 1024
    speed_bps = int(job.get("speed_bps") or 0)
    bytes_loaded = int(job.get("bytes_loaded") or 0)
    eta = job.get("eta")
    if eta is None and speed_bps > 0 and bytes_total > bytes_loaded:
        eta = int((bytes_total - bytes_loaded) / speed_bps)

    return {
        "package_id": package_id,
        "title": job.get("title", "<unknown>"),
        "status": job.get("status", "unknown"),
        "category": job.get("category", "tv"),
        "queue_position": queue_position,
        "percent": int(job.get("percent") or 0),
        "bytes_loaded": bytes_loaded,
        "bytes_total": bytes_total,
        "estimated_bytes": estimated_bytes,
        "speed_bps": speed_bps,
        "average_speed_bps": int(job.get("average_speed_bps") or 0),
        "eta": eta,
        "player": _host_tag(selected_candidate) if selected_candidate else "—",
        "candidate": selected_candidate,
        "candidate_index": candidate_index,
        "candidate_count": len(candidates),
        "added_at": int(job.get("added") or 0),
        "started_at": int(job.get("started_at") or 0),
        "updated_at": int(job.get("updated_at") or 0),
        "completed_at": int(job.get("completed_at") or 0),
        "storage": job.get("storage", ""),
        "error": job.get("error", ""),
    }


def _monitor_page():
    body = '''
    <div class="am-monitor">
      <div class="monitor-head">
        <p>Live view of anime-sama downloads. Updates every second.</p>
        <div class="refresh-controls">
          <span id="refresh-state" class="refresh-state">Connecting…</span>
          <button type="button" class="monitor-refresh" onclick="refreshMonitor()">Refresh</button>
        </div>
      </div>

      <div class="summary-grid">
        <div class="summary-card"><strong id="count-active">0</strong><span>Active</span></div>
        <div class="summary-card"><strong id="count-queued">0</strong><span>Waiting</span></div>
        <div class="summary-card"><strong id="count-completed">0</strong><span>Completed</span></div>
        <div class="summary-card"><strong id="count-failed">0</strong><span>Failed</span></div>
      </div>

      <section class="monitor-section">
        <h3>Current download</h3>
        <div id="active-job" class="empty-state">No active download.</div>
      </section>

      <section class="monitor-section">
        <h3>Waiting queue</h3>
        <div class="table-scroll">
          <table class="monitor-table">
            <thead><tr><th>#</th><th>Release</th><th>Player</th><th>Download link</th><th>Estimated size</th><th>Added</th></tr></thead>
            <tbody id="queue-body"><tr><td colspan="6" class="empty-cell">Queue is empty.</td></tr></tbody>
          </table>
        </div>
      </section>

      <section class="monitor-section">
        <h3>Recent history</h3>
        <div class="table-scroll">
          <table class="monitor-table">
            <thead><tr><th>Status</th><th>Release</th><th>Player</th><th>Download link</th><th>Size</th><th>Average speed / error</th></tr></thead>
            <tbody id="history-body"><tr><td colspan="6" class="empty-cell">No history yet.</td></tr></tbody>
          </table>
        </div>
      </section>

      <section class="monitor-section sonarr-section">
        <h3>Last responses sent to Sonarr</h3>
        <p class="section-hint">Click Refresh in Sonarr, then inspect the exact SABnzbd queue and history JSON returned by Quasarr.</p>
        <div class="sonarr-response-grid">
          <article class="response-card">
            <div class="response-head"><strong>Queue</strong><span id="sonarr-queue-meta">No request captured.</span></div>
            <pre id="sonarr-queue-payload">No queue response captured yet.</pre>
          </article>
          <article class="response-card">
            <div class="response-head"><strong>History</strong><span id="sonarr-history-meta">No request captured.</span></div>
            <pre id="sonarr-history-payload">No history response captured yet.</pre>
          </article>
        </div>
      </section>
    </div>

    <style>
      .am-monitor { width:min(1100px, 88vw); text-align:left; }
      .monitor-head { display:flex; justify-content:space-between; align-items:center; gap:1rem; flex-wrap:wrap; }
      .refresh-controls { display:flex; align-items:center; gap:.75rem; }
      .refresh-state { color:var(--secondary); font-size:.875rem; }
      .monitor-refresh { border:0; border-radius:.45rem; padding:.45rem .8rem; background:#6c757d; color:#fff; cursor:pointer; }
      .monitor-refresh:hover { filter:brightness(1.08); }
      .summary-grid { display:grid; grid-template-columns:repeat(4, minmax(110px, 1fr)); gap:.75rem; margin:1.25rem 0; }
      .summary-card { background:var(--code-bg); border-radius:.75rem; padding:.8rem 1rem; display:flex; align-items:baseline; gap:.6rem; }
      .summary-card strong { color:var(--primary); font-size:1.5rem; }
      .summary-card span { color:var(--secondary); }
      .monitor-section { margin-top:1.5rem; }
      .monitor-section h3 { font-weight:700; }
      .active-card { background:var(--code-bg); border-radius:.9rem; padding:1.1rem; }
      .active-title-row { display:flex; justify-content:space-between; gap:1rem; align-items:flex-start; }
      .active-title { font-weight:700; overflow-wrap:anywhere; }
      .status-pill { border-radius:999px; padding:.15rem .6rem; background:#0d6efd22; color:var(--primary); white-space:nowrap; }
      .progress-track { height:12px; border-radius:999px; background:#6c757d44; overflow:hidden; margin:.9rem 0 .4rem; }
      .progress-bar { height:100%; min-width:0; background:var(--primary); transition:width .35s ease; }
      .progress-label { color:var(--secondary); font-size:.875rem; text-align:right; }
      .metric-grid { display:grid; grid-template-columns:repeat(4, minmax(130px, 1fr)); gap:.75rem; margin-top:1rem; }
      .metric { display:flex; flex-direction:column; }
      .metric span { color:var(--secondary); font-size:.8rem; text-transform:uppercase; letter-spacing:.04em; }
      .metric strong { font-size:1rem; }
      .candidate-line, .error-line { margin-top:.8rem; font-size:.875rem; overflow-wrap:anywhere; }
      .candidate-line { color:var(--secondary); }
      .source-cell { min-width:230px; max-width:320px; }
      .source-cell a { display:block; font-weight:600; }
      .source-url { display:block; color:var(--secondary); font-size:.75rem; overflow-wrap:anywhere; line-height:1.35; }
      .error-line { color:#dc3545; }
      .table-scroll { overflow-x:auto; }
      .monitor-table { width:100%; border-collapse:collapse; }
      .monitor-table th, .monitor-table td { padding:.6rem .75rem; border-bottom:1px solid #6c757d35; vertical-align:top; }
      .monitor-table th { color:var(--secondary); font-size:.8rem; text-transform:uppercase; letter-spacing:.04em; }
      .release-cell { min-width:280px; overflow-wrap:anywhere; }
      .empty-state, .empty-cell { color:var(--secondary); text-align:center; padding:1.5rem; }
      .ok { color:#198754; } .failed { color:#dc3545; }
      .section-hint { color:var(--secondary); font-size:.875rem; }
      .sonarr-response-grid { display:grid; grid-template-columns:1fr 1fr; gap:1rem; }
      .response-card { min-width:0; background:var(--code-bg); border-radius:.75rem; padding:1rem; }
      .response-head { display:flex; justify-content:space-between; align-items:baseline; gap:.75rem; margin-bottom:.7rem; }
      .response-head span { color:var(--secondary); font-size:.75rem; text-align:right; overflow-wrap:anywhere; }
      .response-card pre { margin:0; max-height:420px; overflow:auto; white-space:pre; font-size:.75rem; }
      @media (max-width:760px) {
        .summary-grid { grid-template-columns:repeat(2, 1fr); }
        .metric-grid { grid-template-columns:repeat(2, 1fr); }
        .sonarr-response-grid { grid-template-columns:1fr; }
        .am-monitor { width:90vw; }
      }
    </style>

    <script>
      const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({
        '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;'
      })[char]);

      function bytes(value) {
        let size = Number(value || 0);
        if (!size) return '—';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let index = 0;
        while (size >= 1024 && index < units.length - 1) { size /= 1024; index++; }
        return `${size.toFixed(index < 2 ? 0 : 2)} ${units[index]}`;
      }

      function duration(value) {
        let seconds = Number(value);
        if (!Number.isFinite(seconds) || seconds < 0) return '—';
        seconds = Math.round(seconds);
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;
        return h ? `${h}h ${m}m ${s}s` : m ? `${m}m ${s}s` : `${s}s`;
      }

      function dateTime(epoch) {
        return epoch ? new Date(epoch * 1000).toLocaleString() : '—';
      }

      function sourceLink(value, label) {
        if (!value) return '—';
        try {
          const url = new URL(value);
          if (url.protocol !== 'http:' && url.protocol !== 'https:') return esc(value);
          return `<span class="source-cell">
            <a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">${esc(label || 'Open link')} ↗</a>
            <span class="source-url">${esc(url.href)}</span>
          </span>`;
        } catch (_) {
          return esc(value);
        }
      }

      function renderActive(job) {
        const target = document.getElementById('active-job');
        if (!job) {
          target.className = 'empty-state';
          target.textContent = 'No active download.';
          return;
        }
        const total = job.bytes_total || job.estimated_bytes;
        const percent = Math.max(0, Math.min(100, Number(job.percent || 0)));
        target.className = 'active-card';
        target.innerHTML = `
          <div class="active-title-row">
            <div class="active-title">${esc(job.title)}</div>
            <span class="status-pill">${esc(job.player)}</span>
          </div>
          <div class="progress-track"><div class="progress-bar" style="width:${percent}%"></div></div>
          <div class="progress-label">${percent}%</div>
          <div class="metric-grid">
            <div class="metric"><span>Speed</span><strong>${bytes(job.speed_bps)}/s</strong></div>
            <div class="metric"><span>Downloaded</span><strong>${bytes(job.bytes_loaded)} / ${bytes(total)}</strong></div>
            <div class="metric"><span>ETA</span><strong>${duration(job.eta)}</strong></div>
            <div class="metric"><span>Started</span><strong>${dateTime(job.started_at)}</strong></div>
          </div>
          <div class="candidate-line">Source ${job.candidate_index + 1}/${job.candidate_count}: ${sourceLink(job.candidate, 'Open download link')}</div>`;
      }

      function renderQueue(jobs) {
        const body = document.getElementById('queue-body');
        if (!jobs.length) {
          body.innerHTML = '<tr><td colspan="6" class="empty-cell">Queue is empty.</td></tr>';
          return;
        }
        body.innerHTML = jobs.map(job => `<tr>
          <td>${job.queue_position}</td>
          <td class="release-cell">${esc(job.title)}</td>
          <td>${esc(job.player)}</td>
          <td>${sourceLink(job.candidate, 'Open')}</td>
          <td>${bytes(job.estimated_bytes)}</td>
          <td>${dateTime(job.added_at)}</td>
        </tr>`).join('');
      }

      function renderHistory(jobs) {
        const body = document.getElementById('history-body');
        if (!jobs.length) {
          body.innerHTML = '<tr><td colspan="6" class="empty-cell">No history yet.</td></tr>';
          return;
        }
        body.innerHTML = jobs.slice(0, 10).map(job => `<tr title="${esc(job.error)}">
          <td class="${job.status === 'completed' ? 'ok' : 'failed'}">${job.status === 'completed' ? 'Completed' : 'Failed'}</td>
          <td class="release-cell">${esc(job.title)}</td>
          <td>${esc(job.player)}</td>
          <td>${sourceLink(job.candidate, 'Open')}</td>
          <td>${bytes(job.bytes_loaded || job.estimated_bytes)}</td>
          <td>${job.status === 'completed' ? bytes(job.average_speed_bps) + '/s' : esc(job.error || '—')}</td>
        </tr>`).join('');
      }

      function renderSonarrResponse(mode, entry) {
        const meta = document.getElementById(`sonarr-${mode}-meta`);
        const payload = document.getElementById(`sonarr-${mode}-payload`);
        if (!entry) {
          meta.textContent = 'No request captured.';
          payload.textContent = `No ${mode} response captured yet.`;
          return;
        }
        meta.textContent = `${dateTime(entry.updated_at)} — ${entry.requester || 'unknown'}`;
        payload.textContent = JSON.stringify(entry.payload, null, 2);
      }

      async function refreshMonitor() {
        const state = document.getElementById('refresh-state');
        try {
          const response = await fetch('/api/am-downloads', {cache:'no-store'});
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const data = await response.json();
          document.getElementById('count-active').textContent = data.summary.active;
          document.getElementById('count-queued').textContent = data.summary.queued;
          document.getElementById('count-completed').textContent = data.summary.completed;
          document.getElementById('count-failed').textContent = data.summary.failed;
          renderActive(data.jobs.find(job => job.status === 'downloading'));
          renderQueue(data.jobs.filter(job => job.status === 'queued'));
          renderHistory(data.jobs.filter(job => job.status === 'completed' || job.status === 'failed')
            .sort((a, b) => b.completed_at - a.completed_at));
          renderSonarrResponse('queue', data.sonarr_responses?.queue);
          renderSonarrResponse('history', data.sonarr_responses?.history);
          state.textContent = `Updated ${new Date().toLocaleTimeString()}`;
        } catch (error) {
          state.textContent = `Update failed: ${error.message}`;
        }
      }

      refreshMonitor();
      setInterval(refreshMonitor, 1000);
    </script>
    '''
    body += '<p style="text-align:center;">' + render_button(
        "Back", "secondary", {"onclick": "location.href='/'"}
    ) + '</p>'
    return render_form("anime-sama download monitor", body)


def setup_am_monitor(app, shared_state):
    @app.get('/am-downloads')
    def am_downloads_ui():
        return _monitor_page()

    @app.get('/api/am-downloads')
    def am_downloads_api():
        jobs = []
        queue_position = 0
        for package_id, job in get_all_jobs(shared_state):
            position = None
            if job.get("status") == "queued":
                queue_position += 1
                position = queue_position
            jobs.append(_job_payload(package_id, job, position))

        return {
            "updated_at": int(time.time()),
            "summary": {
                "active": sum(job["status"] == "downloading" for job in jobs),
                "queued": sum(job["status"] == "queued" for job in jobs),
                "completed": sum(job["status"] == "completed" for job in jobs),
                "failed": sum(job["status"] == "failed" for job in jobs),
            },
            "sonarr_responses": get_sonarr_responses(request.app),
            "jobs": jobs,
        }
