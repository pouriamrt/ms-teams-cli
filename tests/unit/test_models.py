from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from teams_cli.api.models import (
    ChatType,
    Person,
    Reaction,
    chat_from_graph,
    message_from_graph,
)

LoadFixture = Callable[[str], dict[str, Any]]


def test_person_from_graph_user() -> None:
    p = Person.from_graph_user(
        {"id": "uid", "displayName": "Alice", "userIdentityType": "aadUser"},
        my_user_id="me",
        email="alice@x",
    )
    assert p.user_id == "uid"
    assert p.name == "Alice"
    assert p.email == "alice@x"
    assert p.from_me is False


def test_chat_from_graph_oneonone(load_fixture: LoadFixture) -> None:
    body = load_fixture("graph_chats_list.json")
    chat = chat_from_graph(body["value"][0], my_user_id="99999999-8888-7777-6666-555555555555")
    assert chat.id.endswith("@unq.gbl.spaces")
    assert chat.chat_type == ChatType.ONE_ON_ONE
    assert chat.topic is None
    assert {m.email for m in chat.members} == {"alice@example.com", "pouriamortezaagha7@gmail.com"}
    assert chat.last_message is not None
    assert chat.last_message.preview == "lgtm, merging now"
    assert chat.last_message.from_.from_me is False
    # has_unread: lastMessageReadDateTime (13:00) < last message createdDateTime (14:03)
    assert chat.has_unread is True
    assert chat.unread_count is None  # not populated by default
    assert chat.last_updated == datetime(2026, 5, 22, 14, 3, tzinfo=UTC)


def test_chat_from_graph_group(load_fixture: LoadFixture) -> None:
    body = load_fixture("graph_chats_list.json")
    chat = chat_from_graph(body["value"][1], my_user_id="99999999-8888-7777-6666-555555555555")
    assert chat.chat_type == ChatType.GROUP
    assert chat.topic == "Platform planning"
    # read time equals message time → no unread
    assert chat.has_unread is False


def test_message_from_graph(load_fixture: LoadFixture) -> None:
    body = load_fixture("graph_chat_messages.json")
    msg = message_from_graph(body["value"][0], my_user_id="99999999-8888-7777-6666-555555555555")
    assert msg.id == "1716393780123"
    assert msg.chat_id.endswith("@unq.gbl.spaces")
    assert msg.from_.name == "Alice Smith"
    assert msg.from_.from_me is False
    assert msg.body == "<p>lgtm, merging now</p>"
    assert msg.body_format == "html"
    assert msg.is_deleted is False
    assert len(msg.reactions) == 1
    assert msg.reactions[0].reaction_type == "like"
    assert msg.reactions[0].user.from_me is True


def test_message_from_me_marks_from_me(load_fixture: LoadFixture) -> None:
    body = load_fixture("graph_chat_messages.json")
    msg = message_from_graph(body["value"][1], my_user_id="99999999-8888-7777-6666-555555555555")
    assert msg.from_.from_me is True


def test_unknown_chat_type_falls_back_to_group(load_fixture: LoadFixture) -> None:
    body = load_fixture("graph_chats_list.json")
    raw = dict(body["value"][0])
    raw["chatType"] = "meeting"
    chat = chat_from_graph(raw, my_user_id="anyone")
    assert chat.chat_type == ChatType.MEETING


def test_reaction_uses_str_not_enum() -> None:
    """We don't constrain reactionType to the 6-emoji enum at the model layer —
    the wire vocabulary changes; the CLI surface is what validates."""
    r = Reaction(
        reaction_type="custom_emoji_xyz",
        user=Person(user_id="u", name="x", email="x@y", from_me=False),
        created_at=datetime.now(UTC),
    )
    assert r.reaction_type == "custom_emoji_xyz"
