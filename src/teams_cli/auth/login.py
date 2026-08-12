"""Bookmarklet-driven login: localhost-hash bypass + MSAL localStorage parser.

Flow:
1. CLI binds a temporary HTTP server on a free 127.0.0.1 port.
2. CLI prints a one-line JavaScript bookmarklet that, when clicked, calls
   ``window.open('http://127.0.0.1:PORT/auth#<payload>', '_blank')``.
3. User opens https://teams.microsoft.com/v2/ in their normal browser, signs in,
   then clicks the bookmarklet.
4. The browser navigates a new tab to the CLI's localhost ``/auth`` route. The
   MSAL localStorage entries travel in the URL fragment (never sent over the
   network from the Teams page).
5. The localhost HTML page reads its own ``location.hash`` and POSTs the
   payload to ``/submit`` — same-origin, so Teams' CSP is bypassed entirely.
6. CLI receives the POST, shuts down the server, parses the MSAL.js entries,
   and writes ``~/.config/teams-cli/credentials.json``.

Why this bypasses Teams' strict CSP:

- ``window.open()`` is navigation, not a ``connect-src`` connection — CSP
  does not restrict it.
- The URL fragment (``#...``) stays client-side and is never transmitted in
  the HTTP request from the Teams page.
- The new tab loads HTML *from the CLI's own server*, so its origin is
  ``http://127.0.0.1:PORT`` — the ``fetch('/submit', ...)`` is same-origin
  and the page has no strict CSP of its own.
"""

from __future__ import annotations

import base64
import binascii
import http.server
import json
import logging
import sys
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from teams_cli.auth.token_store import Credentials, save
from teams_cli.errors import UserError

log = logging.getLogger(__name__)

TEAMS_LOGIN_URL = "https://teams.microsoft.com/v2/"


# The bookmarklet opens a new tab on our local server with the payload in the
# URL fragment. ``window.open`` is not a connect-src and the fragment is never
# transmitted from the Teams page, so Teams' CSP doesn't apply.
#
# Only MSAL credential/account entries are sent. Teams keeps megabytes of app
# state in localStorage; shipping all of it overflows the browser's URL length
# limit and the fragment arrives truncated (JSONDecodeError mid-string). The
# filter matches exactly what ``parse_localstorage`` consumes — RefreshToken,
# IdToken, and account entries — and skips AccessToken blobs. Falls back to the
# full dump if nothing matches, in case MSAL's schema shifts.
_BOOKMARKLET_TEMPLATE = (
    "javascript:(function(){"
    "var o={},i,k,v;"
    "for(i=0;i<localStorage.length;i++){"
    "k=localStorage.key(i);v=localStorage.getItem(k);"
    "if(v&&(v.indexOf('\"RefreshToken\"')>-1||v.indexOf('\"IdToken\"')>-1||"
    "(v.indexOf('\"homeAccountId\"')>-1&&v.indexOf('\"username\"')>-1)))o[k]=v;"
    "}"
    "if(!Object.keys(o).length)o=Object.fromEntries(Object.entries(localStorage));"
    "window.open('http://127.0.0.1:__PORT__/auth#'+"
    "encodeURIComponent(JSON.stringify(o)),'_blank');"
    "})()"
)

# Served on GET /auth. The inline script reads the URL hash and POSTs the
# payload back to /submit on the same origin (the CLI's own server) — so the
# CSP restrictions on teams.microsoft.com no longer apply here.
_AUTH_PAGE_HTML = """<html>
<head><title>Teams CLI: Authenticating...</title></head>
<body style="font-family: sans-serif; text-align: center; padding-top: 50px;">
<h1 id="status">Teams CLI: Processing...</h1>
<p>Do not close this tab until you see a success message.</p>
<script>
var s = document.getElementById("status");
var payload = "";
try {
  payload = decodeURIComponent(window.location.hash.substring(1));
} catch (e) {
  s.innerText = "Error: Failed to decode payload.";
}
if (payload) {
  fetch('/submit', { method: 'POST', body: payload }).then(function(r) {
    if (r.ok) {
      s.innerText = "Success! You can close this tab.";
      s.style.color = "green";
    } else {
      s.innerText = "Error: CLI rejected the payload.";
      s.style.color = "red";
    }
  }).catch(function() {
    s.innerText = "Error: Failed to send to CLI.";
    s.style.color = "red";
  });
}
</script>
</body>
</html>
"""


