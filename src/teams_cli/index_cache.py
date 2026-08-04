"""Per-family short-index caches: last_chat_listing.json, last_message_listing.json."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filelock import FileLock

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatListing:
    captured_at: str
    entries: dict[int, str] = field(default_factory=dict)  # index -> chat_id


@dataclass(frozen=True)
class MessageListing:
    captured_at: str
    chat_id: str
    entries: dict[int, str] = field(default_factory=dict)  # index -> message_id


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock"):
        tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent))
        try:
            json.dump(payload, tmp, indent=2, sort_keys=True)
            tmp_path = tmp.name
        finally:
            tmp.close()
        os.replace(tmp_path, path)


def _read(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("listing file corrupt: %s", path)
        return None
    if not isinstance(data, dict):
        log.warning("listing file has unexpected shape: %s", path)
        return None
    return data


def save_chat_listing(listing: ChatListing, path: Path) -> None:
    _write(
        path,
        {
            "captured_at": listing.captured_at,
            "entries": {str(k): v for k, v in listing.entries.items()},
        },
    )


def load_chat_listing(path: Path) -> ChatListing | None:
    raw = _read(path)
    if raw is None:
        return None
    entries_raw = raw.get("entries") or {}
    if not isinstance(entries_raw, dict):
        return None
    entries = {int(k): str(v) for k, v in entries_raw.items()}
    return ChatListing(captured_at=str(raw.get("captured_at", "")), entries=entries)


def save_message_listing(listing: MessageListing, path: Path) -> None:
    _write(
        path,
        {
            "captured_at": listing.captured_at,
            "chat_id": listing.chat_id,
            "entries": {str(k): v for k, v in listing.entries.items()},
        },
    )


def load_message_listing(path: Path) -> MessageListing | None:
    raw = _read(path)
    if raw is None:
        return None
    entries_raw = raw.get("entries") or {}
    if not isinstance(entries_raw, dict):
        return None
    entries = {int(k): str(v) for k, v in entries_raw.items()}
    return MessageListing(
        captured_at=str(raw.get("captured_at", "")),
        chat_id=str(raw.get("chat_id", "")),
        entries=entries,
    )
