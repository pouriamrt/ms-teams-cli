from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from teams_cli.api.client import CHATSVC_AMER_BASE
from teams_cli.cli import app

runner = CliRunner()

ME_OID = "00000000-0000-0000-0000-00000000000a"


@pytest.fixture
def logged_in_with_chat_listing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
                "home_account_id": f"{ME_OID}.tid",
                "username": "me@example.com",
                "refresh_token": "rt",
                "shared_from": None,
                "id_token_claims": {"oid": ME_OID},
            }
        ),
        encoding="utf-8",
    )

    cache = tmp_path / ".cache" / "teams-cli"
    cache.mkdir(parents=True, exist_ok=True)
    # chat_id matches the messages fixture (conversationid on each message).
    chat_id = "19:abcdef1234567890@thread.v2"
    (cache / "last_chat_listing.json").write_text(
        json.dumps(
            {
                "captured_at": "2026-05-22T18:00:00Z",
                "entries": {"1": chat_id},
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _mock_auth(skype_response: dict[str, Any]) -> None:
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
    respx.post("https://teams.microsoft.com/api/authsvc/v1.0/authz").mock(
        return_value=httpx.Response(200, json=skype_response)
    )


@respx.mock
def test_chat_read_by_index(
    logged_in_with_chat_listing: Path,
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    _mock_auth(load_fixture("aadtokenauth_response.json"))
    chat_id = "19:abcdef1234567890@thread.v2"
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/messages").mock(
        return_value=httpx.Response(200, json=load_fixture("chatsvc_messages.json"))
    )

    result = runner.invoke(app, ["--json", "chat", "read", "1", "--top", "5"])

    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    parsed = json.loads(result.stdout)
    assert parsed["chat_id"] == chat_id
    # Fixture has 5 messages: 2 user messages + 1 deleted user message
    # (kept) + Event/Call + ThreadActivity/MemberJoined (both filtered).
    assert len(parsed["items"]) == 3
    # Indexed oldest-first (display order in `read`).
    assert parsed["items"][0]["index"] == 1
    # Verify the message-listing cache was written.
    listing_path = (
        logged_in_with_chat_listing / ".cache" / "teams-cli" / "last_message_listing.json"
    )
    listing = json.loads(listing_path.read_text("utf-8"))
    assert listing["chat_id"] == chat_id
    assert "1" in listing["entries"]


@respx.mock
def test_chat_read_unknown_index_exits_64(
    logged_in_with_chat_listing: Path,
) -> None:
    result = runner.invoke(app, ["chat", "read", "99"])
    assert result.exit_code == 64
    assert "No chat with index 99" in (result.stdout + (result.stderr or ""))
