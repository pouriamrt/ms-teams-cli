"""AAD access-token minting from a refresh token, with per-scope cache + RT rotation."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from filelock import FileLock

from teams_cli.auth.token_store import load, update_refresh_token
from teams_cli.config import http_verify, teams_origin
from teams_cli.errors import ApiError, SessionExpired

log = logging.getLogger(__name__)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
TEAMS_SCOPE = "https://teams.microsoft.com/.default"
# IC3 is the Teams chat/calling backend. chatsvc property writes (e.g. the
# consumptionHorizonBookmark read-cursor) authenticate against this audience,
# NOT https://teams.microsoft.com. Verified from a captured Teams-web request.
IC3_SCOPE = "https://ic3.teams.office.com/.default"

_SKEW_SECONDS = 60  # subtract from `expires_in` so we refresh just before the wire expiry


def _token_endpoint(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


@dataclass(frozen=True)
class _CachedToken:
    access_token: str
    expires_at: datetime  # UTC


class TokenRefresher:
    """Mint and cache AAD access tokens, rotating the refresh token when Microsoft rotates it."""

    def __init__(
        self,
        creds_path: Path,
        cache_path: Path,
        client: httpx.Client | None = None,
    ) -> None:
        self._creds_path = creds_path
        self._cache_path = cache_path
        self._client = client or httpx.Client(timeout=30.0, verify=http_verify())

    def get_token(self, scope: str) -> str:
        cached = self._cache_get(scope)
        if cached is not None:
            log.debug("token cache hit scope=%s", scope)
            return cached.access_token

        creds = load(self._creds_path)
        if creds is None:
            raise SessionExpired("Not logged in. Run 'teams login' to authenticate.")

        log.info("minting AAD AT scope=%s tenant=%s", scope, creds.tenant_id)
        resp = self._client.post(
            _token_endpoint(creds.tenant_id),
            data={
                "client_id": creds.client_id,
                "grant_type": "refresh_token",
                "refresh_token": creds.refresh_token,
                "scope": scope,
            },
            headers={
                "Accept": "application/json",
                # SPA-issued refresh tokens (AADSTS9002327) require an Origin header
                # matching a registered SPA redirect URI. teams.microsoft.com is where
                # the MSAL.js bookmarklet ran when the token was captured.
                "Origin": teams_origin(),
            },
        )
        body: dict[str, Any] = resp.json() if resp.content else {}
        if resp.status_code != 200:
            err = str(body.get("error", "")).lower()
            if err in {"invalid_grant", "interaction_required", "consent_required"}:
                raise SessionExpired(
                    "Session expired. Run 'teams login' to re-authenticate."
                ) from None
            raise ApiError(
                f"Token endpoint returned {resp.status_code}: "
                f"{body.get('error_description') or resp.text}",
                status_code=resp.status_code,
            )

        access_token = str(body["access_token"])
        expires_in = int(body.get("expires_in", 3600))
        expires_at = datetime.now(UTC) + timedelta(seconds=max(expires_in - _SKEW_SECONDS, 60))

        rotated = body.get("refresh_token")
        if rotated:
            log.info("refresh_token rotated; persisting new RT")
            update_refresh_token(self._creds_path, str(rotated))

        self._cache_put(scope, _CachedToken(access_token=access_token, expires_at=expires_at))
        return access_token

    # ----- cache IO -----

    def _cache_read(self) -> dict[str, Any]:
        if not self._cache_path.exists():
            return {}
        try:
            data: dict[str, Any] = json.loads(self._cache_path.read_text(encoding="utf-8"))
            return data
        except json.JSONDecodeError:
            log.warning("access_tokens.json corrupt; treating as empty cache")
            return {}

    def _cache_write(self, data: dict[str, Any]) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self._cache_path) + ".lock"):
            tmp = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", delete=False, dir=str(self._cache_path.parent)
            )
            try:
                json.dump(data, tmp, indent=2, sort_keys=True)
                tmp_path = tmp.name
            finally:
                tmp.close()
            os.replace(tmp_path, self._cache_path)

    def _cache_get(self, scope: str) -> _CachedToken | None:
        data = self._cache_read()
        entry = data.get(scope)
        if not entry:
            return None
        try:
            expires_at = datetime.fromisoformat(entry["expires_at"])
        except (KeyError, ValueError):
            return None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            return None
        return _CachedToken(access_token=str(entry["access_token"]), expires_at=expires_at)

    def _cache_put(self, scope: str, token: _CachedToken) -> None:
        data = self._cache_read()
        data[scope] = {
            "access_token": token.access_token,
            "expires_at": token.expires_at.isoformat(),
        }
        self._cache_write(data)
