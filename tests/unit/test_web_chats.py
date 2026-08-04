"""Unit tests for the chatsvc adapter (api/web_chats.py).

Covers:
- Pure helper functions (_parse_consumption_horizon, _mri_to_oid, _parse_from_url,
  _classify_chat_type).
- The two model adapters (chat_from_chatsvc, message_from_chatsvc).
- WebChats.list_chats / list_messages over mocked chatsvc HTTP responses
  (filters, pagination cap, system-event skipping, chat_id back-fill).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from teams_cli.api.client import CHATSVC_AMER_BASE, GRAPH_BASE, ApiClient
from teams_cli.api.models import ChatType
from teams_cli.api.web_chats import (
    WebChats,
    _classify_chat_type,
    _mri_to_oid,
    _parse_consumption_horizon,
    _parse_from_url,
    chat_from_chatsvc,
    message_from_chatsvc,
)

# my_user_id used across tests — matches the "(self)" entries in the fixture.
ME = "00000000-0000-0000-0000-00000000000a"


# ---------- pure helpers ----------


def test_mri_to_oid_strips_prefix() -> None:
    assert _mri_to_oid("8:orgid:abc-123") == "abc-123"


def test_mri_to_oid_returns_input_when_no_prefix() -> None:
    assert _mri_to_oid("28:bot-id") == "28:bot-id"
    assert _mri_to_oid("") == ""


def test_parse_from_url_extracts_mri() -> None:
    url = "https://teams.microsoft.com/api/chatsvc/ca/v1/users/ME/contacts/8:orgid:abc-123"
    assert _parse_from_url(url) == "8:orgid:abc-123"


def test_parse_from_url_handles_none_and_garbage() -> None:
    assert _parse_from_url(None) == ""
    assert _parse_from_url("") == ""
    # Falls through to input when the path doesn't end in /contacts/<mri>.
    assert _parse_from_url("not-a-url") == "not-a-url"


def test_parse_consumption_horizon_valid() -> None:
    # "<msgId>;<readAtEpochMs>;<extra>"
    dt = _parse_consumption_horizon("msg-abc;1779710000000;noise")
    assert dt is not None
    assert dt.tzinfo == UTC
    assert dt == datetime.fromtimestamp(1779710000, tz=UTC)


@pytest.mark.parametrize(
    "value",
    [None, "", "single-segment-no-semicolon", "msg;not-an-int", "msg;;extra"],
)
def test_parse_consumption_horizon_invalid_returns_none(value: str | None) -> None:
    assert _parse_consumption_horizon(value) is None


@pytest.mark.parametrize(
    "product_thread_type, expected",
    [
        ("OneToOneChat", ChatType.ONE_ON_ONE),
        ("Meeting", ChatType.MEETING),
        ("Chat", ChatType.GROUP),
        ("TeamsTeam", ChatType.GROUP),  # unknown types fall through to GROUP
        ("", ChatType.GROUP),
    ],
)
def test_classify_chat_type_from_product_type(product_thread_type: str, expected: ChatType) -> None:
    assert (
        _classify_chat_type({"threadProperties": {"productThreadType": product_thread_type}})
        == expected
    )


# ---------- chat_from_chatsvc ----------


def _convs(load_fixture: Callable[[str], dict[str, Any]]) -> list[dict[str, Any]]:
    convs: list[dict[str, Any]] = load_fixture("chatsvc_conversations.json")["conversations"]
    return convs


def test_chat_from_chatsvc_returns_none_for_teams_team(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    channel = next(
        c
        for c in _convs(load_fixture)
        if c["threadProperties"].get("productThreadType") == "TeamsTeam"
    )
    assert chat_from_chatsvc(channel, my_user_id=ME) is None


def test_chat_from_chatsvc_returns_none_for_notification_stream(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    stream = next(
        c
        for c in _convs(load_fixture)
        if c["threadProperties"].get("productThreadType") == "StreamOfNotifications"
    )
    assert chat_from_chatsvc(stream, my_user_id=ME) is None


def test_chat_from_chatsvc_group_chat_maps_fields(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    group = next(c for c in _convs(load_fixture) if c["id"] == "19:abcdef1234567890@thread.v2")
    chat = chat_from_chatsvc(group, my_user_id=ME)
    assert chat is not None
    assert chat.id == "19:abcdef1234567890@thread.v2"
    assert chat.topic == "Project Phoenix"
    assert chat.chat_type == ChatType.GROUP
    assert chat.members == []
    assert chat.last_message is not None
    assert chat.last_message.from_.name == "Alice Group"
    assert chat.last_message.from_.user_id == "11111111-1111-1111-1111-111111111111"
    assert chat.last_message.from_.from_me is False
    # consumption_horizon (1779710000000 ms) < lastMessage time → unread.
    assert chat.has_unread is True
    assert chat.last_updated.year == 2026


def test_chat_from_chatsvc_one_on_one_classification(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    one_on_one = next(c for c in _convs(load_fixture) if "@unq.gbl.spaces" in c["id"])
    chat = chat_from_chatsvc(one_on_one, my_user_id=ME)
    assert chat is not None
    assert chat.chat_type == ChatType.ONE_ON_ONE
    # consumption_horizon (2026-05-26 in the fixture) > lastMessage (2026-05-25T12:30) → read.
    assert chat.has_unread is False


def test_chat_from_chatsvc_from_me_chat_unread_follows_cursor(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    """A from-me chat IS unread when the read cursor is behind the last message.

    The from-me suppression was removed so explicit `chat mark-unread` is
    honored even on chats where you sent last. In this fixture the
    consumptionhorizon (2026-05-24) is behind the last (from-me) message
    (2026-05-25), so the chat is unread.
    """
    self_chat = next(c for c in _convs(load_fixture) if c["id"] == "19:selfmsg@thread.v2")
    chat = chat_from_chatsvc(self_chat, my_user_id=ME)
    assert chat is not None
    assert chat.last_message is not None
    assert chat.last_message.from_.from_me is True
    assert chat.has_unread is True


# ---------- message_from_chatsvc ----------


def _msgs(load_fixture: Callable[[str], dict[str, Any]]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = load_fixture("chatsvc_messages.json")["messages"]
    return msgs


def test_message_from_chatsvc_richtext_html_maps_as_html(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    raw = _msgs(load_fixture)[0]  # RichText/Html
    msg = message_from_chatsvc(raw, my_user_id=ME)
    assert msg.body_format == "html"
    assert msg.body == "<p>hello team</p>"
    assert msg.from_.name == "Alice Group"
    assert msg.from_.from_me is False
    assert msg.chat_id == "19:abcdef1234567890@thread.v2"
    assert msg.reactions == []
    assert msg.reply_to_id == "987654321"
    assert msg.importance == "normal"


def test_message_from_chatsvc_text_maps_as_text_and_from_me(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    raw = _msgs(load_fixture)[1]  # Text, from ME
    msg = message_from_chatsvc(raw, my_user_id=ME)
    assert msg.body_format == "text"
    assert msg.from_.from_me is True


def test_message_from_chatsvc_marks_deleted(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    raw = next(m for m in _msgs(load_fixture) if m.get("deletetime"))
    msg = message_from_chatsvc(raw, my_user_id=ME)
    assert msg.is_deleted is True


# ---------- WebChats over mocked chatsvc ----------


def _build_client(skype_token: str = "fake-skype") -> ApiClient:
    """A bare ApiClient with token getters that don't touch the network."""
    return ApiClient(
        get_graph_token=lambda: "irrelevant",
        get_teams_token=lambda: "irrelevant",
        get_skype_token=lambda: skype_token,
    )


