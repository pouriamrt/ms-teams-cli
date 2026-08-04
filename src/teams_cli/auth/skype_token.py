"""Mint and cache a Skype token by trading the AAD Teams AT against authsvc.

Endpoint (current):
    POST https://teams.microsoft.com/api/authsvc/v1.0/authz
    Authorization: Bearer <aad-teams-access-token>
    Origin: https://teams.microsoft.com
    (no body)

Response: {"tokens": {"skypeToken": "<jwt>", "expiresIn": <seconds>}, "region": "<tag>", ...}

Note: the older `https://{region}-prod.asyncgw.teams.microsoft.com/v1/{userAadId}/aadtokenauth`
endpoint was deprecated by Microsoft (returns 400 with empty body as of 2026-05).
Teams web now mints Skype tokens via the new authsvc URL above.

The Skype token is sent as a non-Bearer header on chatsvc calls:
    Authentication: skypetoken=<jwt>
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from filelock import FileLock

from teams_cli.config import http_verify
from teams_cli.errors import ApiError

log = logging.getLogger(__name__)

_AUTHZ_URL = "https://teams.microsoft.com/api/authsvc/v1.0/authz"
_TEAMS_ORIGIN = "https://teams.microsoft.com"
_SKEW_SECONDS = 300  # 5-minute skew; Skype tokens are ~24h so we have plenty of headroom


class SkypeTokenMinter:
    def __init__(self, cache_path: Path, client: httpx.Client | None = None) -> None:
        self._cache_path = cache_path
        self._client = client or httpx.Client(timeout=30.0, verify=http_verify())

    def get_skype_token(self, user_aad_id: str, aad_teams_token: str) -> str:
        """Mint a Skype token from an AAD Teams access token.

        ``user_aad_id`` is unused on the current authsvc endpoint (the user is
        identified by the Bearer token alone). Kept in the signature for
        backwards compatibility with callers that pass it positionally.
        """
        del user_aad_id  # no longer in URL; identity comes from Bearer
        cached = self._cache_get()
        if cached is not None:
            log.debug("skype-token cache hit")
            return cached

        log.info("minting skype token via authsvc")
        resp = self._client.post(
            _AUTHZ_URL,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {aad_teams_token}",
                "Origin": _TEAMS_ORIGIN,
            },
        )
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except json.JSONDecodeError:
                detail = resp.text or "<empty body>"
            raise ApiError(
                f"authsvc/authz returned {resp.status_code}: {detail}",
                status_code=resp.status_code,
            )

        body = resp.json()
        tok = str(body["tokens"]["skypeToken"])
        expires_in = int(body["tokens"].get("expiresIn", 86400))
        expires_at = datetime.now(UTC) + timedelta(seconds=max(expires_in - _SKEW_SECONDS, 60))
        self._cache_put(tok, expires_at)
        return tok

    def invalidate(self) -> None:
        if self._cache_path.exists():
            try:
                self._cache_path.unlink()
            except OSError:
                log.warning("could not delete skype token cache at %s", self._cache_path)

    # ----- cache IO -----

    def _cache_get(self) -> str | None:
        if not self._cache_path.exists():
            return None
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            expires_at = datetime.fromisoformat(data["expires_at"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            return None
        return str(data["skype_token"])

    def _cache_put(self, token: str, expires_at: datetime) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self._cache_path) + ".lock"):
            tmp = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", delete=False, dir=str(self._cache_path.parent)
            )
            try:
                json.dump(
                    {"skype_token": token, "expires_at": expires_at.isoformat()},
                    tmp,
                    indent=2,
                )
                tmp_path = tmp.name
            finally:
                tmp.close()
            os.replace(tmp_path, self._cache_path)
