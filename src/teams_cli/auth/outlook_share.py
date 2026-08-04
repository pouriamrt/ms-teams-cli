"""Detect outlook-cli credentials and FOCI-mint a Teams AT from them.

This is a one-shot best-effort path used before the bookmarklet flow. Always falls
through silently to the bookmarklet flow on any failure — we do NOT propagate errors.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from teams_cli.config import http_verify

log = logging.getLogger(__name__)


def _outlook_cli_home() -> Path:
    override = os.environ.get("OUTLOOK_CLI_HOME")
    return Path(override) if override else Path.home()


def detect_outlook_credentials() -> Path | None:
    """Return the path to outlook-cli's credentials.json if present, else None."""
    candidate = _outlook_cli_home() / ".config" / "outlook-cli" / "credentials.json"
    return candidate if candidate.exists() else None


@dataclass(frozen=True)
class OutlookShareResult:
    ok: bool
    refresh_token: str | None = None
    tenant_id: str | None = None
    client_id: str | None = None
    username: str | None = None
    home_account_id: str | None = None
    id_token_claims: dict[str, Any] | None = None
    reason: str | None = None


def try_share(creds_path: Path, client: httpx.Client | None = None) -> OutlookShareResult:
    """Attempt to mint a Teams-scope AAD AT from outlook-cli's RT.

    Returns OutlookShareResult.ok==True with the RT echoed back when the mint
    succeeded (signaling that FOCI works in this tenant, so we can copy the RT
    into our own credentials store).
    """
    try:
        data = json.loads(creds_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return OutlookShareResult(ok=False, reason=f"read_failed: {exc}")

    if not isinstance(data, dict):
        return OutlookShareResult(ok=False, reason="invalid_credentials_format")

    rt = data.get("refresh_token")
    tid = data.get("tenant_id")
    cid = data.get("client_id")
    if not rt or not tid or not cid:
        return OutlookShareResult(ok=False, reason="missing_fields")

    c = client or httpx.Client(timeout=20.0, verify=http_verify())
    try:
        resp = c.post(
            f"https://login.microsoftonline.com/{tid}/oauth2/v2.0/token",
            data={
                "client_id": cid,
                "grant_type": "refresh_token",
                "refresh_token": rt,
                "scope": "https://teams.microsoft.com/.default",
            },
            headers={
                "Accept": "application/json",
                # The outlook-cli RT was minted at https://outlook.cloud.microsoft;
                # AADSTS9002327 requires the Origin to match that SPA redirect URI.
                "Origin": "https://outlook.cloud.microsoft",
            },
        )
    except httpx.HTTPError as exc:
        return OutlookShareResult(ok=False, reason=f"http_error: {exc}")

    if resp.status_code != 200:
        try:
            err_body = resp.json()
        except json.JSONDecodeError:
            err_body = {"raw": resp.text}
        if not isinstance(err_body, dict):
            err_body = {"raw": str(err_body)}
        err = err_body.get("error", f"http_{resp.status_code}")
        log.info("FOCI share with outlook-cli failed: %s", err)
        return OutlookShareResult(ok=False, reason=str(err))

    raw_claims = data.get("id_token_claims") or {}
    claims: dict[str, Any] = dict(raw_claims) if isinstance(raw_claims, dict) else {}
    preferred = claims.get("preferred_username") if isinstance(claims, dict) else None
    username_value = data.get("username") or preferred or ""

    return OutlookShareResult(
        ok=True,
        refresh_token=str(rt),
        tenant_id=str(tid),
        client_id=str(cid),
        username=str(username_value),
        home_account_id=str(data.get("home_account_id") or ""),
        id_token_claims=claims,
    )
