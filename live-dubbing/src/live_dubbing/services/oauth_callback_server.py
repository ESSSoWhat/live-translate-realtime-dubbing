"""
Local HTTP callback server for capturing OAuth redirects.

When the user completes Google sign-in in Chrome, Supabase/the backend
redirects to ``http://localhost:{port}/`` with the session tokens in the
URL fragment (``#access_token=...&refresh_token=...``).

Because HTTP servers never receive URL fragments, we serve a tiny HTML
page that reads ``window.location.hash``, extracts the tokens via
JavaScript, and POSTs them back to ``/finish`` as JSON.

Usage::

    server = OAuthCallbackServer()
    port = server.start()
    # … open browser with redirect_uri=http://localhost:{port}/ …
    tokens = server.wait_for_token(timeout=300)   # blocks
    server.stop()
    if tokens:
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


# ── HTML served to Chrome after Supabase redirects back ─────────────────────

_CAPTURE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Live Translate — Sign In</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',sans-serif;background:#111;color:#eee;
         display:flex;align-items:center;justify-content:center;height:100vh}
    .card{background:#1e1e1e;border:1px solid #333;border-radius:12px;
          padding:40px 48px;text-align:center;max-width:420px;width:90%}
    h1{font-size:1.5rem;margin-bottom:.5rem}
    p{color:#aaa;font-size:.95rem}
    .success h1{color:#4CAF50}
    .error   h1{color:#e05555}
  </style>
</head>
<body>
<div class="card" id="card">
  <h1 id="title">Completing sign-in…</h1>
  <p  id="sub">Please wait…</p>
</div>
<script>
(function () {
  // ── Implicit flow: tokens arrive in the URL fragment ──────────────────
  var hParams = new URLSearchParams(window.location.hash.substring(1));
  var access  = hParams.get('access_token');
  var refresh = hParams.get('refresh_token') || '';

  if (access) {
    document.getElementById('card').className    = 'card success';
    document.getElementById('title').textContent = 'Signed in!';
    document.getElementById('sub').textContent   = 'You can close this tab and return to Live Translate.';
    fetch('/finish', {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify({access_token: access, refresh_token: refresh})
    }).then(function(r) {
      if (!r.ok) {
        document.getElementById('card').className = 'card error';
        document.getElementById('title').textContent = 'Sign-in failed';
        document.getElementById('sub').textContent = 'Error signing in. Please try again.';
      }
    }).catch(function(err) {
      document.getElementById('card').className = 'card error';
      document.getElementById('title').textContent = 'Sign-in failed';
      document.getElementById('sub').textContent = 'Error signing in. Please try again.';
    });
    return;
  }

  // ── Wix SSO: API key arrives as a query parameter ────────────────────
  var apiKey = new URLSearchParams(window.location.search).get('api_key');

  if (apiKey) {
    document.getElementById('sub').textContent = 'Completing sign-in…';
    fetch('/finish', {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify({api_key: apiKey})
    }).then(function(r) {
      document.getElementById('card').className    = 'card success';
      document.getElementById('title').textContent = 'Signed in!';
      document.getElementById('sub').textContent   = 'You can close this tab and return to Live Translate.';
    }).catch(function() {
      document.getElementById('card').className = 'card error';
      document.getElementById('title').textContent = 'Sign-in failed';
      document.getElementById('sub').textContent = 'Could not contact app. Please try again.';
    });
    return;
  }

  // ── PKCE flow: a one-time code arrives as a query parameter ───────────
  var code = new URLSearchParams(window.location.search).get('code');

  if (code) {
    // Forward the code to the desktop app — it will exchange it with the backend.
    document.getElementById('sub').textContent = 'Completing sign-in\u2026';
    fetch('/finish', {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify({pkce_code: code})
    }).then(function(r) {
      if (!r.ok) {
        document.getElementById('card').className = 'card error';
        document.getElementById('title').textContent = 'Sign-in failed';
        document.getElementById('sub').textContent = 'Error signing in. Please try again.';
      } else {
        document.getElementById('card').className    = 'card success';
        document.getElementById('title').textContent = 'Signed in!';
        document.getElementById('sub').textContent   = 'You can close this tab and return to Live Translate.';
      }
    }).catch(function() {
      document.getElementById('card').className    = 'card error';
      document.getElementById('title').textContent = 'Sign-in failed';
      document.getElementById('sub').textContent   = 'Could not contact app. Please try again.';
    });
    return;
  }

  // ── Check for OAuth error (user denied access, etc.) ─────────────────
  var errorParam = new URLSearchParams(window.location.search).get('error');
  var errorDesc  = new URLSearchParams(window.location.search).get('error_description');
  // Also check fragment for error (some OAuth flows put it there)
  if (!errorParam) {
    errorParam = hParams.get('error');
    errorDesc  = hParams.get('error_description');
  }
  if (errorParam) {
    document.getElementById('card').className    = 'card error';
    document.getElementById('title').textContent = 'Sign-in cancelled';
    document.getElementById('sub').textContent   = errorDesc || 'Access was denied or an error occurred.';
    // Notify the app about the error
    fetch('/finish', {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify({error: errorParam, error_description: errorDesc || ''})
    });
    return;
  }

  // ── Nothing received ──────────────────────────────────────────────────
  document.getElementById('card').className    = 'card error';
  document.getElementById('title').textContent = 'Sign-in failed';
  document.getElementById('sub').textContent   = 'No token received. Please try again.';
})();
</script>
</body>
</html>
"""


