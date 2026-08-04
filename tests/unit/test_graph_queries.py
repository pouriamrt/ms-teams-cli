"""Unit tests for GraphChats.list_chats (paging, filters, optional unread counts)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC
from typing import Any

import httpx
import pytest
import respx

from teams_cli.api.client import GRAPH_BASE, ApiClient
from teams_cli.api.graph_chats import GraphChats


@pytest.fixture
def client() -> ApiClient:
    return ApiClient(
        get_graph_token=lambda: "AT",
        get_teams_token=lambda: "TT",
        get_skype_token=lambda: "STT",
    )


@respx.mock
def test_list_chats_builds_correct_query(
    client: ApiClient, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    body = load_fixture("graph_chats_list.json")
    route = respx.get(f"{GRAPH_BASE}/me/chats").mock(return_value=httpx.Response(200, json=body))

    gc = GraphChats(
        client=client, my_user_id="99999999-8888-7777-6666-555555555555", tenant_id="tid"
    )
    chats, next_skip = gc.list_chats(top=25)
    assert len(chats) == 2
    assert chats[0].id.endswith("@unq.gbl.spaces")
    assert next_skip is None
    assert route.called
    req = route.calls.last.request
    raw = req.url.raw_path.decode()
    assert "%24top=25" in raw or "$top=25" in raw


@respx.mock
def test_list_chats_paginates_via_nextlink(
    client: ApiClient, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    body1 = load_fixture("graph_chats_list.json")
    body1 = dict(body1, **{"@odata.nextLink": f"{GRAPH_BASE}/me/chats?$skip=25"})
    body2: dict[str, Any] = {"value": []}
    respx.get(f"{GRAPH_BASE}/me/chats", params={"$skip": "25"}).mock(
        return_value=httpx.Response(200, json=body2)
    )
    respx.get(f"{GRAPH_BASE}/me/chats").mock(return_value=httpx.Response(200, json=body1))

    gc = GraphChats(
        client=client, my_user_id="99999999-8888-7777-6666-555555555555", tenant_id="tid"
    )
    chats, next_skip = gc.list_chats(top=25, follow_all=True)
    assert len(chats) == 2  # first page had data, second was empty
    assert next_skip is None


@respx.mock
def test_list_chats_filter_unread(
    client: ApiClient, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    body = load_fixture("graph_chats_list.json")
    respx.get(f"{GRAPH_BASE}/me/chats").mock(return_value=httpx.Response(200, json=body))

    gc = GraphChats(
        client=client, my_user_id="99999999-8888-7777-6666-555555555555", tenant_id="tid"
    )
    chats, _ = gc.list_chats(top=25, unread_only=True)
    # Fixture: first chat is unread (lastRead 13:00 < lastMsg 14:03); second is read.
    assert len(chats) == 1
    assert chats[0].chat_type.value == "oneOnOne"


@respx.mock
def test_list_chats_filter_type(
    client: ApiClient, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    body = load_fixture("graph_chats_list.json")
    respx.get(f"{GRAPH_BASE}/me/chats").mock(return_value=httpx.Response(200, json=body))

    gc = GraphChats(
        client=client, my_user_id="99999999-8888-7777-6666-555555555555", tenant_id="tid"
    )
    chats, _ = gc.list_chats(top=25, chat_type="group")
    assert len(chats) == 1
    assert chats[0].chat_type.value == "group"


@respx.mock
def test_list_chats_with_counts_populates_unread_count(
    client: ApiClient, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    chats_body = load_fixture("graph_chats_list.json")
    messages_body = load_fixture("graph_chat_messages.json")
    respx.get(f"{GRAPH_BASE}/me/chats").mock(return_value=httpx.Response(200, json=chats_body))
    # --with-counts queries each unread chat's messages since lastReadDateTime.
    chat_id = chats_body["value"][0]["id"]
    respx.get(f"{GRAPH_BASE}/chats/{chat_id}/messages").mock(
        return_value=httpx.Response(200, json=messages_body)
    )

    gc = GraphChats(
        client=client, my_user_id="99999999-8888-7777-6666-555555555555", tenant_id="tid"
    )
    chats, _ = gc.list_chats(top=25, with_counts=True)
    one_on_one = next(c for c in chats if c.chat_type.value == "oneOnOne")
    assert one_on_one.unread_count is not None
    assert one_on_one.unread_count >= 0


@respx.mock
def test_list_chats_session_expired_raises(client: ApiClient) -> None:
    from teams_cli.errors import ApiError

    respx.get(f"{GRAPH_BASE}/me/chats").mock(return_value=httpx.Response(500))

    gc = GraphChats(client=client, my_user_id="me", tenant_id="tid")
    with pytest.raises(ApiError) as ei:
        gc.list_chats(top=25)
    assert ei.value.status_code == 500


@respx.mock
def test_list_messages_returns_in_chrono_order(
    client: ApiClient, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    body = load_fixture("graph_chat_messages.json")
    chat_id = "19:abc@unq.gbl.spaces"
    respx.get(f"{GRAPH_BASE}/chats/{chat_id}/messages").mock(
        return_value=httpx.Response(200, json=body)
    )

    gc = GraphChats(
        client=client, my_user_id="99999999-8888-7777-6666-555555555555", tenant_id="tid"
    )
    msgs, next_skip = gc.list_messages(chat_id, top=25)
    # Graph returns desc; we expose desc by default (newest-first), but offer reverse for rendering.
    assert msgs[0].id == "1716393780123"
    assert msgs[1].id == "1716393720000"


@respx.mock
def test_list_messages_since_filter_applied(
    client: ApiClient, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    body = load_fixture("graph_chat_messages.json")
    chat_id = "19:abc@unq.gbl.spaces"
    route = respx.get(f"{GRAPH_BASE}/chats/{chat_id}/messages").mock(
        return_value=httpx.Response(200, json=body)
    )

    from datetime import datetime

    gc = GraphChats(client=client, my_user_id="me", tenant_id="tid")
    since = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    gc.list_messages(chat_id, top=10, since=since)
    req = route.calls.last.request
    raw = req.url.raw_path.decode()
    assert (
        "createdDateTime%20gt%20" in raw
        or "createdDateTime gt " in raw
        or "createdDateTime+gt+" in raw
    )


@respx.mock
def test_list_messages_follow_all_chains_nextlink(
    client: ApiClient, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    body1 = load_fixture("graph_chat_messages.json")
    body1 = dict(body1, **{"@odata.nextLink": f"{GRAPH_BASE}/chats/19:c@y/messages?$skip=25"})
    body2: dict[str, Any] = {"value": []}
    chat_id = "19:c@y"
    # Register the more-specific mock first so respx matches it before the catch-all.
    respx.get(f"{GRAPH_BASE}/chats/{chat_id}/messages", params={"$skip": "25"}).mock(
        return_value=httpx.Response(200, json=body2)
    )
    respx.get(f"{GRAPH_BASE}/chats/{chat_id}/messages").mock(
        return_value=httpx.Response(200, json=body1)
    )

    gc = GraphChats(client=client, my_user_id="me", tenant_id="tid")
    msgs, _ = gc.list_messages(chat_id, top=25, follow_all=True)
    assert len(msgs) == 2


@respx.mock
def test_ensure_one_on_one_creates_chat(client: ApiClient) -> None:
    body = {
        "id": "19:me-id_alice-id@unq.gbl.spaces",
        "chatType": "oneOnOne",
        "members": [],
    }
    route = respx.post(f"{GRAPH_BASE}/chats").mock(return_value=httpx.Response(201, json=body))
    gc = GraphChats(client=client, my_user_id="me-id", tenant_id="tid")
    chat_id = gc.ensure_one_on_one("alice-id")
    assert chat_id == "19:me-id_alice-id@unq.gbl.spaces"
    sent = json.loads(route.calls.last.request.content.decode())
    assert sent["chatType"] == "oneOnOne"
    assert len(sent["members"]) == 2
    bindings = [m["user@odata.bind"] for m in sent["members"]]
    assert any("'me-id'" in b for b in bindings)
    assert any("'alice-id'" in b for b in bindings)


@respx.mock
def test_ensure_one_on_one_idempotent_409(client: ApiClient) -> None:
    """Graph may return 409 + existing chat; we treat as success and parse the chat-id."""
    body = {"error": {"code": "Conflict", "message": "Chat exists"}}
    respx.post(f"{GRAPH_BASE}/chats").mock(return_value=httpx.Response(409, json=body))
    # On 409, the implementation falls back to a list filtered by member to find the existing chat.
    list_body = {
        "value": [
            {
                "id": "19:me-id_alice-id@unq.gbl.spaces",
                "chatType": "oneOnOne",
                "members": [
                    {
                        "userId": "me-id",
                        "displayName": "Me",
                        "email": "me@x",
                        "@odata.type": "#microsoft.graph.aadUserConversationMember",
                    },
                    {
                        "userId": "alice-id",
                        "displayName": "A",
                        "email": "a@x",
                        "@odata.type": "#microsoft.graph.aadUserConversationMember",
                    },
                ],
            }
        ]
    }
    respx.get(f"{GRAPH_BASE}/me/chats").mock(return_value=httpx.Response(200, json=list_body))
    gc = GraphChats(client=client, my_user_id="me-id", tenant_id="tid")
    chat_id = gc.ensure_one_on_one("alice-id")
    assert chat_id == "19:me-id_alice-id@unq.gbl.spaces"


@respx.mock
def test_send_message_text(client: ApiClient) -> None:
    chat_id = "19:abc@unq.gbl.spaces"
    body = {
        "id": "1716394000000",
        "createdDateTime": "2026-05-22T14:10:00Z",
        "chatId": chat_id,
        "from": {"user": {"id": "me-id", "displayName": "Me", "userIdentityType": "aadUser"}},
        "body": {"contentType": "text", "content": "hello"},
        "reactions": [],
    }
    route = respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages").mock(
        return_value=httpx.Response(201, json=body)
    )
    gc = GraphChats(client=client, my_user_id="me-id", tenant_id="tid")
    msg = gc.send_message(chat_id, body="hello")
    assert msg.id == "1716394000000"
    sent = json.loads(route.calls.last.request.content.decode())
    assert sent == {"body": {"contentType": "text", "content": "hello"}}


@respx.mock
def test_send_message_html(client: ApiClient) -> None:
    chat_id = "19:abc@unq.gbl.spaces"
    body = {
        "id": "x",
        "createdDateTime": "2026-05-22T14:10:00Z",
        "chatId": chat_id,
        "from": {"user": {"id": "me-id", "displayName": "Me", "userIdentityType": "aadUser"}},
        "body": {"contentType": "html", "content": "<p>hi</p>"},
        "reactions": [],
    }
    route = respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages").mock(
        return_value=httpx.Response(201, json=body)
    )
    gc = GraphChats(client=client, my_user_id="me-id", tenant_id="tid")
    gc.send_message(chat_id, body="<p>hi</p>", html=True, importance="high")
    sent = json.loads(route.calls.last.request.content.decode())
    assert sent["body"]["contentType"] == "html"
    assert sent["importance"] == "high"


@respx.mock
def test_send_message_with_reply_to_quotes_source(
    client: ApiClient, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    source = load_fixture("graph_chat_messages.json")["value"][1]  # "ok if I merge?"
    chat_id = source["chatId"]
    new_body = {
        "id": "new",
        "createdDateTime": "2026-05-22T14:10:00Z",
        "chatId": chat_id,
        "from": {"user": {"id": "me-id", "displayName": "Me", "userIdentityType": "aadUser"}},
        "body": {"contentType": "html", "content": "ack"},
        "reactions": [],
    }
    respx.get(f"{GRAPH_BASE}/chats/{chat_id}/messages/{source['id']}").mock(
        return_value=httpx.Response(200, json=source)
    )
    route = respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages").mock(
        return_value=httpx.Response(201, json=new_body)
    )
    gc = GraphChats(client=client, my_user_id="me-id", tenant_id="tid")
    gc.send_message(chat_id, body="ack", reply_to_message_id=source["id"])
    sent = json.loads(route.calls.last.request.content.decode())
    assert sent["body"]["contentType"] == "html"
    assert "<blockquote>" in sent["body"]["content"] or "<quote>" in sent["body"]["content"].lower()


@respx.mock
def test_set_reaction_graph_path(client: ApiClient) -> None:
    chat_id = "19:abc@unq.gbl.spaces"
    msg_id = "1716393780123"
    route = respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages/{msg_id}/setReaction").mock(
        return_value=httpx.Response(204)
    )
    gc = GraphChats(client=client, my_user_id="me-id", tenant_id="tid")
    result = gc.set_reaction(chat_id, msg_id, reaction_type="like", unreact=False)
    assert result.via == "graph"
    assert result.ok is True
    # Graph v1.0 setReaction expects reactionType as a unicode emoji, NOT the
    # legacy keyword "like" (which returns 400). The CLI's public surface still
    # uses the keyword; the emoji mapping happens only at the wire boundary.
    sent = json.loads(route.calls.last.request.content.decode())
    assert sent == {"reactionType": "👍"}
    # The keyword is preserved on the result for display / JSON output.
    assert result.reaction_type == "like"


@pytest.mark.parametrize(
    ("keyword", "emoji"),
    [
        ("like", "👍"),
        ("heart", "❤️"),
        ("laugh", "😆"),
        ("surprised", "😮"),
        ("sad", "😢"),
        ("angry", "😡"),
    ],
)
@respx.mock
def test_set_reaction_maps_each_keyword_to_unicode(
    client: ApiClient, keyword: str, emoji: str
) -> None:
    chat_id = "19:abc@unq.gbl.spaces"
    msg_id = "m1"
    route = respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages/{msg_id}/setReaction").mock(
        return_value=httpx.Response(204)
    )
    gc = GraphChats(client=client, my_user_id="me-id", tenant_id="tid")
    gc.set_reaction(chat_id, msg_id, reaction_type=keyword, unreact=False)
    sent = json.loads(route.calls.last.request.content.decode())
    assert sent == {"reactionType": emoji}


@respx.mock
def test_unset_reaction_graph_path(client: ApiClient) -> None:
    chat_id = "19:abc@unq.gbl.spaces"
    msg_id = "1716393780123"
    route = respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages/{msg_id}/unsetReaction").mock(
        return_value=httpx.Response(204)
    )
    gc = GraphChats(client=client, my_user_id="me-id", tenant_id="tid")
    result = gc.set_reaction(chat_id, msg_id, reaction_type="like", unreact=True)
    assert result.via == "graph"
    assert route.called
    # unsetReaction also takes the unicode emoji, not the legacy keyword.
    sent = json.loads(route.calls.last.request.content.decode())
    assert sent == {"reactionType": "👍"}


@respx.mock
def test_set_reaction_graph_501_falls_back(client: ApiClient) -> None:
    chat_id = "19:abc@unq.gbl.spaces"
    msg_id = "1716393780123"
    respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages/{msg_id}/setReaction").mock(
        return_value=httpx.Response(501)
    )
    # Mock chatsvc fallback.
    chatsvc_url = (
        f"https://teams.microsoft.com/api/chatsvc/ca/v1/users/ME/conversations/"
        f"{chat_id}/messages/{msg_id}/properties"
    )
    chatsvc_route = respx.post(chatsvc_url).mock(
        return_value=httpx.Response(200, json={"OK": True})
    )
    gc = GraphChats(client=client, my_user_id="me-id", tenant_id="tid")
    result = gc.set_reaction(chat_id, msg_id, reaction_type="like", unreact=False)
    assert result.ok is True
    assert result.via == "chatsvc"
    assert chatsvc_route.called


@respx.mock
def test_set_reaction_both_paths_fail_raises(client: ApiClient) -> None:
    from teams_cli.errors import ApiError

    chat_id = "19:abc@unq.gbl.spaces"
    msg_id = "1716393780123"
    respx.post(f"{GRAPH_BASE}/chats/{chat_id}/messages/{msg_id}/setReaction").mock(
        return_value=httpx.Response(400, text="graph rejected")
    )
    chatsvc_url = (
        f"https://teams.microsoft.com/api/chatsvc/ca/v1/users/ME/conversations/"
        f"{chat_id}/messages/{msg_id}/properties"
    )
    respx.post(chatsvc_url).mock(return_value=httpx.Response(500, text="svc broke"))
    gc = GraphChats(client=client, my_user_id="me-id", tenant_id="tid")
    with pytest.raises(ApiError):
        gc.set_reaction(chat_id, msg_id, reaction_type="like", unreact=False)


@respx.mock
def test_search_chat_messages(
    client: ApiClient, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    body = load_fixture("search_chatmessage.json")
    route = respx.post(f"{GRAPH_BASE}/search/query").mock(
        return_value=httpx.Response(200, json=body)
    )
    gc = GraphChats(client=client, my_user_id="me-id", tenant_id="tid")
    hits, total = gc.search_messages("lgtm", top=10)
    assert total == 2
    assert len(hits) == 2
    assert hits[0]["message_id"] == "1716393780123"
    assert hits[0]["chat_id"].endswith("@unq.gbl.spaces")
    assert hits[0]["preview"] == "lgtm, merging now"
    # Sent body shape:
    sent = json.loads(route.calls.last.request.content.decode())
    assert sent["requests"][0]["entityTypes"] == ["chatMessage"]
    assert sent["requests"][0]["query"]["queryString"] == "lgtm"
    assert sent["requests"][0]["size"] == 10


@respx.mock
def test_search_filtered_by_chat(
    client: ApiClient, load_fixture: Callable[[str], dict[str, Any]]
) -> None:
    body = load_fixture("search_chatmessage.json")
    respx.post(f"{GRAPH_BASE}/search/query").mock(return_value=httpx.Response(200, json=body))
    gc = GraphChats(client=client, my_user_id="me-id", tenant_id="tid")
    chat_id = "19:abc@unq.gbl.spaces"
    hits, _total = gc.search_messages("lgtm", top=10, scope_chat_id=chat_id)
    # Both fixture hits include one matching chat — only the matching hit survives.
    assert all(h["chat_id"] == chat_id for h in hits)
    assert len(hits) == 1
