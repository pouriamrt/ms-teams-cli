"""JSON Schemas for --json output, queryable via `teams --json-schema <name>`."""

from __future__ import annotations

from typing import Any

_PERSON: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "email": {"type": "string"},
        "user_id": {"type": "string"},
        "from_me": {"type": "boolean"},
    },
    "required": ["name", "email"],
}

_MESSAGE_PREVIEW: dict[str, Any] = {
    "type": "object",
    "properties": {
        "from": _PERSON,
        "preview": {"type": "string"},
        "created_at": {"type": "string", "format": "date-time"},
        "message_id": {"type": "string"},
    },
    "required": ["from", "preview", "created_at", "message_id"],
}

_CHAT_LIST_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "index": {"type": "integer", "minimum": 1},
        "topic": {"type": ["string", "null"]},
        "chat_type": {"enum": ["oneOnOne", "group", "meeting"]},
        "members": {"type": "array", "items": _PERSON},
        "last_message": {"oneOf": [_MESSAGE_PREVIEW, {"type": "null"}]},
        "has_unread": {"type": "boolean"},
        "unread_count": {"type": ["integer", "null"], "minimum": 0},
        "is_muted": {"type": "boolean"},
        "last_updated": {"type": "string", "format": "date-time"},
    },
    "required": ["id", "index", "chat_type", "members", "has_unread", "is_muted", "last_updated"],
}

_MESSAGE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "index": {"type": "integer", "minimum": 1},
        "chat_id": {"type": "string"},
        "from": _PERSON,
        "created_at": {"type": "string", "format": "date-time"},
        "body": {"type": "string"},
        "body_format": {"enum": ["text", "html", "markdown"]},
        "importance": {"enum": ["normal", "high", "urgent"]},
        "reactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "reaction_type": {"type": "string"},
                    "user": _PERSON,
                    "created_at": {"type": "string", "format": "date-time"},
                },
                "required": ["reaction_type", "user", "created_at"],
            },
        },
        "is_deleted": {"type": "boolean"},
        "reply_to_id": {"type": ["string", "null"]},
    },
    "required": ["id", "index", "chat_id", "from", "created_at", "body", "body_format"],
}

_SCHEMAS: dict[str, dict[str, Any]] = {
    "chat.list": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": _CHAT_LIST_ITEM},
            "next_skip": {"type": ["integer", "null"], "minimum": 0},
        },
        "required": ["items"],
    },
    "chat.read": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "chat_id": {"type": "string"},
            "items": {"type": "array", "items": _MESSAGE},
            "next_skip": {"type": ["integer", "null"], "minimum": 0},
        },
        "required": ["chat_id", "items"],
    },
    "chat.send": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "chat_id": {"type": "string"},
            "message_id": {"type": "string"},
            "created_at": {"type": "string", "format": "date-time"},
        },
        "required": ["chat_id", "message_id", "created_at"],
    },
    "chat.reply": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "chat_id": {"type": "string"},
            "message_id": {"type": "string"},
            "reply_to_id": {"type": "string"},
            "created_at": {"type": "string", "format": "date-time"},
        },
        "required": ["chat_id", "message_id", "reply_to_id", "created_at"],
    },
    "chat.react": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "reaction": {"type": "string"},
            "via": {"enum": ["graph", "chatsvc"]},
            "unreact": {"type": "boolean"},
        },
        "required": ["ok", "reaction", "via", "unreact"],
    },
    "chat.search": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string"},
                        "chat_id": {"type": "string"},
                        "chat_index": {"type": ["integer", "null"]},
                        "from": _PERSON,
                        "preview": {"type": "string"},
                        "created_at": {"type": "string", "format": "date-time"},
                        "score": {"type": ["number", "null"]},
                    },
                    "required": ["message_id", "chat_id", "from", "preview", "created_at"],
                },
            },
            "total_estimated": {"type": ["integer", "null"]},
        },
        "required": ["items"],
    },
}


def get_schema(name: str) -> dict[str, Any]:
    if name not in _SCHEMAS:
        raise KeyError(name)
    return _SCHEMAS[name]


def list_schema_names() -> list[str]:
    return sorted(_SCHEMAS.keys())