@respx.mock
def test_list_chats_filters_non_chat_threads(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    """Channels and notification streams must never appear in the result."""
    body = load_fixture("chatsvc_conversations.json")
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(200, json=body)
    )

    wc = WebChats(client=_build_client(), my_user_id=ME)
    chats, _ = wc.list_chats(top=10)

    ids = [c.id for c in chats]
    assert "19:teamchannel111@thread.tacv2" not in ids
    assert "48:notifications" not in ids
    # All four "Chat"-product entries survive (group, 1:1, meeting, self).
    assert len(chats) == 4


@respx.mock
def test_list_chats_truncates_to_top(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    body = load_fixture("chatsvc_conversations.json")
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(200, json=body)
    )

    wc = WebChats(client=_build_client(), my_user_id=ME)
    chats, _ = wc.list_chats(top=2)
    assert len(chats) == 2


@respx.mock
def test_list_chats_unread_only_filters(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    body = load_fixture("chatsvc_conversations.json")
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(200, json=body)
    )

    wc = WebChats(client=_build_client(), my_user_id=ME)
    chats, _ = wc.list_chats(top=10, unread_only=True)
    # Only the "Project Phoenix" group chat has has_unread=True in the fixture.
    assert all(c.has_unread for c in chats)
    assert any(c.topic == "Project Phoenix" for c in chats)


