from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from teams_cli.auth.token_store import Credentials, load, save


def _sample(tmp_path: Path, fixtures_dir: Path) -> Path:
    src = fixtures_dir / "credentials_sample.json"
    dst = tmp_path / "credentials.json"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def test_load_parses_full_credentials(tmp_path: Path, fixtures_dir: Path) -> None:
    path = _sample(tmp_path, fixtures_dir)
    creds = load(path)
    assert creds is not None
    assert creds.username == "pouriamortezaagha7@gmail.com"
    assert creds.tenant_id == "11111111-2222-3333-4444-555555555555"
    assert creds.user_aad_id == "99999999-8888-7777-6666-555555555555"
    assert creds.refresh_token.startswith("1.AXYA")
    assert creds.shared_from is None


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load(tmp_path / "does_not_exist.json") is None


def test_save_round_trips(tmp_path: Path) -> None:
    creds = Credentials(
        version=1,
        acquired_at="2026-05-22T08:00:00Z",
        tenant_id="tid",
        client_id="cid",
        home_account_id="oid.tid",
        username="x@example.com",
        refresh_token="rt1",
        shared_from=None,
        id_token_claims={"name": "X"},
    )
    path = tmp_path / "credentials.json"
    save(creds, path)
    loaded = load(path)
    assert loaded == creds


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX permission semantics")
def test_save_writes_mode_0600_on_posix(tmp_path: Path) -> None:
    creds = Credentials(
        version=1,
        acquired_at="now",
        tenant_id="t",
        client_id="c",
        home_account_id="o.t",
        username="u",
        refresh_token="r",
        shared_from=None,
        id_token_claims={},
    )
    path = tmp_path / "credentials.json"
    save(creds, path)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_save_replaces_atomically(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text("ORIGINAL", encoding="utf-8")
    creds = Credentials(
        version=1,
        acquired_at="now",
        tenant_id="t",
        client_id="c",
        home_account_id="o.t",
        username="u",
        refresh_token="r2",
        shared_from=None,
        id_token_claims={},
    )
    save(creds, path)
    assert json.loads(path.read_text(encoding="utf-8"))["refresh_token"] == "r2"


def test_update_refresh_token_atomic(tmp_path: Path, fixtures_dir: Path) -> None:
    from teams_cli.auth.token_store import update_refresh_token

    path = _sample(tmp_path, fixtures_dir)
    update_refresh_token(path, "1.AXYA-ROTATED")
    after = load(path)
    assert after is not None
    assert after.refresh_token == "1.AXYA-ROTATED"


def test_auth_facade_exports() -> None:
    import teams_cli.auth as auth

    names = {
        "Credentials",
        "GRAPH_SCOPE",
        "OutlookShareResult",
        "ParsedSession",
        "SkypeTokenMinter",
        "TEAMS_SCOPE",
        "TokenRefresher",
        "capture_session",
        "detect_outlook_credentials",
        "load",
        "parse_localstorage",
        "persist_session",
        "save",
        "try_share",
        "update_refresh_token",
    }
    assert names <= set(dir(auth))
