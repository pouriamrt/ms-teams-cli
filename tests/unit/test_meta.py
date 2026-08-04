from __future__ import annotations

import json

import pytest
import typer
from typer.testing import CliRunner

from teams_cli.cli import app
from teams_cli.commands.meta import json_schema_cmd

runner = CliRunner()


def test_version_prints_semver() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout
    assert "Graph v1.0" in result.stdout
    assert "chatsvc" in result.stdout


def test_version_json() -> None:
    result = runner.invoke(app, ["--json", "version"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["cli_version"] == "0.1.0"
    assert parsed["graph_api"] == "v1.0"


def test_json_schema_chat_list_returns_valid_jsonschema() -> None:
    result = runner.invoke(app, ["--json-schema", "chat.list"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["type"] == "object"
    assert "items" in parsed["properties"]


def test_json_schema_unknown_exits_2() -> None:
    result = runner.invoke(app, ["--json-schema", "nope"])
    assert result.exit_code == 2
    assert "Unknown schema" in (result.stderr + result.stdout)


def test_json_schema_no_arg_lists_names() -> None:
    result = runner.invoke(app, ["--json-schema", "--list"])
    assert result.exit_code == 0
    for name in (
        "chat.list",
        "chat.read",
        "chat.send",
        "chat.reply",
        "chat.react",
        "chat.search",
    ):
        assert name in result.stdout


def test_json_schema_cmd_none_without_list_exits_2() -> None:
    """Contract: calling json_schema_cmd with no name and no --list exits 2."""
    with pytest.raises(typer.Exit) as exc_info:
        json_schema_cmd(name=None, list_names=False)
    assert exc_info.value.exit_code == 2