@respx.mock
def test_list_chats_type_filter(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    body = load_fixture("chatsvc_conversations.json")
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(200, json=body)
    )

    wc = WebChats(client=_build_client(), my_user_id=ME)
    chats, _ = wc.list_chats(top=10, chat_type="oneOnOne")
    assert len(chats) == 1
    assert chats[0].chat_type == ChatType.ONE_ON_ONE


@respx.mock
def test_list_chats_safety_cap_stops_paging(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    """When no chats are found, the loop must stop at the page cap (5) — not loop forever."""
    body = load_fixture("chatsvc_conversations.json")
    # Strip every recognized chat-product type to force the page-cap exit path.
    chat_product_types = {"Chat", "OneToOneChat", "Meeting"}
    body = json.loads(json.dumps(body))  # deep copy
    body["conversations"] = [
        c
        for c in body["conversations"]
        if c["threadProperties"].get("productThreadType") not in chat_product_types
    ]
    body["_metadata"] = {
        "syncState": "https://teams.microsoft.com/api/chatsvc/ca/v1/users/ME/conversations?cursor=next"
    }

    route = respx.get(
        url__regex=r"https://teams\.microsoft\.com/api/chatsvc/ca/v1/users/ME/conversations.*"
    ).mock(return_value=httpx.Response(200, json=body))

    wc = WebChats(client=_build_client(), my_user_id=ME)
    chats, next_skip = wc.list_chats(top=10)
    assert chats == []
    # Hard-coded cap in web_chats.py is 5 pages without --all.
    assert route.call_count == 5
    assert next_skip is not None


@respx.mock
def test_list_messages_skips_system_events_and_backfills_chat_id(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    chat_id = "19:abcdef1234567890@thread.v2"
    body = load_fixture("chatsvc_messages.json")
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/messages").mock(
        return_value=httpx.Response(200, json=body)
    )

    wc = WebChats(client=_build_client(), my_user_id=ME)
    msgs, _ = wc.list_messages(chat_id, top=10)

    # System events (Event/* and ThreadActivity/*) must be dropped.
    msg_types = {m.id for m in msgs}
    assert "1000000000003" not in msg_types  # Event/Call
    assert "1000000000004" not in msg_types  # ThreadActivity/MemberJoined
    # Three real messages remain.
    assert len(msgs) == 3

    # The deleted message in the fixture has conversationid="" — chat_id is back-filled.
    deleted = next(m for m in msgs if m.id == "1000000000005")
    assert deleted.chat_id == chat_id
    assert deleted.is_deleted is True


@respx.mock
def test_list_messages_since_sets_starttime_param(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    """The `since` datetime must be passed as `startTime` epoch-ms."""
    chat_id = "19:abcdef1234567890@thread.v2"
    body = load_fixture("chatsvc_messages.json")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=body)

    respx.get(
        url__startswith=f"{CHATSVC_AMER_BASE}/users/ME/conversations/{chat_id}/messages"
    ).mock(side_effect=handler)

    since = datetime(2026, 5, 25, 0, 0, 0, tzinfo=UTC)
    wc = WebChats(client=_build_client(), my_user_id=ME)
    wc.list_messages(chat_id, top=5, since=since)
    assert captured["params"]["startTime"] == str(int(since.timestamp() * 1000))
    assert captured["params"]["pageSize"] == "5"


@respx.mock
def test_list_chats_raises_on_401() -> None:
    from teams_cli.errors import SessionExpired

    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(401, text='{"error":"unauthorized"}')
    )
    wc = WebChats(client=_build_client(), my_user_id=ME)
    with pytest.raises(SessionExpired):
        wc.list_chats(top=5)


@respx.mock
def test_list_chats_raises_apierror_on_500() -> None:
    from teams_cli.errors import ApiError

    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(500, text="boom")
    )
    wc = WebChats(client=_build_client(), my_user_id=ME)
    with pytest.raises(ApiError) as ei:
        wc.list_chats(top=5)
    assert ei.value.status_code == 500


