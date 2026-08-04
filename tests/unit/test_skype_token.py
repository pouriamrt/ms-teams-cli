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

from teams_cli.auth.skype_token import SkypeTokenMinter

_AUTHZ_URL = "https://teams.microsoft.com/api/authsvc/v1.0/authz"


@respx.mock
@freeze_time("2026-05-22T18:00:00Z")
def test_first_call_mints_and_caches(
    tmp_path: Path, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    cache = tmp_path / "skype_token.json"
    body = load_fixture("aadtokenauth_response.json")
    route = respx.post(_AUTHZ_URL).mock(return_value=httpx.Response(200, json=body))
    minter = SkypeTokenMinter(cache_path=cache)

    tok = minter.get_skype_token(user_aad_id="oid-1", aad_teams_token="AAD-TEAMS-AT")

    assert tok == body["tokens"]["skypeToken"]
    assert route.called
    written = json.loads(cache.read_text("utf-8"))
    assert written["skype_token"] == tok
    assert "expires_at" in written


@respx.mock
@freeze_time("2026-05-22T18:00:00Z")
def test_second_call_uses_cache(
    tmp_path: Path, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    cache = tmp_path / "skype_token.json"
    body = load_fixture("aadtokenauth_response.json")
    route = respx.post(_AUTHZ_URL).mock(return_value=httpx.Response(200, json=body))
    minter = SkypeTokenMinter(cache_path=cache)
    minter.get_skype_token(user_aad_id="oid-1", aad_teams_token="AAD-TEAMS-AT")
    minter.get_skype_token(user_aad_id="oid-1", aad_teams_token="AAD-TEAMS-AT")
    assert route.call_count == 1


@respx.mock
def test_expired_token_remints(
    tmp_path: Path, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    cache = tmp_path / "skype_token.json"
    body = load_fixture("aadtokenauth_response.json")
    route = respx.post(_AUTHZ_URL).mock(return_value=httpx.Response(200, json=body))
    with freeze_time("2026-05-22T18:00:00Z") as frozen:
        minter = SkypeTokenMinter(cache_path=cache)
        minter.get_skype_token(user_aad_id="oid-1", aad_teams_token="AAD-TEAMS-AT")
        # 24h + a minute
        frozen.tick(timedelta(hours=24, minutes=1))
        minter.get_skype_token(user_aad_id="oid-1", aad_teams_token="AAD-TEAMS-AT")
    assert route.call_count == 2


@respx.mock
def test_403_raises_apierror(tmp_path: Path) -> None:
    from teams_cli.errors import ApiError

    cache = tmp_path / "skype_token.json"
    respx.post(_AUTHZ_URL).mock(return_value=httpx.Response(403, text='{"error":"forbidden"}'))
    minter = SkypeTokenMinter(cache_path=cache)
    with pytest.raises(ApiError) as ei:
        minter.get_skype_token(user_aad_id="oid-1", aad_teams_token="AAD-TEAMS-AT")
    assert ei.value.status_code == 403


@respx.mock
def test_request_sends_bearer_and_origin(
    tmp_path: Path, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """The new authsvc endpoint uses Bearer auth + Origin header (no JSON body)."""
    cache = tmp_path / "skype_token.json"
    body = load_fixture("aadtokenauth_response.json")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=body)

    respx.post(_AUTHZ_URL).mock(side_effect=handler)
    minter = SkypeTokenMinter(cache_path=cache)
    minter.get_skype_token(user_aad_id="oid-1", aad_teams_token="AAD-TEAMS-AT")
    assert captured["headers"]["authorization"] == "Bearer AAD-TEAMS-AT"
    assert captured["headers"]["origin"] == "https://teams.microsoft.com"
    # No request body — identity is in the Bearer.
    assert captured["body"] == b""


def test_invalidate_clears_cache(tmp_path: Path) -> None:
    cache = tmp_path / "skype_token.json"
    cache.write_text('{"skype_token":"x","expires_at":"2099-01-01T00:00:00+00:00"}', "utf-8")
    minter = SkypeTokenMinter(cache_path=cache)
    minter.invalidate()
    assert not cache.exists()