@dataclass(frozen=True)
class ParsedSession:
    refresh_token: str
    client_id: str
    tenant_id: str
    home_account_id: str
    username: str
    id_token_claims: dict[str, Any] = field(default_factory=dict)


def _decode_jwt_payload(jwt: str) -> dict[str, Any]:
    """Decode a JWT payload (NO signature verification — we trust the browser source)."""
    try:
        _header, payload, _sig = jwt.split(".", 2)
    except ValueError:
        return {}
    pad = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + pad)
    except (ValueError, binascii.Error):
        return {}
    try:
        result = json.loads(decoded)
    except json.JSONDecodeError:
        return {}
    if not isinstance(result, dict):
        return {}
    return result


def _rt_sort_key(entry: dict[str, Any]) -> tuple[int, int]:
    """Order for picking the best refresh-token candidate.

    Higher tuple wins. Primary: ``lastUpdatedAt`` (newer first) — avoids stale
    duplicates left behind by older MSAL schema versions. Tiebreaker: FOCI
    (familyId == "1") wins, since FOCI tokens are usable across the Microsoft
    client family.
    """
    try:
        last = int(entry.get("lastUpdatedAt", 0))
    except (TypeError, ValueError):
        last = 0
    foci = 1 if str(entry.get("familyId", "")) == "1" else 0
    return (last, foci)


def parse_localstorage(storage: dict[str, str]) -> ParsedSession:
    """Pull the best refresh_token + metadata out of MSAL.js storage.

    Selection mirrors outlook-cli's approach: among all RT entries, prefer the
    most recently issued one (``lastUpdatedAt``), with FOCI as a tiebreaker.
    The previous "first-FOCI-wins" picker was non-deterministic in dict
    iteration order and could grab a stale entry whose client is no longer
    preauthorized for the scopes we need.
    """
    refresh_candidates: list[dict[str, Any]] = []
    id_tokens: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []

    for _key, value in storage.items():
        try:
            entry = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(entry, dict):
            continue
        credential_type = entry.get("credentialType")
        if credential_type == "RefreshToken" and entry.get("secret"):
            refresh_candidates.append(entry)
        elif credential_type == "IdToken":
            id_tokens.append(entry)
        elif "homeAccountId" in entry and "username" in entry and "localAccountId" in entry:
            accounts.append(entry)

    if not refresh_candidates:
        raise LookupError("No MSAL refresh-token entry found in localStorage")

    rt_entry = max(refresh_candidates, key=_rt_sort_key)
    if log.isEnabledFor(logging.INFO):
        cids = sorted({str(e.get("clientId", "?")) for e in refresh_candidates})
        log.info(
            "RT candidates by clientId: %s; picked clientId=%s familyId=%s lastUpdatedAt=%s",
            cids,
            rt_entry.get("clientId"),
            rt_entry.get("familyId"),
            rt_entry.get("lastUpdatedAt"),
        )

    rt = str(rt_entry["secret"])
    client_id = str(rt_entry.get("clientId", ""))
    home_account_id = str(rt_entry.get("homeAccountId", ""))
    tenant_id = home_account_id.split(".", 1)[1] if "." in home_account_id else ""

    # Prefer the IdToken whose clientId matches the RT we picked.
    chosen_id: dict[str, Any] = {}
    for it in id_tokens:
        if it.get("clientId") == client_id:
            chosen_id = it
            break
    if not chosen_id and id_tokens:
        chosen_id = id_tokens[0]

    id_token_claims = _decode_jwt_payload(str(chosen_id.get("secret", "")))

    # Prefer username from the account entry that matches our home_account_id.
    username = ""
    for acc in accounts:
        if acc.get("homeAccountId") == home_account_id:
            username = str(acc.get("username", ""))
            break
    if not username:
        username = str(id_token_claims.get("preferred_username", ""))

    return ParsedSession(
        refresh_token=rt,
        client_id=client_id,
        tenant_id=tenant_id,
        home_account_id=home_account_id,
        username=username,
        id_token_claims=id_token_claims,
    )


