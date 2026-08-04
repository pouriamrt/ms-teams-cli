"""$EDITOR-backed body composition for chat send/reply."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from teams_cli.errors import UserError

TEMPLATE = "\n# Type your message above. Lines starting with '#' are ignored.\n"


def _editor_argv() -> list[str]:
    """Split $EDITOR into an argv, tolerating `code -w` and quoted Windows paths."""
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "vi")
    # ponytail: posix=False keeps backslash paths intact on Windows; strip the
    # quotes it leaves behind, since subprocess re-quotes each argv element.
    return [arg.strip('"') for arg in shlex.split(editor, posix=os.name != "nt")]


def _open_editor(template: str) -> str | None:
    """Open $EDITOR on a temp file. Return its contents, or None if never saved."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(template)
        path = Path(handle.name)
    try:
        before = path.stat().st_mtime
        subprocess.run([*_editor_argv(), str(path)], check=True)
        if path.stat().st_mtime == before:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise UserError(f"Could not open $EDITOR: {exc}") from exc
    finally:
        path.unlink(missing_ok=True)


def compose_body(inline_body: str | None, stdin_data: str | None) -> str:
    """Return the final message body, opening $EDITOR if no inline body was given.

    - `--body "text"`            → inline_body="text"
    - `--body @-` (or `--body=-`) → read from stdin
    - (nothing)                   → open $EDITOR
    """
    if inline_body == "@-" or inline_body == "-":
        if stdin_data is None:
            raise UserError("--body @- was given but stdin is empty.")
        text = stdin_data
    elif inline_body is not None:
        text = inline_body
    else:
        edited = _open_editor(TEMPLATE)
        if edited is None:
            raise UserError("Editor exited without saving; message not sent.")
        text = edited

    cleaned = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    ).strip()
    if not cleaned:
        raise UserError("Body is empty; message not sent.")
    return cleaned
