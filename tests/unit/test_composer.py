from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from teams_cli.commands.composer import _open_editor, compose_body
from teams_cli.errors import UserError


def test_compose_with_inline_body_returns_as_is() -> None:
    assert compose_body(inline_body="hello", stdin_data=None) == "hello"


def test_compose_reads_stdin_when_inline_is_dash() -> None:
    assert compose_body(inline_body="@-", stdin_data="from stdin") == "from stdin"


def test_compose_opens_editor_when_no_body_given() -> None:
    with patch("teams_cli.commands.composer._open_editor", return_value="edited content"):
        assert compose_body(inline_body=None, stdin_data=None) == "edited content"


def test_compose_empty_after_edit_raises() -> None:
    with (
        patch("teams_cli.commands.composer._open_editor", return_value=""),
        pytest.raises(UserError) as ei,
    ):
        compose_body(inline_body=None, stdin_data=None)
    assert "empty" in str(ei.value).lower()


def test_compose_editor_returned_none_raises() -> None:
    with (
        patch("teams_cli.commands.composer._open_editor", return_value=None),
        pytest.raises(UserError),
    ):
        compose_body(inline_body=None, stdin_data=None)


def test_compose_inline_dash_no_stdin_raises() -> None:
    with pytest.raises(UserError):
        compose_body(inline_body="@-", stdin_data=None)


def _fake_editor(tmp_path: Path, body: str) -> str:
    """An $EDITOR that overwrites the file it is handed with `body`."""
    script = tmp_path / "fake_editor.py"
    script.write_text(
        "import pathlib, sys, time\n"
        "time.sleep(0.01)\n"
        f"pathlib.Path(sys.argv[1]).write_text({body!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


def test_open_editor_returns_saved_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDITOR", _fake_editor(tmp_path, "hello from the editor\n"))
    assert _open_editor("# template\n") == "hello from the editor\n"


def test_open_editor_returns_none_when_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    noop = tmp_path / "noop.py"
    noop.write_text("import time; time.sleep(0.01)\n", encoding="utf-8")
    monkeypatch.setenv("EDITOR", f'"{sys.executable}" "{noop}"')
    assert _open_editor("# template\n") is None


def test_open_editor_raises_when_editor_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDITOR", "definitely-not-an-editor-binary")
    with pytest.raises(UserError):
        _open_editor("# template\n")
