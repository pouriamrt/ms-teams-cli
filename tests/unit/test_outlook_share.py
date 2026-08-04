from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from teams_cli.auth.outlook_share import (
    OutlookShareResult,
    detect_outlook_credentials,
    try_share,
)

OUTLOOK_CREDS_SAMPLE = {
    "version": 1,
    "acquired_at": "2026-05-22T08:00:00Z",
    "tenant_id": "tid-outlook",
    "client_id": "outlook-cid",
    "home_account_id": "oid-1.tid-outlook",
    "username": "u@example.com",
    "refresh_token": "outlook-rt",
    "id_token_claims": {"oid": "oid-1", "name": "User"},
}


def test_detect_returns_none_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTLOOK_CLI_HOME", str(tmp_path))
    assert detect_outlook_credentials() is None


def test_detect_returns_path_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTLOOK_CLI_HOME", str(tmp_path))
    target = tmp_path / ".config" / "outlook-cli" / "credentials.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(OUTLOOK_CREDS_SAMPLE), encoding="utf-8")
    detected = detect_outlook_credentials()
    assert detected == target


@respx.mock
def test_try_share_returns_success_on_200(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTLOOK_CLI_HOME", str(tmp_path))
    target = tmp_path / ".config" / "outlook-cli" / "credentials.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(OUTLOOK_CREDS_SAMPLE), encoding="utf-8")

    respx.post("https://login.microsoftonline.com/tid-outlook/oauth2/v2.0/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "FAKE-TEAMS-AT",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "https://teams.microsoft.com/.default",
            },
        )
    )

    result = try_share(target)
    assert isinstance(result, OutlookShareResult)
    assert result.ok is True
    assert result.refresh_token == "outlook-rt"
    assert result.tenant_id == "tid-outlook"
    assert result.client_id == "outlook-cid"
    assert result.username == "u@example.com"


@respx.mock
def test_try_share_returns_failure_on_invalid_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OUTLOOK_CLI_HOME", str(tmp_path))
    target = tmp_path / ".config" / "outlook-cli" / "credentials.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(OUTLOOK_CREDS_SAMPLE), encoding="utf-8")
    respx.post("https://login.microsoftonline.com/tid-outlook/oauth2/v2.0/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    result = try_share(target)
    assert result.ok is False
    assert "invalid_grant" in (result.reason or "")


@respx.mock
def test_try_share_returns_failure_on_interaction_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OUTLOOK_CLI_HOME", str(tmp_path))
    target = tmp_path / ".config" / "outlook-cli" / "credentials.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(OUTLOOK_CREDS_SAMPLE), encoding="utf-8")
    respx.post("https://login.microsoftonline.com/tid-outlook/oauth2/v2.0/token").mock(
        return_value=httpx.Response(400, json={"error": "interaction_required"})
    )

    result = try_share(target)
    assert result.ok is False
    assert "interaction_required" in (result.reason or "")


@respx.mock
def test_try_share_sends_outlook_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTLOOK_CLI_HOME", str(tmp_path))
    target = tmp_path / ".config" / "outlook-cli" / "credentials.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(OUTLOOK_CREDS_SAMPLE), encoding="utf-8")

    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["origin"] = request.headers.get("Origin")
        return httpx.Response(
            200,
            json={
                "access_token": "AT",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "https://teams.microsoft.com/.default",
            },
        )

    respx.post("https://login.microsoftonline.com/tid-outlook/oauth2/v2.0/token").mock(
        side_effect=handler
    )

    result = try_share(target)
    assert result.ok is True
    assert captured["origin"] == "https://outlook.cloud.microsoft"