# ---------- 1:1 chat member backfill (lazy /threads/{id} + graph /users/{oid}) ----------
#
# chatsvc's list view doesn't include thread members, so for 1:1 chats where I
# sent the last message the renderer falls through to "(self)". We backfill
# the other participant lazily by GET /threads/{id} on chatsvc (gives MRIs)
# then GET /users/{oid} on Graph (gives displayName).

_BACKFILL_ME = "11111111-1111-1111-1111-111111111111"
_BACKFILL_OTHER_OID = "22222222-2222-2222-2222-222222222222"
_BACKFILL_CHAT_ID = (
    "19:11111111-1111-1111-1111-111111111111_22222222-2222-2222-2222-222222222222@unq.gbl.spaces"
)


def _one_on_one_me_last_payload() -> dict[str, Any]:
    """A chatsvc /me/conversations payload with one 1:1 chat where I sent the last message."""
    return {
        "conversations": [
            {
                "id": _BACKFILL_CHAT_ID,
                "type": "Conversation",
                "threadProperties": {
                    "threadType": "chat",
                    "productThreadType": "OneToOneChat",
                },
                "properties": {
                    "lastimreceivedtime": "2026-05-25T13:00:00.000Z",
                    "consumptionhorizon": "msg-1;1779710000000;extra",
                },
                "lastMessage": {
                    "id": "9999999999500",
                    "messagetype": "Text",
                    "content": "from me",
                    "from": (
                        "https://teams.microsoft.com/api/chatsvc/ca/v1/users/ME/contacts/"
                        f"8:orgid:{_BACKFILL_ME}"
                    ),
                    "imdisplayname": "Me",
                    "composetime": "2026-05-25T13:00:00.000Z",
                },
            }
        ],
        "_metadata": {},
    }


@respx.mock
def test_list_chats_backfills_members_when_one_on_one_last_from_me(
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    """1:1 chat with last_message.from_me=True must trigger a thread fetch + Graph
    lookup, and the resulting Chat must carry the other person as a member."""
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(200, json=_one_on_one_me_last_payload())
    )
    thread_route = respx.get(f"{CHATSVC_AMER_BASE}/threads/{_BACKFILL_CHAT_ID}").mock(
        return_value=httpx.Response(200, json=load_fixture("chatsvc_thread.json"))
    )
    graph_route = respx.get(f"{GRAPH_BASE}/users/{_BACKFILL_OTHER_OID}").mock(
        return_value=httpx.Response(200, json=load_fixture("graph_user_by_oid.json"))
    )

    wc = WebChats(client=_build_client(), my_user_id=_BACKFILL_ME)
    chats, _ = wc.list_chats(top=5)

    assert thread_route.call_count == 1
    assert graph_route.call_count == 1
    assert len(chats) == 1
    chat = chats[0]
    assert chat.chat_type == ChatType.ONE_ON_ONE
    others = [m for m in chat.members if not m.from_me]
    assert len(others) == 1
    assert others[0].name == "Bob Builder"
    assert others[0].user_id == _BACKFILL_OTHER_OID
    assert others[0].email == "bob.builder@example.com"


@respx.mock
def test_list_chats_does_not_backfill_when_one_on_one_other_sent_last() -> None:
    """If the OTHER person sent the last message we already have a usable name
    (last_message.from_.name). No /threads or /users call should be made.

    Uses a self-contained payload so a future fixture change can't silently
    weaken this assertion.
    """
    body = {
        "conversations": [
            {
                "id": _BACKFILL_CHAT_ID,
                "type": "Conversation",
                "threadProperties": {
                    "threadType": "chat",
                    "productThreadType": "OneToOneChat",
                },
                "properties": {
                    "lastimreceivedtime": "2026-05-25T13:00:00.000Z",
                },
                "lastMessage": {
                    "id": "9999999999700",
                    "messagetype": "Text",
                    "content": "hello",
                    "from": (
                        "https://teams.microsoft.com/api/chatsvc/ca/v1/users/ME/contacts/"
                        f"8:orgid:{_BACKFILL_OTHER_OID}"
                    ),
                    "imdisplayname": "Bob Builder",
                    "composetime": "2026-05-25T13:00:00.000Z",
                },
            }
        ],
        "_metadata": {},
    }
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(200, json=body)
    )
    thread_route = respx.get(url__regex=rf"{re.escape(CHATSVC_AMER_BASE)}/threads/.+").mock(
        return_value=httpx.Response(200, json={})
    )
    graph_route = respx.get(url__regex=rf"{re.escape(GRAPH_BASE)}/users/.+").mock(
        return_value=httpx.Response(200, json={})
    )

    wc = WebChats(client=_build_client(), my_user_id=_BACKFILL_ME)
    wc.list_chats(top=5)

    assert thread_route.call_count == 0
    assert graph_route.call_count == 0


