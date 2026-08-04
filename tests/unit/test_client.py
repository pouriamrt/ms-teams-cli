from __future__ import annotations

import httpx
import respx

from teams_cli.api.client import CHATSVC_AMER_BASE, GRAPH_BASE, ApiClient


@respx.mock
def test_graph_get_injects_bearer() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"value": []})

    respx.get(f"{GRAPH_BASE}/me/chats").mock(side_effect=handler)
    client = ApiClient(
        get_graph_token=lambda: "GRAPH-AT",
        get_teams_token=lambda: "TEAMS-AT",
        get_skype_token=lambda: "SKYPE-TOK",
    )
    resp = client.graph_get("/me/chats")
    assert resp.status_code == 200
    assert captured["auth"] == "Bearer GRAPH-AT"


@respx.mock
def test_graph_post_injects_bearer_and_content_type() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["content_type"] = request.headers.get("Content-Type")
        return httpx.Response(201, json={"id": "x"})

    respx.post(f"{GRAPH_BASE}/chats").mock(side_effect=handler)
    client = ApiClient(
        get_graph_token=lambda: "GRAPH-AT",
        get_teams_token=lambda: "TEAMS-AT",
        get_skype_token=lambda: "SKYPE-TOK",
    )
    resp = client.graph_post("/chats", json={"chatType": "oneOnOne"})
    assert resp.status_code == 201
    assert captured["auth"] == "Bearer GRAPH-AT"
    content_type = captured["content_type"]
    assert content_type is not None
    assert content_type.startswith("application/json")


@respx.mock
def test_chatsvc_uses_skypetoken_header_not_bearer() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["authentication"] = request.headers.get("Authentication")
        return httpx.Response(200, json={"ok": True})

    respx.post(f"{CHATSVC_AMER_BASE}/users/ME/conversations/19:x@y/messages/1/properties").mock(
        side_effect=handler
    )
    client = ApiClient(
        get_graph_token=lambda: "GRAPH-AT",
        get_teams_token=lambda: "TEAMS-AT",
        get_skype_token=lambda: "SKYPE-TOK",
    )
    resp = client.chatsvc_post(
        "/users/ME/conversations/19:x@y/messages/1/properties",
        json={"emotions": []},
    )
    assert resp.status_code == 200
    # Skype token goes in `Authentication: skypetoken=...`, NOT in `Authorization`.
    assert captured["auth"] is None
    assert captured["authentication"] == "skypetoken=SKYPE-TOK"


@respx.mock
def test_graph_get_with_query_params() -> None:
    route = respx.get(f"{GRAPH_BASE}/me/chats").mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    client = ApiClient(
        get_graph_token=lambda: "AT",
        get_teams_token=lambda: "TT",
        get_skype_token=lambda: "STT",
    )
    client.graph_get("/me/chats", params={"$top": 10, "$filter": "isHidden eq false"})
    assert route.called
    req = route.calls.last.request
    assert b"%24top=10" in req.url.raw_path or b"$top=10" in req.url.raw_path


@respx.mock
def test_propagates_user_agent() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["ua"] = request.headers.get("User-Agent")
        return httpx.Response(200, json={})

    respx.get(f"{GRAPH_BASE}/me").mock(side_effect=handler)
    client = ApiClient(
        get_graph_token=lambda: "AT",
        get_teams_token=lambda: "TT",
        get_skype_token=lambda: "STT",
    )
    client.graph_get("/me")
    ua = captured["ua"]
    assert ua is not None
    assert "teams-cli" in ua