# ---------- bookmarklet -> localhost hash capture ----------


class _AuthServer(http.server.HTTPServer):
    """HTTPServer with typed slots for the captured payload + error."""

    captured_storage: dict[str, Any] | None = None
    capture_error: str | None = None


class BookmarkletAuthHandler(http.server.BaseHTTPRequestHandler):
    server: _AuthServer

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path.startswith("/auth"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_AUTH_PAGE_HTML.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path == "/submit":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)

            try:
                parsed = json.loads(post_data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                # Byte count makes a truncated fragment obvious in the error.
                self.server.capture_error = f"{exc} (received {len(post_data)} bytes)"
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
            else:
                self.server.captured_storage = parsed
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

            # Shut down the server on a separate thread to unblock serve_forever.
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib API)
        # Silence stderr access logs from the test server.
        pass


def capture_session(
    *,
    port: int = 49152,
    timeout_seconds: int = 300,  # noqa: ARG001 (reserved for future use)
) -> dict[str, str]:
    """Print bookmarklet instructions, start a one-shot localhost server, return the payload.

    Args:
        port: starting port; the server tries up to ``port + 100`` if busy.
        timeout_seconds: reserved for future use; current implementation blocks
            on ``serve_forever()`` and exits on Ctrl+C.

    Returns:
        The parsed localStorage dict (string keys → string values).

    Raises:
        UserError: no free port, invalid JSON, missing data, or wrong shape.
    """
    server: _AuthServer | None = None
    chosen_port = port
    for p in range(port, port + 100):
        try:
            server = _AuthServer(("127.0.0.1", p), BookmarkletAuthHandler)
            chosen_port = p
            break
        except OSError:
            continue

    if server is None:
        raise UserError(
            "Could not bind to a local port for the bookmarklet server "
            f"(tried {port}..{port + 99})."
        )

    bookmarklet = _BOOKMARKLET_TEMPLATE.replace("__PORT__", str(chosen_port))

    bar = "=" * 64
    print("", file=sys.stderr)
    print(bar, file=sys.stderr)
    print(" Sign in to Microsoft Teams", file=sys.stderr)
    print(bar, file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "  1. Create a new bookmark in your browser (drag it to your bookmarks bar).",
        file=sys.stderr,
    )
    print("     Name it: Teams CLI Login", file=sys.stderr)
    print(f"     URL: {bookmarklet}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  2. Open this URL in your browser:", file=sys.stderr)
    print(f"       {TEAMS_LOGIN_URL}", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "  3. Sign in normally (corporate SSO / MFA). Once Teams loads, click the",
        file=sys.stderr,
    )
    print("     'Teams CLI Login' bookmark you just made.", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "     A new tab will open on 127.0.0.1 and show a success message when",
        file=sys.stderr,
    )
    print("     the payload has been captured. Then return to this terminal.", file=sys.stderr)
    print(bar, file=sys.stderr)
    print("\nWaiting for authentication (press Ctrl+C to cancel)...", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLogin cancelled.", file=sys.stderr)
        sys.exit(1)
    finally:
        server.server_close()

    if server.capture_error:
        raise UserError(f"Failed to parse data from bookmarklet: {server.capture_error}")

    storage = server.captured_storage
    if storage is None:
        raise UserError("No data received from bookmarklet.")
    if not isinstance(storage, dict):
        raise UserError("Received JSON is not an object.")

    return {str(k): str(v) for k, v in storage.items()}


def persist_session(
    parsed: ParsedSession,
    credentials_path: Path,
    shared_from: str | None = None,
) -> Credentials:
    creds = Credentials(
        version=1,
        acquired_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        tenant_id=parsed.tenant_id,
        client_id=parsed.client_id,
        home_account_id=parsed.home_account_id,
        username=parsed.username,
        refresh_token=parsed.refresh_token,
        shared_from=shared_from,
        id_token_claims=parsed.id_token_claims,
    )
    save(creds, credentials_path)
    return creds
