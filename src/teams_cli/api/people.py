"""Resolve email -> AAD user via Graph /users with a 24h LRU cache (cap 256)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from filelock import FileLock

from teams_cli.api.client import ApiClient
from teams_cli.api.models import Person
from teams_cli.errors import ApiError, NotFound

log = logging.getLogger(__name__)

_TTL = timedelta(hours=24)
_MAX_ENTRIES = 256


class PeopleResolver:
    def __init__(self, client: ApiClient, cache_path: Path) -> None:
        self._c = client
        self._cache_path = cache_path

    def resolve(self, email: str) -> Person:
        cached = self._cache_get(email)
        if cached is not None:
            return cached

        resp = self._c.graph_get(
            f"/users/{email}",
            params={"$select": "id,displayName,mail,userPrincipalName"},
        )
        if resp.status_code == 404:
            raise NotFound(f"User {email} not found in directory.")
        if resp.status_code >= 400:
            raise ApiError(
                f"Graph /users returned {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
            )
        body = resp.json()
        person = Person(
            user_id=str(body.get("id") or ""),
            name=str(body.get("displayName") or email),
            email=str(body.get("mail") or body.get("userPrincipalName") or email),
            from_me=False,
        )
        self._cache_put(email, person)
        return person

    # ----- cache -----

    def _read_cache(self) -> dict[str, dict[str, Any]]:
        if not self._cache_path.exists():
            return {}
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        entries = data.get("entries") or {}
        if not isinstance(entries, dict):
            return {}
        return {str(k): dict(v) for k, v in entries.items() if isinstance(v, dict)}

    def _write_cache(self, entries: dict[str, dict[str, Any]]) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self._cache_path) + ".lock"):
            tmp = tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                dir=str(self._cache_path.parent),
            )
            try:
                json.dump({"entries": entries}, tmp, indent=2, sort_keys=True)
                tmp_path = tmp.name
            finally:
                tmp.close()
            os.replace(tmp_path, self._cache_path)

    def _cache_get(self, email: str) -> Person | None:
        entries = self._read_cache()
        entry = entries.get(email.lower())
        if not entry:
            return None
        try:
            expires_at = datetime.fromisoformat(str(entry["expires_at"]))
        except (KeyError, ValueError):
            return None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            return None
        return Person(
            user_id=str(entry.get("user_id") or ""),
            name=str(entry.get("name") or ""),
            email=str(entry.get("email") or ""),
            from_me=False,
        )

    def _cache_put(self, email: str, person: Person) -> None:
        entries = self._read_cache()
        key = email.lower()
        # Drop oldest (lex-first) to keep size bounded if at cap and new key.
        if len(entries) >= _MAX_ENTRIES and key not in entries:
            try:
                first = next(iter(sorted(entries.keys())))
                entries.pop(first, None)
            except StopIteration:
                pass
        entries[key] = {
            "user_id": person.user_id,
            "name": person.name,
            "email": person.email,
            "expires_at": (datetime.now(UTC) + _TTL).isoformat(),
        }
        self._write_cache(entries)
