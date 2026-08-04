"""Credentials model + JSON IO with atomic rotation and filelock."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from filelock import FileLock


@dataclass(frozen=True)
class Credentials:
    version: int
    acquired_at: str
    tenant_id: str
    client_id: str
    home_account_id: str
    username: str
    refresh_token: str
    shared_from: str | None
    id_token_claims: dict[str, Any] = field(default_factory=dict)

    @property
    def user_aad_id(self) -> str:
        """The `oid` claim — the user's AAD object ID (used in aadtokenauth path)."""
        oid = self.id_token_claims.get("oid")
        if oid:
            return str(oid)
        # Fallback: home_account_id is shaped `oid.tenant_id`.
        return self.home_account_id.split(".", 1)[0]


def _lock_for(path: Path) -> FileLock:
    return FileLock(str(path) + ".lock")


def load(path: Path) -> Credentials | None:
    if not path.exists():
        return None
    with _lock_for(path):
        raw = json.loads(path.read_text(encoding="utf-8"))
    return Credentials(
        version=int(raw["version"]),
        acquired_at=str(raw["acquired_at"]),
        tenant_id=str(raw["tenant_id"]),
        client_id=str(raw["client_id"]),
        home_account_id=str(raw["home_account_id"]),
        username=str(raw["username"]),
        refresh_token=str(raw["refresh_token"]),
        shared_from=raw.get("shared_from"),
        id_token_claims=dict(raw.get("id_token_claims") or {}),
    )


def save(creds: Credentials, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(creds)
    with _lock_for(path):
        tmp_fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
            os.replace(tmp_name, path)
            if not sys.platform.startswith("win"):
                os.chmod(path, 0o600)
        finally:
            if os.path.exists(tmp_name):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)


def update_refresh_token(path: Path, new_rt: str) -> None:
    """Atomically replace just the refresh_token field. Used by token-rotation."""
    current = load(path)
    if current is None:
        raise FileNotFoundError(path)
    if current.refresh_token == new_rt:
        return
    rotated = Credentials(
        version=current.version,
        acquired_at=current.acquired_at,
        tenant_id=current.tenant_id,
        client_id=current.client_id,
        home_account_id=current.home_account_id,
        username=current.username,
        refresh_token=new_rt,
        shared_from=current.shared_from,
        id_token_claims=current.id_token_claims,
    )
    save(rotated, path)
