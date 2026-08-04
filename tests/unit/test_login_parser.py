from __future__ import annotations

import base64
import http.client
import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from teams_cli.auth.login import (
    BookmarkletAuthHandler,
    ParsedSession,
    _AuthServer,
    capture_session,
    parse_localstorage,
    persist_session,
)
from teams_cli.errors import UserError


def _make_id_token_claims() -> dict[str, Any]:
    return {
        "name": "Pouria Mortezaagha",
        "preferred_username": "pouriamortezaagha7@gmail.com",
        "oid": "99999999-8888-7777-6666-555555555555",
        "tid": "11111111-2222-3333-4444-555555555555",
    }


def _make_synthetic_id_token() -> str:
    """Construct a fake JWT whose payload contains realistic claims."""
    header = base64.urlsafe_b64encode(b'{"typ":"JWT","alg":"none"}').rstrip(b"=").decode()
    payload = (
        base64.urlsafe_b64encode(json.dumps(_make_id_token_claims()).encode()).rstrip(b"=").decode()
    )
    return f"{header}.{payload}.signature"


# ---------- parser-side tests (unchanged) ----------


def test_parse_extracts_refresh_token_and_metadata(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    storage_any = load_fixture("msal_localstorage.json")
    storage: dict[str, str] = {str(k): str(v) for k, v in storage_any.items()}
    # Patch in a realistic id-token JWT so the parser can decode claims.
    for k, v in list(storage.items()):
        if "-idtoken-" in k:
            entry = json.loads(v)
            entry["secret"] = _make_synthetic_id_token()
            storage[k] = json.dumps(entry)

    parsed = parse_localstorage(storage)
    assert isinstance(parsed, ParsedSession)
    assert parsed.refresh_token == "1.AXYA-FAKE-TEAMS-RT"
    assert parsed.client_id == "5e3ce6c0-2b1f-4285-8d4b-75ee78787346"
    assert parsed.tenant_id == "11111111-2222-3333-4444-555555555555"
    assert parsed.home_account_id.startswith("99999999-8888-7777-6666-555555555555")
    assert parsed.username == "pouriamortezaagha7@gmail.com"
    assert parsed.id_token_claims["oid"] == "99999999-8888-7777-6666-555555555555"


def test_parse_handles_missing_refresh_token() -> None:
    storage = {"msal.account.keys": "[]"}
    with pytest.raises(LookupError):
        parse_localstorage(storage)


def test_parse_ignores_non_msal_entries() -> None:
    storage = {
        "some.unrelated.key": "ignore-me",
        "x-y-refreshtoken-CID-": (
            '{"credentialType":"RefreshToken","clientId":"CID","secret":"RT1",'
            '"homeAccountId":"OID.TID","environment":"login.windows.net"}'
        ),
    }
    # Without an idtoken entry, we can still extract the RT but claims will be empty.
    parsed = parse_localstorage(storage)
    assert parsed.refresh_token == "RT1"
    assert parsed.client_id == "CID"
    assert parsed.tenant_id == "TID"


def test_parse_with_multiple_rts_picks_the_one_with_familyid(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    storage_any = load_fixture("msal_localstorage.json")
    storage: dict[str, str] = {str(k): str(v) for k, v in storage_any.items()}
    # Inject a second, non-FOCI RT — parser should prefer the FOCI one.
    storage["other-key-refreshtoken-DIFFERENT-CID-"] = json.dumps(
        {
            "credentialType": "RefreshToken",
            "clientId": "DIFFERENT-CID",
            "secret": "1.NOT-FOCI",
            "homeAccountId": "other.tid",
            "environment": "login.windows.net",
            # no familyId
        }
    )
    parsed = parse_localstorage(storage)
    assert parsed.refresh_token == "1.AXYA-FAKE-TEAMS-RT"


def test_persist_session_writes_credentials(tmp_path: Path) -> None:
    parsed = ParsedSession(
        refresh_token="rt",
        client_id="cid",
        tenant_id="tid",
        home_account_id="oid.tid",
        username="u@example.com",
        id_token_claims={"oid": "oid"},
    )
    path = tmp_path / "credentials.json"
    creds = persist_session(parsed, path, shared_from="outlook-cli")
    written = json.loads(path.read_text("utf-8"))
    assert written["refresh_token"] == "rt"
    assert written["shared_from"] == "outlook-cli"
    assert creds.username == "u@example.com"


# ---------- server-side capture_session tests ----------


def _pick_free_port() -> int:
    """Pick a free port to start the capture range from; avoids stomping on the next test."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


@pytest.fixture
def capture_thread() -> Iterator[dict[str, Any]]:
    """Run capture_session in a background thread; the test pokes the server itself."""
    state: dict[str, Any] = {"result": None, "error": None, "port": None}
    start_port = _pick_free_port()
    state["port"] = start_port

    def runner() -> None:
        try:
            state["result"] = capture_session(port=start_port)
        except BaseException as exc:  # noqa: BLE001
            state["error"] = exc

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    # Wait for the HTTP server to become bindable / ready.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", state["port"], timeout=0.5)
            conn.request("GET", "/auth")
            resp = conn.getresponse()
            resp.read()
            conn.close()
            break
        except OSError:
            time.sleep(0.05)
    else:  # pragma: no cover — defensive
        pytest.fail("capture_session server never became reachable")

    try:
        yield state
    finally:
        # If the thread is still alive, hit /submit with empty body to unblock it.
        if t.is_alive():
            try:
                conn = http.client.HTTPConnection("127.0.0.1", state["port"], timeout=0.5)
                conn.request("POST", "/submit", body=b"{}")
                conn.getresponse().read()
                conn.close()
            except OSError:
                pass
        t.join(timeout=2.0)


def test_capture_session_serves_html_and_accepts_post(
    capture_thread: dict[str, Any],
) -> None:
    port = capture_thread["port"]

    # GET /auth returns the bootstrap HTML.
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
    conn.request("GET", "/auth")
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    conn.close()
    assert resp.status == 200
    assert "Teams CLI" in body
    assert "fetch('/submit'" in body

    # POST /submit with a JSON dict unblocks serve_forever().
    payload = {"k1": "v1", "k2": "v2"}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
    conn.request("POST", "/submit", body=json.dumps(payload).encode("utf-8"))
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 200

    # capture_session returns the parsed dict (string-coerced).
    deadline = time.time() + 5.0
    while time.time() < deadline and capture_thread["result"] is None:
        time.sleep(0.05)
    assert capture_thread["error"] is None, capture_thread["error"]
    assert capture_thread["result"] == payload


def test_capture_session_rejects_invalid_json() -> None:
    """Non-JSON body should make capture_session raise UserError after the handler 400s."""
    state: dict[str, Any] = {"error": None}
    start_port = _pick_free_port()

    def runner() -> None:
        try:
            capture_session(port=start_port)
        except BaseException as exc:  # noqa: BLE001
            state["error"] = exc

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    # Wait for the server.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", start_port, timeout=0.5)
            conn.request("GET", "/auth")
            conn.getresponse().read()
            conn.close()
            break
        except OSError:
            time.sleep(0.05)

    # Garbage body — not JSON.
    conn = http.client.HTTPConnection("127.0.0.1", start_port, timeout=2.0)
    conn.request("POST", "/submit", body=b"this is not json")
    conn.getresponse().read()
    conn.close()

    t.join(timeout=5.0)
    assert isinstance(state["error"], UserError)
    assert "Failed to parse" in str(state["error"])


def test_capture_session_404_for_unknown_paths() -> None:
    """GET to anything other than /auth and POST to anything other than /submit return 404."""
    # Spin up the server manually so we can hit it without the long banner.
    port = _pick_free_port()
    server = _AuthServer(("127.0.0.1", port), BookmarkletAuthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
        conn.request("GET", "/nope")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 404

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
        conn.request("POST", "/elsewhere", body=b"")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 404
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2.0)
