"""Pydantic models for Chat, Message, Reaction, Person + Graph adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatType(str, Enum):
    ONE_ON_ONE = "oneOnOne"
    GROUP = "group"
    MEETING = "meeting"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    s = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class Person(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str = ""
    name: str
    email: str = ""
    from_me: bool = False

    @classmethod
    def from_graph_user(
        cls,
        user_node: dict[str, Any] | None,
        my_user_id: str,
        email: str = "",
    ) -> Person:
        if not user_node:
            return cls(user_id="", name="(unknown)", email=email, from_me=False)
        uid = str(user_node.get("id") or "")
        return cls(
            user_id=uid,
            name=str(user_node.get("displayName") or "(unknown)"),
            email=email,
            from_me=bool(uid) and uid == my_user_id,
        )


class Reaction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reaction_type: str
    user: Person
    created_at: datetime


class MessagePreview(BaseModel):
    from_: Person = Field(..., alias="from")
    preview: str
    created_at: datetime
    message_id: str

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Message(BaseModel):
    id: str
    chat_id: str
    from_: Person = Field(..., alias="from")
    created_at: datetime
    body: str
    body_format: str  # "text" | "html"
    importance: str = "normal"
    reactions: list[Reaction] = Field(default_factory=list)
    is_deleted: bool = False
    reply_to_id: str | None = None

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Chat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    topic: str | None = None
    chat_type: ChatType
    members: list[Person]
    last_message: MessagePreview | None = None
    has_unread: bool = False
    unread_count: int | None = None
    is_muted: bool = False
    last_updated: datetime


# ----- Graph adapters -----


def _member_to_person(member: dict[str, Any], my_user_id: str) -> Person:
    return Person(
        user_id=str(member.get("userId") or ""),
        name=str(member.get("displayName") or "(unknown)"),
        email=str(member.get("email") or ""),
        from_me=str(member.get("userId") or "") == my_user_id,
    )


def chat_from_graph(node: dict[str, Any], my_user_id: str) -> Chat:
    try:
        chat_type = ChatType(node.get("chatType", "group"))
    except ValueError:
        chat_type = ChatType.GROUP

    members = [_member_to_person(m, my_user_id) for m in (node.get("members") or [])]
    last_msg = node.get("lastMessagePreview")
    last_preview: MessagePreview | None = None
    if last_msg:
        from_node = (last_msg.get("from") or {}).get("user")
        body = last_msg.get("body") or {}
        last_preview = MessagePreview.model_validate(
            {
                "from": Person.from_graph_user(from_node, my_user_id),
                "preview": str(body.get("content") or ""),
                "created_at": _parse_dt(last_msg.get("createdDateTime")) or datetime.now(UTC),
                "message_id": str(last_msg.get("id") or ""),
            }
        )

    viewpoint = node.get("viewpoint") or {}
    last_read = _parse_dt(viewpoint.get("lastMessageReadDateTime"))
    last_msg_time = last_preview.created_at if last_preview else None
    has_unread = bool(last_msg_time and (last_read is None or last_read < last_msg_time))
    # Don't count messages from me as unread.
    if last_preview and last_preview.from_.from_me:
        has_unread = False

    last_updated = _parse_dt(node.get("lastUpdatedDateTime")) or last_msg_time or datetime.now(UTC)

    return Chat(
        id=str(node["id"]),
        topic=node.get("topic"),
        chat_type=chat_type,
        members=members,
        last_message=last_preview,
        has_unread=has_unread,
        unread_count=None,
        is_muted=bool(viewpoint.get("isHidden") or False),
        last_updated=last_updated,
    )


def message_from_graph(node: dict[str, Any], my_user_id: str) -> Message:
    from_user = (node.get("from") or {}).get("user")
    body = node.get("body") or {}
    reactions: list[Reaction] = []
    for r in node.get("reactions") or []:
        r_user_node = (r.get("user") or {}).get("user")
        reactions.append(
            Reaction(
                reaction_type=str(r.get("reactionType") or ""),
                user=Person.from_graph_user(r_user_node, my_user_id),
                created_at=_parse_dt(r.get("createdDateTime")) or datetime.now(UTC),
            )
        )
    return Message.model_validate(
        {
            "id": str(node["id"]),
            "chat_id": str(node.get("chatId") or ""),
            "from": Person.from_graph_user(from_user, my_user_id),
            "created_at": _parse_dt(node.get("createdDateTime")) or datetime.now(UTC),
            "body": str(body.get("content") or ""),
            "body_format": str(body.get("contentType") or "text"),
            "importance": str(node.get("importance") or "normal"),
            "reactions": reactions,
            "is_deleted": bool(node.get("deletedDateTime")),
            "reply_to_id": node.get("replyToId"),
        }
    )
