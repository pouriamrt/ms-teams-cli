from __future__ import annotations

import io
import json
from datetime import UTC, datetime

from jsonschema import Draft202012Validator
from rich.console import Console

from teams_cli.api.models import (
    Chat,
    ChatType,
    Message,
    MessagePreview,
    Person,
)
from teams_cli.render.json_out import dump_chat_list, dump_messages
from teams_cli.schemas import get_schema


def _person(name: str = "A", uid: str = "uid", email: str = "a@x", from_me: bool = False) -> Person:
    return Person(user_id=uid, name=name, email=email, from_me=from_me)


def test_dump_chat_list_validates_against_schema() -> None:
    chats = [
        Chat(
            id="19:abc@unq.gbl.spaces",
            topic=None,
            chat_type=ChatType.ONE_ON_ONE,
            members=[
                _person("Alice", "alice-id", "alice@x"),
                _person("Me", "me-id", "me@x", from_me=True),
            ],
            last_message=MessagePreview.model_validate(
                {
                    "from": _person("Alice", "alice-id", "alice@x"),
                    "preview": "lgtm",
                    "created_at": datetime(2026, 5, 22, 14, 3, tzinfo=UTC),
                    "message_id": "1716393780123",
                }
            ),
            has_unread=True,
            unread_count=None,
            is_muted=False,
            last_updated=datetime(2026, 5, 22, 14, 3, tzinfo=UTC),
        )
    ]
    buf = io.StringIO()
    dump_chat_list(Console(file=buf), chats, indices={1: chats[0].id}, next_skip=None)
    parsed = json.loads(buf.getvalue())
    Draft202012Validator(get_schema("chat.list")).validate(parsed)
    assert parsed["items"][0]["index"] == 1
    assert parsed["items"][0]["has_unread"] is True
    assert parsed["items"][0]["unread_count"] is None


def test_dump_messages_includes_index_and_chat_id() -> None:
    msgs = [
        Message(
            id="m1",
            chat_id="19:abc@unq.gbl.spaces",
            **{"from": _person()},
            created_at=datetime(2026, 5, 22, 14, 2, tzinfo=UTC),
            body="hi",
            body_format="text",
            importance="normal",
            reactions=[],
            is_deleted=False,
            reply_to_id=None,
        )
    ]
    buf = io.StringIO()
    dump_messages(
        Console(file=buf),
        msgs,
        chat_id="19:abc@unq.gbl.spaces",
        indices={1: "m1"},
        next_skip=None,
    )
    parsed = json.loads(buf.getvalue())
    Draft202012Validator(get_schema("chat.read")).validate(parsed)
    assert parsed["chat_id"] == "19:abc@unq.gbl.spaces"
    assert parsed["items"][0]["index"] == 1
