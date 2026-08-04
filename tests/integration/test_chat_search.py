"""Integration tests for `teams chat search`."""

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


@respx.mock
def test_chat_search_json(logged_in: Path, load_fixture: Callable[[str], dict[str, Any]]) -> None:
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
    respx.post(f"{GRAPH_BASE}/search/query").mock(
        return_value=httpx.Response(200, json=load_fixture("search_chatmessage.json"))
    )
    result = runner.invoke(app, ["--json", "chat", "search", "lgtm", "--top", "10"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    parsed = json.loads(result.stdout)
    assert len(parsed["items"]) == 2
    assert parsed["items"][0]["preview"] == "lgtm, merging now"


@respx.mock
def test_chat_search_scoped_by_chat_index(
    logged_in: Path, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    chat_id = "19:abc@unq.gbl.spaces"
    cache = logged_in / ".cache" / "teams-cli"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "last_chat_listing.json").write_text(
        json.dumps({"captured_at": "now", "entries": {"1": chat_id}}),
        encoding="utf-8",
    )

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
    respx.post(f"{GRAPH_BASE}/search/query").mock(
        return_value=httpx.Response(200, json=load_fixture("search_chatmessage.json"))
    )

    result = runner.invoke(app, ["--json", "chat", "search", "lgtm", "--in", "1"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert all(item["chat_id"] == chat_id for item in parsed["items"])
