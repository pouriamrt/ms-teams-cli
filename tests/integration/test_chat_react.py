"""Integration tests for `teams chat react`."""

from __future__ import annotations

import json
from pathlib import Path

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
    (cache / "last_message_listing.json").write_text(
        json.dumps(
            {
                "captured_at": "now",
                "chat_id": "19:abc@unq.gbl.spaces",
                "entries": {"1": "msg-1"},
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


@respx.mock
def test_react_like_graph_path(logged_in_with_message_listing: Path) -> None:
    _mock_token()
    chat_id = "19:abc@unq.gbl.spaces"
    respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages/msg-1/setReaction").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["--json", "chat", "react", "1", "like"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    parsed = json.loads(result.stdout)
    assert parsed == {
        "ok": True,
        "reaction": "like",
        "via": "graph",
        "unreact": False,
    }


@respx.mock
def test_react_unreact(logged_in_with_message_listing: Path) -> None:
    _mock_token()
    chat_id = "19:abc@unq.gbl.spaces"
    respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages/msg-1/unsetReaction").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["--json", "chat", "react", "1", "like", "--unreact"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    parsed = json.loads(result.stdout)
    assert parsed["unreact"] is True


def test_unsupported_emoji_exits_2(logged_in_with_message_listing: Path) -> None:
    result = runner.invoke(app, ["chat", "react", "1", "rocket"])
    assert result.exit_code == 2
    combined = result.stderr + result.stdout
    assert "Supported reactions" in combined or "rocket" in combined
