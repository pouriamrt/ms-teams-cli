"""Integration tests for `teams chat mark-read` / `mark-unread`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from freezegun import freeze_time
from typer.testing import CliRunner

from teams_cli.api.client import CHATSVC_AMER_BASE, GRAPH_BASE
from teams_cli.cli import app
from teams_cli.errors import ApiError

runner = CliRunner()

CHAT_ID = "19:abc@unq.gbl.spaces"
OTHER_CHAT_ID = "19:zzz@unq.gbl.spaces"


@pytest.fixture
def logged_in_with_chat_listing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Mirrors test_chat_react fixture but seeds the chat-listing cache."""
    monkeypatch.setenv("TEAMS_CLI_HOME", str(tmp_path))
    cdir = tmp_path / ".config" / "teams-cli"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "credentials.json").write_text(
        json.dumps(
            {
                "version": 1,
                "acquired_at": "now",
                "tenant_id": "tid",
                "client_id": "cid",
                "home_account_id": "me-id.tid",
                "username": "me@example.com",
                "refresh_token": "rt",
                "shared_from": None,
                "id_token_claims": {"oid": "me-id"},
            }
        ),
        encoding="utf-8",
    )
    cache = tmp_path / ".cache" / "teams-cli"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "last_chat_listing.json").write_text(
        json.dumps(
            {
                "captured_at": "now",
                "entries": {"3": CHAT_ID, "4": OTHER_CHAT_ID},
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _mock_token() -> None:
    respx.post("https://login.microsoftonline.com/tid/oauth2/v2.0/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "AT",
                "expires_in": 3600,
                "scope": "https://graph.microsoft.com/.default",
                "token_type": "Bearer",
            },
        )
    )


def _mock_skype_token() -> None:
    """The chatsvc fallback needs a Skype JWT, minted at authsvc."""
    respx.post("https://teams.microsoft.com/api/authsvc/v1.0/authz").mock(
        return_value=httpx.Response(
            200,
            json={
                "tokens": {"skypeToken": "SKYPE-JWT", "expiresIn": 86400},
                "region": "amer",
            },
        )
    )


def _mock_readback(chat_id: str, cursor_ms: int) -> None:
    """Mock the consumption-horizon read-back so verification passes: returns
    a cursor for the signed-in user (oid 'me-id') matching ``cursor_ms``."""
    respx.get(f"{CHATSVC_AMER_BASE}/threads/{chat_id}/consumptionhorizons").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": chat_id,
                "consumptionhorizons": [
                    {
                        "id": "8:orgid:me-id",
                        "consumptionhorizon": f"{cursor_ms};{cursor_ms};0",
                    }
                ],
            },
        )
    )


def _seed_people_cache(home: Path, *, email: str, user_id: str) -> None:
    """Pre-populate people.json so PeopleResolver bypasses Graph /users."""
    cache = home / ".cache" / "teams-cli"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "people.json").write_text(
        json.dumps(
            {
                "entries": {
                    email.lower(): {
                        "user_id": user_id,
                        "name": "Alice",
                        "email": email,
                        "expires_at": "9999-12-31T00:00:00+00:00",
                    }
                }
            }
        ),
        encoding="utf-8",
    )


# ---------------------- mark-read ----------------------


@respx.mock
def test_mark_read_by_index(logged_in_with_chat_listing: Path) -> None:
    _mock_token()
    route = respx.post(f"{GRAPH_BASE}/chats/{CHAT_ID}/markChatReadForUser").mock(
        return_value=httpx.Response(204)
    )

    result = runner.invoke(app, ["--json", "chat", "mark-read", "3"])

    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert route.called
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body == {"user": {"id": "me-id", "tenantId": "tid"}}
    parsed = json.loads(result.stdout)
    assert parsed == {
        "ok": True,
        "chat_id": CHAT_ID,
        "state": "read",
        "via": "graph",
        "verified": True,
        "error": None,
    }


@respx.mock
def test_mark_read_by_chat_id_literal(logged_in_with_chat_listing: Path) -> None:
    _mock_token()
    route = respx.post(f"{GRAPH_BASE}/chats/{CHAT_ID}/markChatReadForUser").mock(
        return_value=httpx.Response(204)
    )

    result = runner.invoke(app, ["chat", "mark-read", CHAT_ID])

    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert route.called
    assert "read" in result.stdout.lower()


@respx.mock
def test_mark_read_by_email(logged_in_with_chat_listing: Path) -> None:
    _mock_token()
    _seed_people_cache(logged_in_with_chat_listing, email="alice@example.com", user_id="alice-aad")
    # ensure_one_on_one POSTs to /chats and returns the new (or existing) id.
    respx.post(f"{GRAPH_BASE}/chats").mock(return_value=httpx.Response(201, json={"id": CHAT_ID}))
    route = respx.post(f"{GRAPH_BASE}/chats/{CHAT_ID}/markChatReadForUser").mock(
        return_value=httpx.Response(204)
    )

    result = runner.invoke(app, ["chat", "mark-read", "alice@example.com"])

    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert route.called


