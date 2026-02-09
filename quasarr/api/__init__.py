# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

from bottle import Bottle

import quasarr.providers.html_images as images
from quasarr.api.arr import setup_arr_routes
from quasarr.api.captcha import setup_captcha_routes
from quasarr.api.config import setup_config
from quasarr.api.debug_dashboard import setup_debug_routes
from quasarr.api.sponsors_helper import setup_sponsors_helper_routes
from quasarr.api.statistics import setup_statistics
from quasarr.providers import shared_state
from quasarr.providers.html_templates import render_button, render_centered_html
from quasarr.providers.web_server import Server
from quasarr.storage.config import Config

from quasarr.downloads.packages.package_snapshot import PackageSnapshotter

def get_api(shared_state_dict, shared_state_lock):
    shared_state.set_state(shared_state_dict, shared_state_lock)

    app = Bottle()
    snapshotter = PackageSnapshotter(shared_state, interval=30).start()
    app.config['snapshotter'] = snapshotter
    setup_arr_routes(app)
    setup_captcha_routes(app)
    setup_config(app, shared_state)
    setup_statistics(app, shared_state)
    setup_sponsors_helper_routes(app)
    setup_debug_routes(app)

    @app.get('/')
    def index():
        protected = shared_state.get_db("protected").retrieve_all_titles()
        api_key = Config('API').get('key')

        captcha_hint = ""
        if protected:
            plural = 's' if len(protected) > 1 else ''
            captcha_hint += f"""
            <div class="section">
                <h2>🔒 Link{plural} waiting for CAPTCHA solution</h2>
                """

            if not shared_state.values.get("helper_active"):
                captcha_hint += f"""
                <p>
                    <a href="https://github.com/rix1337/Quasarr?tab=readme-ov-file#sponsorshelper" target="_blank">
                        Sponsors get automated CAPTCHA solutions!
                    </a>
                </p>
                """

            plural = 's' if len(protected) > 1 else ''
            captcha_hint += f"""
                <p>{render_button(f"Solve CAPTCHA{plural}", 'primary', {'onclick': "location.href='/captcha'"})}</p>
            </div>
            <hr>
            """

        info = f"""
        <h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>

        {captcha_hint}

        <div class="section">
            <h2>📖 Setup Instructions</h2>
            <p>
                <a href="https://github.com/rix1337/Quasarr?tab=readme-ov-file#instructions" target="_blank">
                    Refer to the README for detailed instructions.
                </a>
            </p>
        </div>

        <hr>

        <div class="section">
            <h2>⚙️ API Configuration</h2>
            <p>Use the URL and API Key below to set up a <strong>Newznab Indexer</strong> and <strong>SABnzbd Download Client</strong> in Radarr/Sonarr:</p>

            <details id="apiDetails">
                <summary id="apiSummary">Show API Settings</summary>
                <div class="api-settings">

                    <h3>URL</h3>
                    <div class="url-wrapper">
                      <input id="urlInput" class="copy-input" type="text" readonly value="{shared_state.values['internal_address']}" />
                      <button id="copyUrl" class="btn-primary small">Copy</button>
                    </div>

                    <h3>API Key</h3>
                    <div class="api-key-wrapper">
                      <input id="apiKeyInput" class="copy-input" type="password" readonly value="{api_key}" />
                      <button id="toggleKey" class="btn-secondary small">Show</button>
                      <button id="copyKey" class="btn-primary small">Copy</button>
                    </div>

                    <p>{render_button("Regenerate API key", "secondary", {"onclick": "if(confirm('Regenerate API key?')) location.href='/regenerate-api-key';"})}</p>
                </div>
            </details>
        </div>

        <hr>

        <div class="section">
            <h2>🔧 Quick Actions</h2>
            <p><button class="btn-primary" onclick="location.href='/hostnames'">Update Hostnames</button></p>
            <p><button class="btn-primary" onclick="location.href='/statistics'">View Statistics</button></p>
            <p><button class="btn-primary" onclick="location.href='/debug/'">Debug Dashboard</button></p>
        </div>

        <style>
            .section {{ margin: 20px 0; }}
            .api-settings {{ padding: 15px 0; }}
            hr {{ margin: 25px 0; border: none; border-top: 1px solid #ddd; }}
            details {{ margin: 10px 0; }}
            summary {{ 
                cursor: pointer; 
                padding: 8px 0; 
                font-weight: 500;
            }}
            summary:hover {{ 
                color: #0066cc; 
            }}
        </style>

        <script>
          const urlInput = document.getElementById('urlInput');
          const copyUrlBtn = document.getElementById('copyUrl');

          if (copyUrlBtn) {{
            copyUrlBtn.onclick = () => {{
              urlInput.select();
              document.execCommand('copy');
              copyUrlBtn.innerText = 'Copied!';
              setTimeout(() => {{ copyUrlBtn.innerText = 'Copy'; }}, 2000);
            }};
          }}

          const apiInput = document.getElementById('apiKeyInput');
          const toggleBtn = document.getElementById('toggleKey');
          const copyBtn = document.getElementById('copyKey');

          if (toggleBtn) {{
            toggleBtn.onclick = () => {{
              const isHidden = apiInput.type === 'password';
              apiInput.type = isHidden ? 'text' : 'password';
              toggleBtn.innerText = isHidden ? 'Hide' : 'Show';
            }};
          }}

          if (copyBtn) {{
            copyBtn.onclick = () => {{
              apiInput.type = 'text';
              apiInput.select();
              document.execCommand('copy');
              copyBtn.innerText = 'Copied!';
              toggleBtn.innerText = 'Hide';
              setTimeout(() => {{ copyBtn.innerText = 'Copy'; }}, 2000);
            }};
          }}

          // Handle details toggle
          const apiDetails = document.getElementById('apiDetails');
          const apiSummary = document.getElementById('apiSummary');

          if (apiDetails && apiSummary) {{
            apiDetails.addEventListener('toggle', () => {{
              if (apiDetails.open) {{
                apiSummary.textContent = 'Hide API Settings';
              }} else {{
                apiSummary.textContent = 'Show API Settings';
              }}
            }});
          }}
        </script>
        """
        return render_centered_html(info)

    @app.get('/regenerate-api-key')
    def regenerate_api_key():
        api_key = shared_state.generate_api_key()
        return f"""
        <script>
          alert('API key replaced with: {api_key}');
          window.location.href = '/';
        </script>
        """

    Server(app, listen='0.0.0.0', port=shared_state.values["port"]).serve_forever()
