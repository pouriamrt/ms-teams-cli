"""Integration test for `teams chat reply`."""

from __future__ import annotations

import json
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
def logged_in_with_message_listing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
    chat_id = "19:abc@unq.gbl.spaces"
    (cache / "last_message_listing.json").write_text(
        json.dumps(
            {
                "captured_at": "now",
                "chat_id": chat_id,
                "entries": {"1": "src-msg-id"},
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


@respx.mock
def test_reply_quotes_target_message(logged_in_with_message_listing: Path) -> None:
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
    chat_id = "19:abc@unq.gbl.spaces"
    respx.get(f"{GRAPH_BASE}/chats/{chat_id}/messages/src-msg-id").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "src-msg-id",
                "createdDateTime": "2026-05-22T14:00:00Z",
                "chatId": chat_id,
                "from": {
                    "user": {
                        "id": "alice-id",
                        "displayName": "Alice",
                        "userIdentityType": "aadUser",
                    }
                },
                "body": {"contentType": "text", "content": "the original"},
                "reactions": [],
            },
        )
    )
    sent_payload: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent_payload.update(json.loads(request.content.decode()))
        return httpx.Response(
            201,
            json={
                "id": "reply-id",
                "createdDateTime": "2026-05-22T14:10:00Z",
                "chatId": chat_id,
                "from": {
                    "user": {
                        "id": "me-id",
                        "displayName": "Me",
                        "userIdentityType": "aadUser",
                    }
                },
                "body": {"contentType": "html", "content": "ack"},
                "reactions": [],
            },
        )

    respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages").mock(side_effect=handler)

    result = runner.invoke(app, ["--json", "chat", "reply", "1", "--body", "ack"])
    assert result.exit_code == 0, (result.stdout, getattr(result, "stderr", ""))
    parsed = json.loads(result.stdout)
    assert parsed["reply_to_id"] == "src-msg-id"
    assert parsed["chat_id"] == chat_id
    assert sent_payload["body"]["contentType"] == "html"
    assert (
        "blockquote" in sent_payload["body"]["content"].lower()
        or "the original" in sent_payload["body"]["content"]
    )