# ---------------------- mark-unread ----------------------


@respx.mock
def test_mark_unread_by_index_no_since(
    logged_in_with_chat_listing: Path,
) -> None:
    _mock_token()
    route = respx.post(f"{GRAPH_BASE}/chats/{CHAT_ID}/markChatUnreadForUser").mock(
        return_value=httpx.Response(204)
    )

    result = runner.invoke(app, ["--json", "chat", "mark-unread", "3"])

    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert route.called
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body == {"user": {"id": "me-id", "tenantId": "tid"}}
    parsed = json.loads(result.stdout)
    assert parsed == {
        "ok": True,
        "chat_id": CHAT_ID,
        "state": "unread",
        "since": None,
        "via": "graph",
        "verified": True,
        "error": None,
    }


@respx.mock
def test_mark_unread_with_since(logged_in_with_chat_listing: Path) -> None:
    _mock_token()
    route = respx.post(f"{GRAPH_BASE}/chats/{CHAT_ID}/markChatUnreadForUser").mock(
        return_value=httpx.Response(204)
    )

    result = runner.invoke(
        app,
        [
            "--json",
            "chat",
            "mark-unread",
            "3",
            "--since",
            "2026-05-20T14:00:00Z",
        ],
    )

    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert route.called
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["user"] == {"id": "me-id", "tenantId": "tid"}
    # Body should include the parsed timestamp in ISO-8601 with Z suffix.
    sent_ts = sent_body["lastMessageReadDateTime"]
    parsed_sent = datetime.fromisoformat(sent_ts.replace("Z", "+00:00"))
    assert parsed_sent == datetime(2026, 5, 20, 14, 0, 0, tzinfo=UTC)
    parsed = json.loads(result.stdout)
    assert parsed["since"] is not None
    assert parsed["state"] == "unread"


# ---------------------- error paths ----------------------


@respx.mock
@freeze_time("2026-05-22T18:00:00Z")
def test_mark_read_graph_403_falls_back_to_chatsvc(
    logged_in_with_chat_listing: Path,
) -> None:
    """Restricted-tenant scenario: Graph 403 -> chatsvc consumption-horizon write."""
    _mock_token()
    _mock_skype_token()
    respx.post(f"{GRAPH_BASE}/chats/{CHAT_ID}/markChatReadForUser").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    chatsvc_route = respx.put(
        f"{CHATSVC_AMER_BASE}/users/ME/conversations/{CHAT_ID}/properties"
    ).mock(return_value=httpx.Response(200, json={"OK": True}))
    # mark-read clears the unread marker (cursor 0); read-back must report the
    # marker cleared (cursor 0) for verification to pass.
    _mock_readback(CHAT_ID, 0)

    result = runner.invoke(app, ["--json", "chat", "mark-read", "3"])

    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert chatsvc_route.called
    assert "name=consumptionHorizonBookmark" in str(chatsvc_route.calls[0].request.url)
    sent_body = json.loads(chatsvc_route.calls[0].request.content)
    # mark-read -> cursor 0 (clears the unread marker), action=now, msgid=0.
    parts = sent_body["consumptionHorizonBookmark"].split(";")
    assert len(parts) == 3
    assert parts[0] == "0"
    assert int(parts[1]) > 0
    assert parts[2] == "0"
    parsed = json.loads(result.stdout)
    assert parsed["via"] == "chatsvc"
    assert parsed["state"] == "read"
    assert parsed["verified"] is True


@respx.mock
def test_mark_unread_graph_403_falls_back_with_since(
    logged_in_with_chat_listing: Path,
) -> None:
    _mock_token()
    _mock_skype_token()
    respx.post(f"{GRAPH_BASE}/chats/{CHAT_ID}/markChatUnreadForUser").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    chatsvc_route = respx.put(
        f"{CHATSVC_AMER_BASE}/users/ME/conversations/{CHAT_ID}/properties"
    ).mock(return_value=httpx.Response(200, json={"OK": True}))
    since_ms = int(datetime(2026, 5, 20, 14, 0, 0, tzinfo=UTC).timestamp() * 1000)
    _mock_readback(CHAT_ID, since_ms)

    result = runner.invoke(
        app,
        [
            "--json",
            "chat",
            "mark-unread",
            "3",
            "--since",
            "2026-05-20T14:00:00Z",
        ],
    )

    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert chatsvc_route.called
    sent_body = json.loads(chatsvc_route.calls[0].request.content)
    parts = sent_body["consumptionHorizonBookmark"].split(";")
    expected_ms = int(datetime(2026, 5, 20, 14, 0, 0, tzinfo=UTC).timestamp() * 1000)
    # Position 0 = cursor anchor = --since value.
    assert int(parts[0]) == expected_ms
    # Position 1 = action timestamp (now).
    assert int(parts[1]) > 0
    # Position 2 = message id placeholder.
    assert parts[2] == "0"
    parsed = json.loads(result.stdout)
    assert parsed["via"] == "chatsvc"
    assert parsed["since"] is not None


