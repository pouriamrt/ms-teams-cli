from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from teams_cli.cli import app

runner = CliRunner()


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TEAMS_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("OUTLOOK_CLI_HOME", str(tmp_path))  # no outlook-cli to share
    return tmp_path


def test_whoami_without_login_exits_77(isolated_home: Path) -> None:
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 77
    assert "Not logged in" in (result.output + result.stderr)


def test_whoami_json_after_seeded_credentials(isolated_home: Path) -> None:
    cdir = isolated_home / ".config" / "teams-cli"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "credentials.json").write_text(
        json.dumps(
            {
                "version": 1,
                "acquired_at": "2026-05-22T08:00:00Z",
                "tenant_id": "t",
                "client_id": "c",
                "home_account_id": "oid.t",
                "username": "u@example.com",
                "refresh_token": "rt",
                "shared_from": None,
                "id_token_claims": {"name": "U", "oid": "oid"},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["--json", "whoami"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed["username"] == "u@example.com"
    assert parsed["tenant_id"] == "t"
    assert parsed["user_aad_id"] == "oid"


def test_logout_removes_credentials(isolated_home: Path) -> None:
    cdir = isolated_home / ".config" / "teams-cli"
    cdir.mkdir(parents=True, exist_ok=True)
    cred_path = cdir / "credentials.json"
    cred_path.write_text("{}", encoding="utf-8")

    result = runner.invoke(app, ["logout"])
    assert result.exit_code == 0
    assert not cred_path.exists()


def test_login_prefers_outlook_share_when_available(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed outlook-cli credentials.
    odir = isolated_home / ".config" / "outlook-cli"
    odir.mkdir(parents=True, exist_ok=True)
    (odir / "credentials.json").write_text(
        json.dumps(
            {
                "version": 1,
                "tenant_id": "tid",
                "client_id": "cid",
                "home_account_id": "oid.tid",
                "username": "u@example.com",
                "refresh_token": "outlook-rt",
                "id_token_claims": {"oid": "oid"},
            }
        ),
        encoding="utf-8",
    )

    # Make the share call appear successful, and the bookmarklet path should NOT be invoked.
    with (
        patch("teams_cli.commands.auth.try_share") as mock_share,
        patch("teams_cli.commands.auth.capture_session") as mock_capture,
    ):
        from teams_cli.auth.outlook_share import OutlookShareResult

        mock_share.return_value = OutlookShareResult(
            ok=True,
            refresh_token="outlook-rt",
            tenant_id="tid",
            client_id="cid",
            username="u@example.com",
            home_account_id="oid.tid",
            id_token_claims={"oid": "oid"},
        )
        result = runner.invoke(app, ["login", "--reuse-outlook-cli"])

    assert result.exit_code == 0, result.output
    mock_capture.assert_not_called()
    written = json.loads(
        (isolated_home / ".config" / "teams-cli" / "credentials.json").read_text("utf-8")
    )
    assert written["shared_from"] == "outlook-cli"
    assert written["refresh_token"] == "outlook-rt"
