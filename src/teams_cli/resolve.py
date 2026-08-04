"""Resolve user-facing identifiers (index | email | chat-id) to canonical IDs."""

from __future__ import annotations

import re
from pathlib import Path

from teams_cli.errors import NotFound
from teams_cli.index_cache import load_chat_listing, load_message_listing

_CHAT_ID_RE = re.compile(r"^19:[A-Za-z0-9_\-]+@(unq\.gbl\.spaces|thread\.v2|thread\.tacv2)$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_INDEX_RE = re.compile(r"^[1-9][0-9]*$")


def is_chat_id(value: str) -> bool:
    return bool(_CHAT_ID_RE.match(value))


def is_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value))


def is_index(value: str) -> bool:
    return bool(_INDEX_RE.match(value))


def resolve_chat(value: str, *, chat_listing_path: Path) -> str:
    """Resolve a chat reference to a canonical chat_id.

    ``value`` may be: an index from the last ``chat list``, a raw chat-id, or an
    email. Emails are rejected here so callers explicitly handle the create-chat
    / send-by-email path via ``api.people.resolve``.
    """
    if is_chat_id(value):
        return value
    if is_index(value):
        listing = load_chat_listing(chat_listing_path)
        if listing is None:
            raise NotFound(
                "No chat listing found. Run 'teams chat list' first to populate the index cache."
            )
        idx = int(value)
        if idx not in listing.entries:
            raise NotFound(
                f"No chat with index {idx} in last listing (had {len(listing.entries)})."
            )
        return listing.entries[idx]
    if is_email(value):
        raise NotFound(
            f"'{value}' looks like an email - for send/read use the dedicated send-by-email path."
        )
    raise NotFound(f"Could not interpret '{value}' as an index, chat-id, or email.")


def resolve_message(value: str, *, message_listing_path: Path) -> tuple[str, str]:
    """Resolve a message reference to ``(chat_id, message_id)``."""
    if is_index(value):
        listing = load_message_listing(message_listing_path)
        if listing is None:
            raise NotFound("No message listing found. Run 'teams chat read <chat>' first.")
        idx = int(value)
        if idx not in listing.entries:
            raise NotFound(
                f"No message with index {idx} in last read (had {len(listing.entries)})."
            )
        return listing.chat_id, listing.entries[idx]
    raise NotFound(f"'{value}' is not a recognized message index.")