@respx.mock
@freeze_time("2026-05-22T18:00:00Z")
def test_mark_unread_no_since_uses_nonzero_cursor(
    logged_in_with_chat_listing: Path,
) -> None:
    """Without --since, whole-chat-unread writes a NONZERO marker (now).

    cursor=0 would CLEAR the marker (mark read) — the inverse of intent. The
    marker must be nonzero to render the chat unread.
    """
    _mock_token()
    _mock_skype_token()
    respx.post(f"{GRAPH_BASE}/chats/{CHAT_ID}/markChatUnreadForUser").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    chatsvc_route = respx.put(
        f"{CHATSVC_AMER_BASE}/users/ME/conversations/{CHAT_ID}/properties"
    ).mock(return_value=httpx.Response(200, json={"OK": True}))
    now_ms = int(datetime(2026, 5, 22, 18, 0, 0, tzinfo=UTC).timestamp() * 1000)
    _mock_readback(CHAT_ID, now_ms)

    result = runner.invoke(app, ["--json", "chat", "mark-unread", "3"])

    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    sent_body = json.loads(chatsvc_route.calls[0].request.content)
    parts = sent_body["consumptionHorizonBookmark"].split(";")
    assert int(parts[0]) == now_ms  # nonzero marker = unread
    assert int(parts[1]) == now_ms
    assert parts[2] == "0"
    parsed = json.loads(result.stdout)
    assert parsed["via"] == "chatsvc"
    assert parsed["verified"] is True


@respx.mock
def test_mark_unread_unverified_exits_nonzero(
    logged_in_with_chat_listing: Path,
) -> None:
    """chatsvc 200 but read-back shows cursor unchanged -> honest failure.

    The command must NOT falsely claim success: exit non-zero and emit
    verified=false so scripts can detect the no-op.
    """
    _mock_token()
    _mock_skype_token()
    respx.post(f"{GRAPH_BASE}/chats/{CHAT_ID}/markChatUnreadForUser").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    respx.put(f"{CHATSVC_AMER_BASE}/users/ME/conversations/{CHAT_ID}/properties").mock(
        return_value=httpx.Response(200, json={"OK": True})
    )
    # We asked for cursor 0 (mark-unread) but read-back still reports a recent
    # cursor: the silent no-op the verification exists to catch.
    _mock_readback(CHAT_ID, 1779999999999)

    result = runner.invoke(app, ["--json", "chat", "mark-unread", "3"])

    assert result.exit_code != 0
    parsed = json.loads(result.stdout)
    assert parsed["verified"] is False
    assert parsed["ok"] is False


@respx.mock
def test_mark_read_both_paths_fail_raises(
    logged_in_with_chat_listing: Path,
) -> None:
    """Graph 403 AND chatsvc 500 -> ApiError surfacing both status codes."""
    _mock_token()
    _mock_skype_token()
    respx.post(f"{GRAPH_BASE}/chats/{CHAT_ID}/markChatReadForUser").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    respx.put(f"{CHATSVC_AMER_BASE}/users/ME/conversations/{CHAT_ID}/properties").mock(
        return_value=httpx.Response(500, text="boom")
    )

    result = runner.invoke(app, ["chat", "mark-read", "3"])

    assert result.exit_code != 0
    assert isinstance(result.exception, ApiError)
    msg = str(result.exception)
    assert "graph=403" in msg
    assert "chatsvc=500" in msg


@respx.mock
def test_mark_unread_404_surfaces_api_error(
    logged_in_with_chat_listing: Path,
) -> None:
    _mock_token()
    respx.post(f"{GRAPH_BASE}/chats/{CHAT_ID}/markChatUnreadForUser").mock(
        return_value=httpx.Response(404, text="not found")
    )

    result = runner.invoke(app, ["chat", "mark-unread", "3"])

    assert result.exit_code != 0
    assert isinstance(result.exception, ApiError)
    assert result.exception.status_code == 404


def test_mark_read_unknown_index_exits_64(
    logged_in_with_chat_listing: Path,
) -> None:
    result = runner.invoke(app, ["chat", "mark-read", "99"])
    assert result.exit_code == 64
    combined = (result.stderr or "") + (result.stdout or "")
    assert "99" in combined or "index" in combined.lower()


def test_mark_unread_invalid_since_exits_2(
    logged_in_with_chat_listing: Path,
) -> None:
    result = runner.invoke(app, ["chat", "mark-unread", "3", "--since", "not-a-real-date"])
    assert result.exit_code == 2
    combined = (result.stderr or "") + (result.stdout or "")
    assert "since" in combined.lower() or "parse" in combined.lower()
