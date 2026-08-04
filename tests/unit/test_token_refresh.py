from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from freezegun import freeze_time

from teams_cli.auth.token_refresh import (
    GRAPH_SCOPE,
    TEAMS_SCOPE,
    TokenRefresher,
)
from teams_cli.auth.token_store import Credentials, save


def _make_creds(tmp_path: Path) -> Path:
    creds = Credentials(
        version=1,
        acquired_at="2026-05-22T08:00:00Z",
        tenant_id="tid-123",
        client_id="cid-abc",
        home_account_id="oid-1.tid-123",
        username="u@example.com",
        refresh_token="rt-original",
        shared_from=None,
        id_token_claims={"oid": "oid-1"},
    )
    path = tmp_path / "credentials.json"
    save(creds, path)
    return path


@respx.mock
@freeze_time("2026-05-22T18:00:00Z")
def test_first_call_mints_and_caches(
    tmp_path: Path, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    creds_path = _make_creds(tmp_path)
    cache_path = tmp_path / "access_tokens.json"
    body = load_fixture("login_response.json")
    route = respx.post("https://login.microsoftonline.com/tid-123/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json=body)
    )

    refresher = TokenRefresher(creds_path=creds_path, cache_path=cache_path)
    at = refresher.get_token(GRAPH_SCOPE)

    assert at == body["access_token"]
    assert route.called
    # Cache should contain the AT keyed by scope.
    cache = json.loads(cache_path.read_text("utf-8"))
    assert GRAPH_SCOPE in cache
    assert cache[GRAPH_SCOPE]["access_token"] == body["access_token"]


@respx.mock
@freeze_time("2026-05-22T18:00:00Z")
def test_second_call_uses_cache(
    tmp_path: Path, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    creds_path = _make_creds(tmp_path)
    cache_path = tmp_path / "access_tokens.json"
    body = load_fixture("login_response.json")
    route = respx.post("https://login.microsoftonline.com/tid-123/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json=body)
    )

    refresher = TokenRefresher(creds_path=creds_path, cache_path=cache_path)
    refresher.get_token(GRAPH_SCOPE)
    refresher.get_token(GRAPH_SCOPE)
    assert route.call_count == 1


@respx.mock
def test_expired_token_remints(
    tmp_path: Path, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    creds_path = _make_creds(tmp_path)
    cache_path = tmp_path / "access_tokens.json"
    body = load_fixture("login_response.json")
    route = respx.post("https://login.microsoftonline.com/tid-123/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json=body)
    )

    with freeze_time("2026-05-22T18:00:00Z") as frozen:
        refresher = TokenRefresher(creds_path=creds_path, cache_path=cache_path)
        refresher.get_token(GRAPH_SCOPE)
        # Jump past the cache window: expires_in=3600s minus _SKEW_SECONDS=60s -> 59 min window.
        frozen.tick(timedelta(minutes=60))
        refresher.get_token(GRAPH_SCOPE)
    assert route.call_count == 2


@respx.mock
def test_rotated_refresh_token_is_persisted(
    tmp_path: Path, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    creds_path = _make_creds(tmp_path)
    cache_path = tmp_path / "access_tokens.json"
    body = load_fixture("login_response.json")  # contains rotated RT
    respx.post("https://login.microsoftonline.com/tid-123/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json=body)
    )

    refresher = TokenRefresher(creds_path=creds_path, cache_path=cache_path)
    refresher.get_token(GRAPH_SCOPE)
    after = json.loads(creds_path.read_text("utf-8"))
    assert after["refresh_token"] == body["refresh_token"]


@respx.mock
def test_missing_rotated_rt_does_not_clobber(tmp_path: Path) -> None:
    creds_path = _make_creds(tmp_path)
    cache_path = tmp_path / "access_tokens.json"
    no_rotation = {
        "token_type": "Bearer",
        "scope": GRAPH_SCOPE,
        "expires_in": 3600,
        "access_token": "AT-XYZ",
    }
    respx.post("https://login.microsoftonline.com/tid-123/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json=no_rotation)
    )

    refresher = TokenRefresher(creds_path=creds_path, cache_path=cache_path)
    refresher.get_token(GRAPH_SCOPE)
    after = json.loads(creds_path.read_text("utf-8"))
    assert after["refresh_token"] == "rt-original"


@respx.mock
def test_invalid_grant_raises_session_expired(tmp_path: Path) -> None:
    from teams_cli.errors import SessionExpired

    creds_path = _make_creds(tmp_path)
    cache_path = tmp_path / "access_tokens.json"
    respx.post("https://login.microsoftonline.com/tid-123/oauth2/v2.0/token").mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "AADSTS70008: expired"}
        )
    )

    refresher = TokenRefresher(creds_path=creds_path, cache_path=cache_path)
    with pytest.raises(SessionExpired):
        refresher.get_token(GRAPH_SCOPE)


@respx.mock
def test_interaction_required_raises_session_expired(tmp_path: Path) -> None:
    from teams_cli.errors import SessionExpired

    creds_path = _make_creds(tmp_path)
    cache_path = tmp_path / "access_tokens.json"
    respx.post("https://login.microsoftonline.com/tid-123/oauth2/v2.0/token").mock(
        return_value=httpx.Response(400, json={"error": "interaction_required"})
    )

    refresher = TokenRefresher(creds_path=creds_path, cache_path=cache_path)
    with pytest.raises(SessionExpired):
        refresher.get_token(GRAPH_SCOPE)


def test_scope_constants_match_spec() -> None:
    assert GRAPH_SCOPE == "https://graph.microsoft.com/.default"
    assert TEAMS_SCOPE == "https://teams.microsoft.com/.default"


@respx.mock
def test_post_includes_origin_header(
    tmp_path: Path, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    creds_path = _make_creds(tmp_path)
    cache_path = tmp_path / "access_tokens.json"
    body = load_fixture("login_response.json")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["origin"] = request.headers.get("Origin")
        return httpx.Response(200, json=body)

    respx.post("https://login.microsoftonline.com/tid-123/oauth2/v2.0/token").mock(
        side_effect=handler
    )

    refresher = TokenRefresher(creds_path=creds_path, cache_path=cache_path)
    refresher.get_token(GRAPH_SCOPE)
    assert captured["origin"] == "https://teams.microsoft.com"
