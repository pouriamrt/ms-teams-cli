import pytest

from teams_cli.schemas import get_schema, list_schema_names


def test_chat_list_schema_top_level_keys() -> None:
    schema = get_schema("chat.list")
    assert schema["type"] == "object"
    props = schema["properties"]
    assert "items" in props
    assert "next_skip" in props


def test_chat_send_schema_has_required_message_id() -> None:
    schema = get_schema("chat.send")
    assert schema["required"] == ["chat_id", "message_id", "created_at"]


def test_unknown_schema_raises() -> None:
    with pytest.raises(KeyError):
        get_schema("nope")


def test_list_schema_names_contains_chat_commands() -> None:
    names = list_schema_names()
    assert {
        "chat.list",
        "chat.read",
        "chat.send",
        "chat.reply",
        "chat.react",
        "chat.search",
    } <= set(names)
