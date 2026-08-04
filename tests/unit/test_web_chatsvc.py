"""Unit tests for the chatsvc reaction fallback + redaction audit of test data."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from freezegun import freeze_time

from teams_cli.api.client import CHATSVC_AMER_BASE, ApiClient
from teams_cli.api.web_chatsvc import (
    mark_chat_consumption_via_chatsvc,
    set_reaction_via_chatsvc,
)
from teams_cli.errors import ApiError


@pytest.fixture
def client() -> ApiClient:
    return ApiClient(
        get_graph_token=lambda: "AT",
        get_teams_token=lambda: "TT",
        get_skype_token=lambda: "SKYPE-JWT-VALUE-HERE",
        get_ic3_token=lambda: "IC3-AT",
    )


@respx.mock
@freeze_time("2026-05-22T18:00:00Z")
def test_set_reaction_sends_expected_payload(client: ApiClient) -> None:
    chat_id = "19:abc@unq.gbl.spaces"
    msg_id = "m1"
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content.decode())
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"OK": True})

    respx.post(
        f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/messages/{msg_id}/properties"
    ).mock(side_effect=handler)

    set_reaction_via_chatsvc(
        client,
        chat_id=chat_id,
        message_id=msg_id,
        user_aad_id="me-aad-oid",
        reaction_type="like",
    )

    # Header: Authentication: skypetoken=..., NOT Authorization: Bearer ...
    assert captured["headers"].get("authentication", "").startswith("skypetoken=")
    assert "authorization" not in captured["headers"]
    assert "?name=emotions" in captured["url"]

    body = captured["json"]
    assert body["emotions"][0]["key"] == "like"
    user_entry = body["emotions"][0]["users"][0]
    assert user_entry["mri"] == "8:orgid:me-aad-oid"

    # Compute the expected epoch_ms from the frozen instant so the assertion
    # tracks the freeze_time decorator if it ever changes.
    expected_epoch_ms = int(datetime(2026, 5, 22, 18, 0, 0, tzinfo=UTC).timestamp() * 1000)
    assert user_entry["time"] == expected_epoch_ms


@respx.mock
def test_unreact_sends_empty_users_array(client: ApiClient) -> None:
    chat_id = "19:abc@unq.gbl.spaces"
    msg_id = "m1"
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"OK": True})

    respx.post(
        f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/messages/{msg_id}/properties"
    ).mock(side_effect=handler)

    set_reaction_via_chatsvc(
        client,
        chat_id=chat_id,
        message_id=msg_id,
        user_aad_id="me-aad-oid",
        reaction_type="like",
        unreact=True,
    )
    assert captured["json"]["emotions"][0]["users"] == []


@respx.mock
def test_500_raises_apierror(client: ApiClient) -> None:
    chat_id = "19:abc@unq.gbl.spaces"
    msg_id = "m1"
    respx.post(
        f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/messages/{msg_id}/properties"
    ).mock(return_value=httpx.Response(500, text="server error"))

    with pytest.raises(ApiError) as ei:
        set_reaction_via_chatsvc(
            client,
            chat_id=chat_id,
            message_id=msg_id,
            user_aad_id="me-aad-oid",
            reaction_type="like",
        )
    assert ei.value.status_code == 500


_MY_OID = "me-oid"


def _mock_readback(chat_id: str, cursor_ms: int) -> None:
    """Mock the GET /threads/{id}/consumptionhorizons read-back to return a
    cursor for _MY_OID matching ``cursor_ms`` (so verification passes)."""
    respx.get(f"{CHATSVC_AMER_BASE}/threads/{chat_id}/consumptionhorizons").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": chat_id,
                "consumptionhorizons": [
                    {
                        "id": f"8:orgid:{_MY_OID}",
                        "consumptionhorizon": f"{cursor_ms};{cursor_ms};0",
                    }
                ],
            },
        )
    )


@respx.mock
@freeze_time("2026-05-22T18:00:00Z")
def test_mark_read_clears_bookmark_with_cursor_zero(
    client: ApiClient,
) -> None:
    """mark-read writes cursor 0 (clears the unread marker) with IC3 auth.

    The bookmark is an UNREAD marker: nonzero=unread, 0=read. Writing 0 is the
    READ direction. The read direction trusts a 200 (no read-back needed).
    """
    chat_id = "19:abc@unq.gbl.spaces"
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content.decode())
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"OK": True})

    respx.put(f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/properties").mock(
        side_effect=handler
    )
    _mock_readback(chat_id, 0)  # read direction confirms the marker is cleared

    verified = mark_chat_consumption_via_chatsvc(
        client, chat_id=chat_id, my_aad_id=_MY_OID, is_read=True, since=None
    )
    assert verified is True

    # Auth MUST be Bearer with the IC3-scoped token, NOT skypetoken.
    assert captured["headers"].get("authorization") == "Bearer IC3-AT"
    assert "authentication" not in captured["headers"]
    assert captured["headers"].get("behavioroverride") == "redirectAs404"
    assert "clientName=skypeteams" in captured["headers"].get("clientinfo", "")
    assert captured["headers"].get("x-ms-migration") == "True"
    assert "name=consumptionHorizonBookmark" in captured["url"]
    # mark-read -> cursor 0 (clears marker).
    parts = captured["json"]["consumptionHorizonBookmark"].split(";")
    assert parts[0] == "0"
    expected_now_ms = int(datetime(2026, 5, 22, 18, 0, 0, tzinfo=UTC).timestamp() * 1000)
    assert int(parts[1]) == expected_now_ms
    assert parts[2] == "0"


@respx.mock
@freeze_time("2026-05-22T18:00:00Z")
def test_mark_read_verified_when_cursor_advanced_to_now(
    client: ApiClient,
) -> None:
    """Some servers represent 'read' by advancing the cursor to ~now rather
    than zeroing it; that must still verify as read."""
    chat_id = "19:abc@unq.gbl.spaces"
    respx.put(f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/properties").mock(
        return_value=httpx.Response(200, json={"OK": True})
    )
    now_ms = int(datetime(2026, 5, 22, 18, 0, 0, tzinfo=UTC).timestamp() * 1000)
    _mock_readback(chat_id, now_ms)  # cleared = advanced to now, not 0

    verified = mark_chat_consumption_via_chatsvc(
        client, chat_id=chat_id, my_aad_id=_MY_OID, is_read=True, since=None
    )
    assert verified is True


@respx.mock
def test_mark_read_unverified_when_marker_still_set(client: ApiClient) -> None:
    """mark-read 200 but read-back still shows a stale nonzero unread marker
    (neither 0 nor recent) -> not verified."""
    chat_id = "19:abc@unq.gbl.spaces"
    respx.put(f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/properties").mock(
        return_value=httpx.Response(200, json={"OK": True})
    )
    # An old (2021) nonzero marker that is neither 0 nor within the recency
    # window of "now" -> the clear did not take.
    _mock_readback(chat_id, 1609459200000)

    verified = mark_chat_consumption_via_chatsvc(
        client, chat_id=chat_id, my_aad_id=_MY_OID, is_read=True, since=None
    )
    assert verified is False


@respx.mock
@freeze_time("2026-05-22T18:00:00Z")
def test_mark_unread_whole_uses_nonzero_cursor(client: ApiClient) -> None:
    """mark-unread without --since writes a NONZERO cursor (now) to set the
    unread marker, and confirms it via read-back."""
    chat_id = "19:abc@unq.gbl.spaces"
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"OK": True})

    respx.put(f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/properties").mock(
        side_effect=handler
    )
    now_ms = int(datetime(2026, 5, 22, 18, 0, 0, tzinfo=UTC).timestamp() * 1000)
    _mock_readback(chat_id, now_ms)

    verified = mark_chat_consumption_via_chatsvc(
        client, chat_id=chat_id, my_aad_id=_MY_OID, is_read=False, since=None
    )
    assert verified is True

    parts = captured["json"]["consumptionHorizonBookmark"].split(";")
    assert int(parts[0]) == now_ms  # nonzero marker = unread
    assert int(parts[1]) == now_ms


@respx.mock
@freeze_time("2026-05-22T18:00:00Z")
def test_mark_unread_with_cutoff_sets_cursor(client: ApiClient) -> None:
    chat_id = "19:abc@unq.gbl.spaces"
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"OK": True})

    respx.put(f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/properties").mock(
        side_effect=handler
    )
    cutoff = datetime(2026, 5, 20, 14, 0, 0, tzinfo=UTC)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    _mock_readback(chat_id, cutoff_ms)

    verified = mark_chat_consumption_via_chatsvc(
        client, chat_id=chat_id, my_aad_id=_MY_OID, is_read=False, since=cutoff
    )
    assert verified is True

    parts = captured["json"]["consumptionHorizonBookmark"].split(";")
    assert int(parts[0]) == cutoff_ms  # marker anchored at --since
    assert int(parts[1]) == int(
        datetime(2026, 5, 22, 18, 0, 0, tzinfo=UTC).timestamp() * 1000
    )  # action = now


@respx.mock
def test_mark_unread_unverified_when_cursor_unchanged(
    client: ApiClient,
) -> None:
    """mark-unread 200 but read-back shows a different cursor -> not verified."""
    chat_id = "19:abc@unq.gbl.spaces"
    respx.put(f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/properties").mock(
        return_value=httpx.Response(200, json={"OK": True})
    )
    # Read-back reports an unrelated cursor — the silent no-op case.
    _mock_readback(chat_id, 1)

    verified = mark_chat_consumption_via_chatsvc(
        client, chat_id=chat_id, my_aad_id=_MY_OID, is_read=False, since=None
    )
    assert verified is False


@respx.mock
def test_mark_consumption_500_raises(client: ApiClient) -> None:
    chat_id = "19:abc@unq.gbl.spaces"
    respx.put(f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/properties").mock(
        return_value=httpx.Response(500, text="boom")
    )

    with pytest.raises(ApiError) as ei:
        mark_chat_consumption_via_chatsvc(
            client, chat_id=chat_id, my_aad_id=_MY_OID, is_read=False, since=None
        )
    assert ei.value.status_code == 500


def test_no_skypetoken_leaks_in_cassette_dir() -> None:
    """Audit: scan every committed cassette and fixture for raw token-shaped strings.

    The regex deliberately requires a long opaque suffix so it does NOT match the
    REDACTED placeholders in fixtures (e.g. `Bearer <REDACTED>` or
    `skypetoken=<REDACTED>`).
    """
    root = Path(__file__).resolve().parents[1]  # tests/
    bad = re.compile(r"skypetoken=[A-Za-z0-9._\-+/=]{16,}|Bearer\s+[A-Za-z0-9._\-+/=]{30,}")
    offenders: list[str] = []
    for sub in ["integration/cassettes", "fixtures"]:
        d = root / sub
        if not d.exists():
            continue
        for path in d.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if bad.search(text):
                offenders.append(str(path))
    assert offenders == [], f"Token-shaped strings found in committed test data: {offenders}"
