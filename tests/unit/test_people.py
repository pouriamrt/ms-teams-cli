from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from freezegun import freeze_time

from teams_cli.api.client import GRAPH_BASE, ApiClient
from teams_cli.api.models import Person
from teams_cli.api.people import PeopleResolver
from teams_cli.errors import NotFound


@pytest.fixture
def client() -> ApiClient:
    return ApiClient(
        get_graph_token=lambda: "AT",
        get_teams_token=lambda: "TT",
        get_skype_token=lambda: "STT",
    )


@respx.mock
def test_resolve_by_email_round_trip(
    tmp_path: Path,
    client: ApiClient,
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    cache = tmp_path / "people.json"
    body = load_fixture("graph_user.json")
    route = respx.get(f"{GRAPH_BASE}/users/alice@example.com").mock(
        return_value=httpx.Response(200, json=body)
    )
    resolver = PeopleResolver(client=client, cache_path=cache)
    person = resolver.resolve("alice@example.com")
    assert isinstance(person, Person)
    assert person.user_id == "ab06770f-89ff-4bb2-99a1-2b0b8d52975e"
    assert person.email == "alice@example.com"
    assert person.name == "Alice Smith"
    assert route.called


@respx.mock
def test_resolve_caches_for_24h(
    tmp_path: Path,
    client: ApiClient,
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    cache = tmp_path / "people.json"
    body = load_fixture("graph_user.json")
    route = respx.get(f"{GRAPH_BASE}/users/alice@example.com").mock(
        return_value=httpx.Response(200, json=body)
    )
    with freeze_time("2026-05-22T18:00:00Z"):
        resolver = PeopleResolver(client=client, cache_path=cache)
        resolver.resolve("alice@example.com")
        resolver.resolve("alice@example.com")
    assert route.call_count == 1
    cached = json.loads(cache.read_text("utf-8"))
    assert "alice@example.com" in cached["entries"]


@respx.mock
def test_resolve_404_raises_not_found(tmp_path: Path, client: ApiClient) -> None:
    cache = tmp_path / "people.json"
    respx.get(f"{GRAPH_BASE}/users/ghost@example.com").mock(
        return_value=httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound"}})
    )
    resolver = PeopleResolver(client=client, cache_path=cache)
    with pytest.raises(NotFound):
        resolver.resolve("ghost@example.com")


@respx.mock
def test_resolve_does_not_grow_cache_unbounded(
    tmp_path: Path,
    client: ApiClient,
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    body = load_fixture("graph_user.json")
    cache = tmp_path / "people.json"
    # Pre-load 256 entries so the next insert triggers eviction.
    pre = {
        f"u{i}@x.com": {
            "user_id": f"u{i}",
            "name": f"U{i}",
            "email": f"u{i}@x.com",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
        for i in range(256)
    }
    cache.write_text(json.dumps({"entries": pre}))
    respx.get(f"{GRAPH_BASE}/users/alice@example.com").mock(
        return_value=httpx.Response(200, json=body)
    )
    resolver = PeopleResolver(client=client, cache_path=cache)
    resolver.resolve("alice@example.com")
    after = json.loads(cache.read_text("utf-8"))
    assert len(after["entries"]) <= 256
    assert "alice@example.com" in after["entries"]