class OAuthCallbackServer:
    """
    Temporary localhost HTTP server that captures the OAuth token redirect.

    The server is intentionally minimal (stdlib only, no extra deps).
    It shuts down automatically after the first successful POST to ``/finish``
    or after ``wait_for_token()`` times out.
    """

    def __init__(self) -> None:
        self._port: int = 0
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._result: dict | None = None
        self._ready = threading.Event()   # set when tokens arrive (or error)
        self._error: str | None = None

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def port(self) -> int:
        """Listening port (valid after ``start()``)."""
        return self._port

    @property
    def redirect_uri(self) -> str:
        """Return the full callback URL to pass as the OAuth redirect_uri.

        Uses trailing slash for consistency with OAuth conventions.
        """
        return f"http://localhost:{self._port}/"

    def start(self) -> int:
        """
        Bind to a free port and start the server in a daemon thread.

        Returns:
            The port number the server is listening on.
        """
        outer_self = self

        class _Handler(BaseHTTPRequestHandler):
            """Minimal handler: serve capture HTML on GET, accept tokens on POST."""

            def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]  # pylint: disable=arguments-differ
                pass  # Suppress server access logs

            def do_GET(self) -> None:  # noqa: N802
                # Serve the token-capture HTML for any GET (Supabase redirects here)
                body = _CAPTURE_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                if self.path == "/finish":
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except (ValueError, TypeError):
                        length = 0
                    if length < 0 or length > 1024 * 1024:
                        length = 0
                    raw = self.rfile.read(length)
                    try:
                        data = json.loads(raw)
                        outer_self._result = data
                    except Exception:
                        outer_self._error = "Invalid JSON from browser callback"

                    outer_self._ready.set()  # unblock wait_for_token()

                    ack = b'{"ok":true}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(ack)))
                    self.end_headers()
                    self.wfile.write(ack)
                else:
                    self.send_response(404)
                    self.end_headers()

        self._server = _ReusableHTTPServer(("127.0.0.1", 0), _Handler)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="oauth-cb"
        )
        self._thread.start()
        return self._port

    def wait_for_token(self, timeout: float = 300.0) -> dict | None:
        """
        Block the calling thread until tokens arrive or ``timeout`` seconds elapse.

        Returns:
            ``{"access_token": str, "refresh_token": str}`` on success, or
            ``None`` if timeout elapsed without a callback.

        Raises:
            ValueError: If the browser sent invalid JSON or a parse error occurred.
        """
        self._ready.wait(timeout=timeout)
        if self._error:
            raise ValueError(self._error)
        return self._result

    def stop(self) -> None:
        """Shut down the HTTP server."""
        import contextlib
        srv = self._server
        self._server = None
        if srv is not None:
            with contextlib.suppress(Exception):
                srv.shutdown()