@respx.mock
def test_list_chats_does_not_backfill_for_group_chats() -> None:
    """Group chats use `topic` for their label and never need member backfill."""
    body = {
        "conversations": [
            {
                "id": "19:groupchat@thread.v2",
                "type": "Conversation",
                "threadProperties": {
                    "topic": "Project X",
                    "threadType": "chat",
                    "productThreadType": "Chat",
                },
                "properties": {
                    "lastimreceivedtime": "2026-05-25T13:00:00.000Z",
                },
                "lastMessage": {
                    "id": "9999999999600",
                    "messagetype": "Text",
                    "content": "from me",
                    "from": (
                        "https://teams.microsoft.com/api/chatsvc/ca/v1/users/ME/contacts/"
                        f"8:orgid:{_BACKFILL_ME}"
                    ),
                    "imdisplayname": "Me",
                    "composetime": "2026-05-25T13:00:00.000Z",
                },
            }
        ],
        "_metadata": {},
    }
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(200, json=body)
    )
    thread_route = respx.get(url__regex=rf"{re.escape(CHATSVC_AMER_BASE)}/threads/.+").mock(
        return_value=httpx.Response(200, json={})
    )
    graph_route = respx.get(url__regex=rf"{re.escape(GRAPH_BASE)}/users/.+").mock(
        return_value=httpx.Response(200, json={})
    )

    wc = WebChats(client=_build_client(), my_user_id=_BACKFILL_ME)
    wc.list_chats(top=5)

    assert thread_route.call_count == 0
    assert graph_route.call_count == 0


@respx.mock
def test_list_chats_backfill_swallows_thread_endpoint_errors() -> None:
    """If /threads/{id} fails, the chat is returned unchanged (no exception)."""
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(200, json=_one_on_one_me_last_payload())
    )
    respx.get(f"{CHATSVC_AMER_BASE}/threads/{_BACKFILL_CHAT_ID}").mock(
        return_value=httpx.Response(500, text="boom")
    )

    wc = WebChats(client=_build_client(), my_user_id=_BACKFILL_ME)
    chats, _ = wc.list_chats(top=5)

    assert len(chats) == 1
    assert chats[0].members == []


@respx.mock
def test_list_chats_backfill_skips_when_only_me_in_thread() -> None:
    """If /threads/{id} returns only my own MRI as a member, populating
    members=[me_only] would still render as "(self)" — so we should skip
    the update AND not waste a Graph call resolving anyone."""
    only_me_thread = {
        "id": _BACKFILL_CHAT_ID,
        "type": "Thread",
        "members": [
            {
                "id": f"8:orgid:{_BACKFILL_ME}",
                "type": "ThreadMember",
                "role": "Admin",
            }
        ],
        "properties": {},
    }
    respx.get(f"{CHATSVC_AMER_BASE}/users/ME/conversations").mock(
        return_value=httpx.Response(200, json=_one_on_one_me_last_payload())
    )
    respx.get(f"{CHATSVC_AMER_BASE}/threads/{_BACKFILL_CHAT_ID}").mock(
        return_value=httpx.Response(200, json=only_me_thread)
    )
    graph_route = respx.get(url__regex=rf"{re.escape(GRAPH_BASE)}/users/.+").mock(
        return_value=httpx.Response(200, json={})
    )

    wc = WebChats(client=_build_client(), my_user_id=_BACKFILL_ME)
    chats, _ = wc.list_chats(top=5)

    # No Graph lookup should have been issued — there's no non-me OID to resolve.
    assert graph_route.call_count == 0
    # And the chat is returned with no backfilled members so the renderer
    # falls through to "(self)" cleanly rather than to a misleading
    # members=[me_only] list.
    assert len(chats) == 1
    assert chats[0].members == []
