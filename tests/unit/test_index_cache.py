from __future__ import annotations

from pathlib import Path

from teams_cli.index_cache import (
    ChatListing,
    MessageListing,
    load_chat_listing,
    load_message_listing,
    save_chat_listing,
    save_message_listing,
)


def test_chat_listing_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "last_chat_listing.json"
    listing = ChatListing(
        captured_at="2026-05-22T18:00:00Z",
        entries={1: "19:a@unq.gbl.spaces", 2: "19:b@thread.v2"},
    )
    save_chat_listing(listing, path)
    loaded = load_chat_listing(path)
    assert loaded is not None
    assert loaded.entries == {1: "19:a@unq.gbl.spaces", 2: "19:b@thread.v2"}
    assert loaded.captured_at == "2026-05-22T18:00:00Z"


def test_message_listing_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "last_message_listing.json"
    listing = MessageListing(
        captured_at="2026-05-22T18:00:00Z",
        chat_id="19:a@unq.gbl.spaces",
        entries={1: "msg-1", 2: "msg-2", 3: "msg-3"},
    )
    save_message_listing(listing, path)
    loaded = load_message_listing(path)
    assert loaded is not None
    assert loaded.chat_id == "19:a@unq.gbl.spaces"
    assert loaded.entries[2] == "msg-2"


def test_missing_listing_returns_none(tmp_path: Path) -> None:
    assert load_chat_listing(tmp_path / "nope.json") is None
    assert load_message_listing(tmp_path / "nope2.json") is None


def test_corrupt_file_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json")
    assert load_chat_listing(p) is None
    assert load_message_listing(p) is None
