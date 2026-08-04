"""Integration tests for `teams chat send`."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from teams_cli.api.client import GRAPH_BASE
from teams_cli.cli import app

runner = CliRunner()


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
                "home_account_id": "me-id.tid",
                "username": "me@example.com",
                "refresh_token": "rt",
                "shared_from": None,
                "id_token_claims": {"oid": "me-id"},
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _mock_token_endpoint() -> None:
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


@respx.mock
def test_send_by_email_creates_chat_and_posts(
    logged_in: Path,
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    _mock_token_endpoint()
    user_body = load_fixture("graph_user.json")
    respx.get(f"{GRAPH_BASE}/users/alice@example.com").mock(
        return_value=httpx.Response(200, json=user_body)
    )
    chat_id = "19:me-id_ab06770f-89ff-4bb2-99a1-2b0b8d52975e@unq.gbl.spaces"
    respx.post(f"{GRAPH_BASE}/chats").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": chat_id,
                "chatType": "oneOnOne",
                "members": [],
            },
        )
    )
    respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "newmsg",
                "createdDateTime": "2026-05-22T14:10:00Z",
                "chatId": chat_id,
                "from": {
                    "user": {
                        "id": "me-id",
                        "displayName": "Me",
                        "userIdentityType": "aadUser",
                    }
                },
                "body": {"contentType": "text", "content": "lgtm"},
                "reactions": [],
            },
        )
    )

    result = runner.invoke(app, ["--json", "chat", "send", "alice@example.com", "--body", "lgtm"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    parsed = json.loads(result.stdout)
    assert parsed["chat_id"] == chat_id
    assert parsed["message_id"] == "newmsg"


@respx.mock
def test_send_by_index_uses_existing_chat(logged_in: Path) -> None:
    _mock_token_endpoint()
    chat_id = "19:abc@unq.gbl.spaces"
    cache = logged_in / ".cache" / "teams-cli"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "last_chat_listing.json").write_text(
        json.dumps(
            {
                "captured_at": "now",
                "entries": {"1": chat_id},
            }
        ),
        encoding="utf-8",
    )
    respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "m",
                "createdDateTime": "2026-05-22T14:10:00Z",
                "chatId": chat_id,
                "from": {
                    "user": {
                        "id": "me-id",
                        "displayName": "Me",
                        "userIdentityType": "aadUser",
                    }
                },
                "body": {"contentType": "text", "content": "ping"},
                "reactions": [],
            },
        )
    )

    result = runner.invoke(app, ["chat", "send", "1", "--body", "ping"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "Sent" in result.stdout or "Sent" in (result.stderr or "")


@respx.mock
def test_send_by_email_when_user_missing_exits_64(logged_in: Path) -> None:
    _mock_token_endpoint()
    respx.get(f"{GRAPH_BASE}/users/ghost@example.com").mock(
        return_value=httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound"}})
    )
    result = runner.invoke(app, ["chat", "send", "ghost@example.com", "--body", "hi"])
    assert result.exit_code == 64
    assert "not found" in (result.stdout + (result.stderr or "")).lower()
