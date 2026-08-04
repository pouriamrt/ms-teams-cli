"""Stable, schema-versioned JSON output. Top-level keys frozen at v1.0."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from rich.console import Console

from teams_cli.api.models import Chat, Message


def _dt(value: datetime) -> str:
    s = value.isoformat()
    return s.replace("+00:00", "Z") if s.endswith("+00:00") else s


def _person_dict(p: Any) -> dict[str, Any]:
    return {
        "name": p.name,
        "email": p.email,
        "user_id": p.user_id,
        "from_me": p.from_me,
    }


def _chat_dict(chat: Chat, idx: int) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    if chat.last_message:
        last = {
            "from": _person_dict(chat.last_message.from_),
            "preview": chat.last_message.preview,
            "created_at": _dt(chat.last_message.created_at),
            "message_id": chat.last_message.message_id,
        }
    return {
        "id": chat.id,
        "index": idx,
        "topic": chat.topic,
        "chat_type": chat.chat_type.value,
        "members": [_person_dict(m) for m in chat.members],
        "last_message": last,
        "has_unread": chat.has_unread,
        "unread_count": chat.unread_count,
        "is_muted": chat.is_muted,
        "last_updated": _dt(chat.last_updated),
    }


def _message_dict(msg: Message, idx: int) -> dict[str, Any]:
    return {
        "id": msg.id,
        "index": idx,
        "chat_id": msg.chat_id,
        "from": _person_dict(msg.from_),
        "created_at": _dt(msg.created_at),
        "body": msg.body,
        "body_format": msg.body_format,
        "importance": msg.importance,
        "reactions": [
            {
                "reaction_type": r.reaction_type,
                "user": _person_dict(r.user),
                "created_at": _dt(r.created_at),
            }
            for r in msg.reactions
        ],
        "is_deleted": msg.is_deleted,
        "reply_to_id": msg.reply_to_id,
    }


def dump_chat_list(
    console: Console,
    chats: list[Chat],
    indices: dict[int, str],
    next_skip: int | None,
) -> None:
    reverse = {v: k for k, v in indices.items()}
    items = [_chat_dict(c, reverse.get(c.id, 0)) for c in chats]
    payload = {"items": items, "next_skip": next_skip}
    console.file.write(json.dumps(payload, indent=2) + "\n")


def dump_messages(
    console: Console,
    messages: list[Message],
    chat_id: str,
    indices: dict[int, str],
    next_skip: int | None,
) -> None:
    reverse = {v: k for k, v in indices.items()}
    items = [_message_dict(m, reverse.get(m.id, 0)) for m in messages]
    payload = {"chat_id": chat_id, "items": items, "next_skip": next_skip}
    console.file.write(json.dumps(payload, indent=2) + "\n")
