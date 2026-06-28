# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import json
import re
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import quote, unquote

import requests
from bottle import request, response, redirect

import quasarr.providers.html_images as images
from quasarr.downloads.linkcrypters.filecrypt import get_filecrypt_links
from quasarr.downloads.packages import delete_package
from quasarr.providers import shared_state
from quasarr.providers.html_templates import render_button, render_centered_html
from quasarr.providers.log import info, debug
from quasarr.providers.obfuscated import captcha_js, captcha_values
from quasarr.providers.statistics import StatsHelper


def js_single_quoted_string_safe(text):
    return text.replace('\\', '\\\\').replace("'", "\\'")


def setup_captcha_routes(app):
    @app.get('/captcha')
    def check_captcha():
        try:
            device = shared_state.values["device"]
        except KeyError:
            device = None
        if not device:
            return render_centered_html(f'''<h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>
            <p>JDownloader connection not established.</p>
            <p>
                {render_button("Back", "secondary", {"onclick": "location.href='/'"})}
            </p>''')

        protected = shared_state.get_db("protected").retrieve_all_titles()
        if not protected:
            return render_centered_html(f'''<h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>
            <p>No protected packages found! CAPTCHA not needed.</p>
            <p>
                {render_button("Confirm", "secondary", {"onclick": "location.href='/'"})}
            </p>''')
        else:
            package = protected[0]
            package_id = package[0]
            data = json.loads(package[1])
            title = data["title"]
            links = data["links"]
            password = data["password"]
            try:
                desired_mirror = data["mirror"]
            except KeyError:
                desired_mirror = None

            # This is set for circle CAPTCHAs
            session = data.get("session", None)

            # This is required for cutcaptcha
            rapid = [ln for ln in links if "rapidgator" in ln[1].lower()]
            others = [ln for ln in links if "rapidgator" not in ln[1].lower()]
            prioritized_links = rapid + others

            payload = {
                "package_id": package_id,
                "title": title,
                "password": password,
                "mirror": desired_mirror,
                "session": session,
                "links": prioritized_links
            }

            encoded_payload = urlsafe_b64encode(json.dumps(payload).encode()).decode()

            if session:
                debug(f'Session "{session}" found, redirecting to circle CAPTCHA')
                redirect(f"/captcha/circle?data={quote(encoded_payload)}")
            else:
                debug(f"Redirecting to cutcaptcha")
                redirect(f"/captcha/cutcaptcha?data={quote(encoded_payload)}")

            return render_centered_html(f'''<h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>
            <p>Unexpected Error!</p>
            <p>
                {render_button("Back", "secondary", {"onclick": "location.href='/'"})}
            </p>''')

    def decode_payload():
        encoded = request.query.get('data')
        try:
            decoded = urlsafe_b64decode(unquote(encoded)).decode()
            return json.loads(decoded)
        except Exception as e:
            return {"error": f"Failed to decode payload: {str(e)}"}

    @app.get('/captcha/delete/<package_id>')
    def delete_captcha_package(package_id):
        success = delete_package(shared_state, package_id)

        # Check if there are more CAPTCHAs to solve after deletion
        remaining_protected = shared_state.get_db("protected").retrieve_all_titles()
        has_more_captchas = bool(remaining_protected)

        if has_more_captchas:
            solve_button = render_button("Solve another CAPTCHA", "primary", {
                "onclick": "location.href='/captcha'",
            })
        else:
            solve_button = "<b>No more CAPTCHAs</b>"

        if success:
            return render_centered_html(f'''<h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>
            <p>Package successfully deleted!</p>
            <p>
                {solve_button}
            </p>
            <p>
                {render_button("Back", "secondary", {"onclick": "location.href='/'"})}
            </p>''')
        else:
            return render_centered_html(f'''<h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>
            <p>Failed to delete package!</p>
            <p>
                {solve_button}
            </p>
            <p>
                {render_button("Back", "secondary", {"onclick": "location.href='/'"})}
            </p>''')

    # The following routes are for cutcaptcha
    @app.get('/captcha/cutcaptcha')
    def serve_cutcaptcha():
        payload = decode_payload()

        if "error" in payload:
            return render_centered_html(f'''<h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>
            <p>{payload["error"]}</p>
            <p>
                {render_button("Back", "secondary", {"onclick": "location.href='/'"})}
            </p>''')

        package_id = payload.get("package_id")
        title = payload.get("title")
        password = payload.get("password")
        desired_mirror = payload.get("mirror")
        prioritized_links = payload.get("links")

        if not prioritized_links:
            # No links found, show an error message
            return render_centered_html(f'''
                <h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>
                <p style="max-width: 370px; word-wrap: break-word; overflow-wrap: break-word;"><b>Package:</b> {title}</p>
                <p><b>Error:</b> No download links available for this package.</p>
                <p>
                    {render_button("Delete Package", "secondary", {"onclick": f"location.href='/captcha/delete/{package_id}'"})}
                </p>
                <p>
                    {render_button("Back", "secondary", {"onclick": "location.href='/'"})}
                </p>
            ''')

        link_options = ""
        if len(prioritized_links) > 1:
            for link in prioritized_links:
                if "filecrypt." in link[0]:
                    link_options += f'<option value="{link[0]}">{link[1]}</option>'
            link_select = f'''<div id="mirrors-select">
                    <label for="link-select">Mirror:</label>
                    <select id="link-select">
                        {link_options}
                    </select>
                </div>
                <script>
                    document.getElementById("link-select").addEventListener("change", function() {{
                        var selectedLink = this.value;
                        document.getElementById("link-hidden").value = selectedLink;
                    }});
                </script>
            '''
        else:
            link_select = f'<div id="mirrors-select">Mirror: <b>{prioritized_links[0][1]}</b></div>'

        # Pre-render button HTML in Python
        solve_another_html = render_button("Solve another CAPTCHA", "primary", {"onclick": "location.href='/captcha'"})
        back_button_html = render_button("Back", "secondary", {"onclick": "location.href='/'"})

        content = render_centered_html(r'''
            <script type="text/javascript">
                var api_key = "''' + captcha_values()["api_key"] + r'''";
                var endpoint = '/' + window.location.pathname.split('/')[1] + '/' + api_key + '.html';
                var solveAnotherHtml = `<p>''' + solve_another_html + r'''</p><p>''' + back_button_html + r'''</p>`;
                var noMoreHtml = `<p><b>No more CAPTCHAs</b></p><p>''' + back_button_html + r'''</p>`;

                function handleToken(token) {
                    document.getElementById("puzzle-captcha").remove();
                    document.getElementById("mirrors-select").remove();
                    document.getElementById("delete-package-section").style.display = "none";
                    document.getElementById("back-button-section").style.display = "none";

                    // Remove width limit on result screen
                    var packageTitle = document.getElementById("package-title");
                    packageTitle.style.maxWidth = "none";

                    document.getElementById("captcha-key").innerText = 'Using result "' + token + '" to decrypt links...';
                    var link = document.getElementById("link-hidden").value;
                    const fullPath = '/captcha/decrypt-filecrypt';

                    fetch(fullPath, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ 
                            token: token,
                            ''' + f'''package_id: '{package_id}',
                            title: '{js_single_quoted_string_safe(title)}',
                            link: link,
                            password: '{password}',
                            mirror: '{desired_mirror}',
                        ''' + '''})
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            document.getElementById("captcha-key").insertAdjacentHTML('afterend', 
                                '<p>Successful!</p>');
                        } else {
                            document.getElementById("captcha-key").insertAdjacentHTML('afterend', 
                                '<p>Failed. Check console for details!</p>');
                        }

                        // Show appropriate button based on whether more CAPTCHAs exist
                        var reloadSection = document.getElementById("reload-button");
                        if (data.has_more_captchas) {
                            reloadSection.innerHTML = solveAnotherHtml;
                        } else {
                            reloadSection.innerHTML = noMoreHtml;
                        }
                        reloadSection.style.display = "block";
                    });
                }
                ''' + captcha_js() + f'''</script>
                <div>
                    <h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>
                    <p id="package-title" style="max-width: 370px; word-wrap: break-word; overflow-wrap: break-word;"><b>Package:</b> {title}</p>
                    <div id="captcha-key"></div>
                    {link_select}<br><br>
                    <input type="hidden" id="link-hidden" value="{prioritized_links[0][0]}" />
                    <div class="captcha-container">
                        <div id="puzzle-captcha" aria-style="mobile">
                            <strong>Your adblocker prevents the captcha from loading. Disable it!</strong>
                        </div>
                    </div>
                    <div id="reload-button" style="display: none;">
                    </div>
            <br>
            <div id="delete-package-section">
            <p>
                {render_button("Delete Package", "secondary", {"onclick": f"location.href='/captcha/delete/{package_id}'"})}
            </p>
            </div>
            <div id="back-button-section">
            <p>
                {render_button("Back", "secondary", {"onclick": "location.href='/'"})}
            </p>
            </div>
                </div>
                </html>''')

        return content

    @app.post('/captcha/<captcha_id>.html')
    def proxy_html(captcha_id):
        captcha_url = captcha_values()["url"]
        target_url = f"{captcha_url}/captcha/{captcha_id}.html"

        headers = {key: value for key, value in request.headers.items() if key != 'Host'}
        data = request.body.read()
        resp = requests.post(target_url, headers=headers, data=data, verify=False)

        response.content_type = resp.headers.get('Content-Type')

        content = resp.text
        content = re.sub(
            r'''<script\s+src="/(jquery(?:-ui|\.ui\.touch-punch\.min)?\.js)(?:\?[^"]*)?"></script>''',
            r'''<script src="/captcha/js/\1"></script>''',
            content
        )

        response.content_type = 'text/html'
        return content

    @app.post('/captcha/<captcha_id>.json')
    def proxy_json(captcha_id):
        captcha_url = captcha_values()["url"]
        target_url = f"{captcha_url}/captcha/{captcha_id}.json"

        headers = {key: value for key, value in request.headers.items() if key != 'Host'}
        data = request.body.read()
        resp = requests.post(target_url, headers=headers, data=data, verify=False)

        response.content_type = resp.headers.get('Content-Type')
        return resp.content

    @app.get('/captcha/js/<filename>')
    def serve_local_js(filename):
        upstream = f"{captcha_values()['url']}/{filename}"
        try:
            upstream_resp = requests.get(upstream, verify=False, stream=True)
            upstream_resp.raise_for_status()
        except requests.RequestException as e:
            response.status = 502
            return f"/* Error proxying {filename}: {e} */"

        response.content_type = 'application/javascript'
        return upstream_resp.iter_content(chunk_size=8192)

    @app.get('/captcha/<captcha_id>/<uuid>/<filename>')
    def proxy_pngs(captcha_id, uuid, filename):
        captcha_url = captcha_values()["url"]
        new_url = f"{captcha_url}/captcha/{captcha_id}/{uuid}/{filename}"

        try:
            external_response = requests.get(new_url, stream=True, verify=False)
            external_response.raise_for_status()
            response.content_type = 'image/png'
            response.headers['Content-Disposition'] = f'inline; filename="{filename}"'
            return external_response.iter_content(chunk_size=8192)

        except requests.RequestException as e:
            response.status = 502
            return f"Error fetching resource: {e}"

    @app.post('/captcha/<captcha_id>/check')
    def proxy_check(captcha_id):
        captcha_url = captcha_values()["url"]
        new_url = f"{captcha_url}/captcha/{captcha_id}/check"
        headers = {key: value for key, value in request.headers.items()}

        data = request.body.read()
        resp = requests.post(new_url, headers=headers, data=data, verify=False)

        response.status = resp.status_code
        for header in resp.headers:
            if header.lower() not in ['content-encoding', 'transfer-encoding', 'content-length', 'connection']:
                response.set_header(header, resp.headers[header])
        return resp.content

    @app.post('/captcha/decrypt-filecrypt')
    def submit_token():
        protected = shared_state.get_db("protected").retrieve_all_titles()
        if not protected:
            return {"success": False, "title": "No protected packages found! CAPTCHA not needed."}

        links = []
        title = "Unknown Package"
        try:
            data = request.json
            token = data.get('token')
            package_id = data.get('package_id')
            title = data.get('title')
            link = data.get('link')
            password = data.get('password')
            mirror = None if (mirror := data.get('mirror')) == "None" else mirror

            if token:
                info(f"Received token: {token}")
                info(f"Decrypting links for {title}")
                decrypted = get_filecrypt_links(shared_state, token, title, link, password=password, mirror=mirror)
                if decrypted:
                    if decrypted.get("status", "") == "replaced":
                        replace_url = decrypted.get("replace_url")
                        session = decrypted.get("session")
                        mirror = decrypted.get("mirror", "filecrypt")

                        links = [replace_url]

                        blob = json.dumps(
                            {
                                "title": title,
                                "links": [replace_url, mirror],
                                "size_mb": 0,
                                "password": password,
                                "mirror": mirror,
                                "session": session
                            })
                        shared_state.get_db("protected").update_store(package_id, blob)
                        info(f"Another CAPTCHA solution is required for {mirror} link: {replace_url}")

                    else:
                        links = decrypted.get("links", [])
                        info(f"Decrypted {len(links)} download links for {title}")
                        if not links:
                            raise ValueError("No download links found after decryption")
                        downloaded = shared_state.download_package(links, title, password, package_id)
                        if downloaded:
                            StatsHelper(shared_state).increment_package_with_links(links)
                            shared_state.get_db("protected").delete(package_id)
                        else:
                            links = []
                            raise RuntimeError("Submitting Download to JDownloader failed")
                else:
                    raise ValueError("No download links found")

        except Exception as e:
            info(f"Error decrypting: {e}")

        success = bool(links)
        if success:
            StatsHelper(shared_state).increment_captcha_decryptions_manual()
        else:
            StatsHelper(shared_state).increment_failed_decryptions_manual()

        # Check if there are more CAPTCHAs to solve
        remaining_protected = shared_state.get_db("protected").retrieve_all_titles()
        has_more_captchas = bool(remaining_protected)

        return {"success": success, "title": title, "has_more_captchas": has_more_captchas}

    # The following routes are for circle CAPTCHA
    @app.get('/captcha/circle')
    def serve_circle():
        payload = decode_payload()

        if "error" in payload:
            return render_centered_html(f'''<h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>
            <p>{payload["error"]}</p>
            <p>
                {render_button("Back", "secondary", {"onclick": "location.href='/'"})}
            </p>''')

        package_id = payload.get("package_id")
        session = payload.get("session")
        title = payload.get("title", "Unknown Package")
        url = payload.get("links")[0] if payload.get("links") else None

        if not url or not session or not package_id:
            response.status = 400
            return "Missing required parameters"

        return render_centered_html(f"""
        <!DOCTYPE html>
        <html>
          <body>
            <h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>
            <p><b>Package:</b> {title}</p>
            <form action="/captcha/decrypt-filecrypt-circle?url={url}&session={session}&&package_id={package_id}" method="post">
              <input type="image" src="/captcha/circle.php?session={session}" name="button" alt="Captcha">
            </form>
            <p>
                {render_button("Delete Package", "secondary", {"onclick": f"location.href='/captcha/delete/{package_id}'"})}
            </p>
            <p>
                {render_button("Back", "secondary", {"onclick": "location.href='/'"})}
            </p>
          </body>
        </html>""")

    @app.get('/captcha/circle.php')
    def proxy_circle_php():
        target_url = "https://filecrypt.cc/captcha/circle.php"

        session = request.query.get('session')
        if not session:
            response.status = 400
            return "Missing session parameter"

        headers = {'User-Agent': shared_state.values["user_agent"]}
        cookies = {'PHPSESSID': session}

        resp = requests.get(target_url, headers=headers, cookies=cookies, verify=False)

        response.content_type = resp.headers.get('Content-Type', 'application/octet-stream')
        return resp.content

    @app.post('/captcha/decrypt-filecrypt-circle')
    def proxy_form_submit():
        url = request.query.get('url')
        session = request.query.get('session')
        package_id = request.query.get('package_id')
        success = False

        if not url or not session or not package_id:
            response.status = 400
            return "Missing required parameters"

        cookies = {'PHPSESSID': session}

        headers = {
            'User-Agent': shared_state.values["user_agent"],
        }

        post_data = request.forms
        data = {k: v for k, v in post_data.items()}

        resp = requests.post(url, cookies=cookies, headers=headers, data=data, verify=False)

        response.content_type = resp.headers.get('Content-Type', 'text/html')

        solution = "You did not solve the CAPTCHA correctly. Please try again."
        match = re.search(r"top\.location\.href\s*=\s*['\"]([^'\"]+)['\"]", resp.text)
        if match:
            solution = match.group(1)
            info(f"Redirect URL: {solution}")
            try:
                redirect_resp = requests.get(solution, headers=headers, cookies=cookies, allow_redirects=True,
                                             timeout=10)
                if "expired" in redirect_resp.text.lower():
                    solution = f"The CAPTCHA session has expired. Deleting package: {package_id}"
                    info(solution)
                    shared_state.get_db("protected").delete(package_id)
                else:
                    download_link = redirect_resp.url
                    if redirect_resp.ok:
                        solution = f"Successfully resolved download link!"
                        info(solution)

                        raw_data = shared_state.get_db("protected").retrieve(package_id)
                        data = json.loads(raw_data)
                        title = data.get("title")
                        password = data.get("password", "")
                        links = [download_link]
                        downloaded = shared_state.download_package(links, title, password, package_id)
                        if downloaded:
                            StatsHelper(shared_state).increment_package_with_links(links)
                            success = True
                            shared_state.get_db("protected").delete(package_id)
                        else:
                            raise RuntimeError("Submitting Download to JDownloader failed")
                    else:
                        info(
                            f"Failed to reach redirect target. Status: {redirect_resp.status_code}, Solution: {solution}")
            except Exception as e:
                info(f"Error while resolving download link: {e}")
        else:
            if resp.url.endswith("404.html"):
                info("Your IP has been blocked by Filecrypt. Please try again later.")
            else:
                info("You did not solve the CAPTCHA correctly. Please try again.")

        if success:
            StatsHelper(shared_state).increment_captcha_decryptions_manual()
        else:
            StatsHelper(shared_state).increment_failed_decryptions_manual()

        # Check if there are more CAPTCHAs to solve
        remaining_protected = shared_state.get_db("protected").retrieve_all_titles()
        has_more_captchas = bool(remaining_protected)

        if has_more_captchas:
            solve_button = render_button("Solve another CAPTCHA", "primary", {
                "onclick": "location.href='/captcha'",
            })
        else:
            solve_button = "<b>No more CAPTCHAs</b>"

        return render_centered_html(f"""
        <!DOCTYPE html>
        <html>
          <body>
            <h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>
            <p>{solution}</p>
            <p>
                {solve_button}
            </p>
            <p>
                {render_button("Back", "secondary", {"onclick": "location.href='/'"})}
            </p>
          </body>
        </html>""")
