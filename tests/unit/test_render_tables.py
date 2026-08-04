from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Console
from syrupy.assertion import SnapshotAssertion

from teams_cli.api.models import (
    Chat,
    ChatType,
    Message,
    MessagePreview,
    Person,
    Reaction,
)
from teams_cli.render.tables import render_chat_list, render_messages


def _alice() -> Person:
    return Person(user_id="alice-id", name="Alice Smith", email="alice@example.com", from_me=False)


def _me() -> Person:
    return Person(
        user_id="me-id",
        name="Pouria Mortezaagha",
        email="pouriamortezaagha7@gmail.com",
        from_me=True,
    )


def _sample_chat() -> Chat:
    return Chat(
        id="19:abc@unq.gbl.spaces",
        topic=None,
        chat_type=ChatType.ONE_ON_ONE,
        members=[_alice(), _me()],
        last_message=MessagePreview.model_validate(
            {
                "from": _alice(),
                "preview": "lgtm, merging now",
                "created_at": datetime(2026, 5, 22, 14, 3, tzinfo=UTC),
                "message_id": "1716393780123",
            }
        ),
        has_unread=True,
        unread_count=None,
        is_muted=False,
        last_updated=datetime(2026, 5, 22, 14, 3, tzinfo=UTC),
    )


def test_render_chat_list_includes_index_and_unread_marker(snapshot: SnapshotAssertion) -> None:
    chats = [_sample_chat()]
    console = Console(record=True, width=120, color_system=None)
    render_chat_list(console, chats, indices={1: chats[0].id})
    out = console.export_text()
    assert "Alice Smith" in out
    assert "lgtm, merging now" in out
    assert "1" in out
    assert "●" in out or "*" in out or "[unread]" in out  # any unread marker
    assert out == snapshot


def test_render_messages_oldest_first(snapshot: SnapshotAssertion) -> None:
    msgs = [
        Message(
            id="m2",
            chat_id="19:abc@unq.gbl.spaces",
            **{"from": _alice()},
            created_at=datetime(2026, 5, 22, 14, 3, tzinfo=UTC),
            body="lgtm, merging now",
            body_format="text",
            importance="normal",
            reactions=[],
            is_deleted=False,
            reply_to_id=None,
        ),
        Message(
            id="m1",
            chat_id="19:abc@unq.gbl.spaces",
            **{"from": _me()},
            created_at=datetime(2026, 5, 22, 14, 2, tzinfo=UTC),
            body="ok if I merge?",
            body_format="text",
            importance="normal",
            reactions=[
                Reaction(
                    reaction_type="like",
                    user=_alice(),
                    created_at=datetime(2026, 5, 22, 14, 3, tzinfo=UTC),
                )
            ],
            is_deleted=False,
            reply_to_id=None,
        ),
    ]
    console = Console(record=True, width=120, color_system=None)
    render_messages(console, msgs, indices={1: "m1", 2: "m2"})
    out = console.export_text()
    assert "ok if I merge?" in out
    assert "lgtm, merging now" in out
    # like reaction is shown on the second message
    assert "like" in out or "👍" in out
    assert out == snapshot
