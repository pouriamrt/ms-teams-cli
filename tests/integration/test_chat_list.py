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

# Matches the "Me" sender in chatsvc_conversations.json so "(self)" chats
# register as from-me and the unread heuristic behaves predictably.
ME_OID = "00000000-0000-0000-0000-00000000000a"


@pytest.fixture
def logged_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
                "id_token_claims": {"oid": ME_OID, "name": "Me"},
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _mock_auth(skype_response: dict[str, Any]) -> None:
    """Mock the AAD token endpoint + the Skype JWT mint endpoint.

    Both `chat list` and `chat read` go through the chatsvc backend, which
    requires the Skype JWT — itself minted from an AAD Teams-scope token.
    """
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
def test_chat_list_json(logged_in: Path, load_fixture: Callable[[str], dict[str, Any]]) -> None:
    _mock_auth(load_fixture("aadtokenauth_response.json"))
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(200, json=load_fixture("chatsvc_conversations.json"))
    )

    result = runner.invoke(app, ["--json", "chat", "list", "--top", "10"])

    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    parsed = json.loads(result.stdout)
    # chatsvc fixture has Chat, OneToOneChat, Meeting, TeamsTeam (filtered),
    # StreamOfNotifications (filtered), and a self-only Chat. The kept types
    # yield 4 chats; all have a usable lastMessage.
    assert len(parsed["items"]) == 4
    assert parsed["items"][0]["index"] == 1
    chat_types = {item["chat_type"] for item in parsed["items"]}
    assert chat_types == {"oneOnOne", "group", "meeting"}


@respx.mock
def test_chat_list_unread(logged_in: Path, load_fixture: Callable[[str], dict[str, Any]]) -> None:
    _mock_auth(load_fixture("aadtokenauth_response.json"))
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(200, json=load_fixture("chatsvc_conversations.json"))
    )

    result = runner.invoke(app, ["--json", "chat", "list", "--unread"])

    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    parsed = json.loads(result.stdout)
    # Of the 4 kept chats: only "Bob 1to1" was already read (consumptionhorizon
    # ahead of its lastMessage). The other three — Alice Group, Carol Meeting,
    # and the (self) chat — all have a cursor behind the last message and so
    # count as unread. The (self) chat counts despite being from-me: the
    # from-me suppression was removed so explicit mark-unread is honored.
    assert len(parsed["items"]) == 3
    for item in parsed["items"]:
        assert item["has_unread"] is True


@respx.mock
def test_chat_list_session_expired(logged_in: Path) -> None:
    respx.post("https://login.microsoftonline.com/tid/oauth2/v2.0/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    result = runner.invoke(app, ["chat", "list"])
    assert result.exit_code == 77
    assert "Session expired" in result.stderr or "Session expired" in result.stdout
