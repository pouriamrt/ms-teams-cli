from __future__ import annotations

from pathlib import Path

import pytest

from teams_cli.errors import NotFound
from teams_cli.index_cache import (
    ChatListing,
    MessageListing,
    save_chat_listing,
    save_message_listing,
)
from teams_cli.resolve import (
    is_chat_id,
    is_email,
    is_index,
    resolve_chat,
    resolve_message,
)


def test_is_chat_id_recognizes_known_shapes() -> None:
    assert is_chat_id("19:abc_def@unq.gbl.spaces") is True
    assert is_chat_id("19:xxxx@thread.v2") is True
    assert is_chat_id("19:meeting_xxx@thread.v2") is True
    assert is_chat_id("19:xxx@thread.tacv2") is True  # channel; v2 scope
    assert is_chat_id("alice@example.com") is False
    assert is_chat_id("3") is False
    assert is_chat_id("") is False


def test_is_email() -> None:
    assert is_email("alice@example.com") is True
    assert is_email("alice+tag@example.com") is True
    assert is_email("3") is False
    assert is_email("19:x@unq.gbl.spaces") is False


def test_is_index() -> None:
    assert is_index("3") is True
    assert is_index("123") is True
    assert is_index("0") is False  # 1-based
    assert is_index("-1") is False
    assert is_index("abc") is False


def test_resolve_chat_with_chat_id_passthrough(tmp_path: Path) -> None:
    cid = "19:abc@unq.gbl.spaces"
    out = resolve_chat(cid, chat_listing_path=tmp_path / "x.json")
    assert out == cid


def test_resolve_chat_with_index(tmp_path: Path) -> None:
    listing_path = tmp_path / "last_chat_listing.json"
    save_chat_listing(
        ChatListing(
            captured_at="t",
            entries={1: "19:a@unq.gbl.spaces", 2: "19:b@thread.v2"},
        ),
        listing_path,
    )
    assert resolve_chat("2", chat_listing_path=listing_path) == "19:b@thread.v2"


def test_resolve_chat_index_out_of_range_raises(tmp_path: Path) -> None:
    listing_path = tmp_path / "last_chat_listing.json"
    save_chat_listing(
        ChatListing(captured_at="t", entries={1: "19:a@unq.gbl.spaces"}),
        listing_path,
    )
    with pytest.raises(NotFound):
        resolve_chat("3", chat_listing_path=listing_path)


def test_resolve_chat_index_no_listing_raises(tmp_path: Path) -> None:
    with pytest.raises(NotFound):
        resolve_chat("1", chat_listing_path=tmp_path / "nope.json")


def test_resolve_message_index(tmp_path: Path) -> None:
    listing_path = tmp_path / "last_message_listing.json"
    save_message_listing(
        MessageListing(
            captured_at="t",
            chat_id="19:a@unq.gbl.spaces",
            entries={1: "m1", 2: "m2"},
        ),
        listing_path,
    )
    chat, msg = resolve_message("2", message_listing_path=listing_path)
    assert chat == "19:a@unq.gbl.spaces"
    assert msg == "m2"
