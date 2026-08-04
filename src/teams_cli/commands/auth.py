"""`teams login` / `logout` / `whoami` commands."""

from __future__ import annotations

import json
import logging

import typer

from teams_cli.auth import (
    capture_session,
    detect_outlook_credentials,
    load,
    parse_localstorage,
    persist_session,
    try_share,
)
from teams_cli.auth.login import ParsedSession
from teams_cli.config import paths_for
from teams_cli.errors import UserError

log = logging.getLogger(__name__)


def login_cmd(
    ctx: typer.Context,
    reuse_outlook_cli: bool = typer.Option(
        False,
        "--reuse-outlook-cli",
        help="Skip the prompt and reuse outlook-cli credentials non-interactively if available.",
    ),
    no_share: bool = typer.Option(
        False, "--no-share", help="Force bookmarklet flow; never reuse outlook-cli."
    ),
    timeout: int = typer.Option(
        300,
        "--timeout",
        help="Seconds to wait for the bookmarklet POST before giving up.",
    ),
) -> None:
    """Capture a Teams session via bookmarklet (or share from outlook-cli)."""
    p = paths_for()
    p.config_dir.mkdir(parents=True, exist_ok=True)

    # ----- 1. try the outlook-cli share path -----
    if not no_share:
        share_candidate = detect_outlook_credentials()
        if share_candidate is not None:
            result = try_share(share_candidate)
            if result.ok:
                proceed = reuse_outlook_cli
                if not proceed:
                    proceed = typer.confirm(
                        f"Detected outlook-cli credentials for "
                        f"{result.username or 'unknown user'}. Reuse them?",
                        default=True,
                    )
                if proceed:
                    parsed = ParsedSession(
                        refresh_token=result.refresh_token or "",
                        client_id=result.client_id or "",
                        tenant_id=result.tenant_id or "",
                        home_account_id=result.home_account_id or "",
                        username=result.username or "",
                        id_token_claims=result.id_token_claims or {},
                    )
                    persist_session(parsed, p.credentials, shared_from="outlook-cli")
                    typer.echo(f"Logged in as {parsed.username} (via outlook-cli share).")
                    raise typer.Exit(code=0)
            else:
                log.info("outlook-cli share path failed: %s", result.reason)

    # ----- 2. bookmarklet + localhost-hash capture -----
    try:
        storage = capture_session(timeout_seconds=timeout)
    except UserError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=exc.exit_code) from None

    try:
        parsed = parse_localstorage(storage)
    except LookupError as exc:
        typer.secho(
            f"Could not find an MSAL refresh-token entry in localStorage: {exc}. "
            f"Did you sign in on the Teams tab before clicking the bookmarklet?",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from None

    persist_session(parsed, p.credentials, shared_from=None)
    typer.echo(f"Logged in as {parsed.username}.")


def logout_cmd() -> None:
    """Delete the saved credentials and Skype token cache."""
    p = paths_for()
    removed_any = False
    for path in [
        p.credentials,
        p.access_tokens,
        p.skype_token,
        p.last_chat_listing,
        p.last_message_listing,
    ]:
        if path.exists():
            try:
                path.unlink()
                removed_any = True
            except OSError as exc:
                typer.secho(f"Could not delete {path}: {exc}", fg=typer.colors.YELLOW, err=True)
    if removed_any:
        typer.echo("Logged out.")
    else:
        typer.echo("No active session.")


def whoami_cmd(ctx: typer.Context) -> None:
    """Print the signed-in user info."""
    p = paths_for()
    creds = load(p.credentials)
    if creds is None:
        typer.secho(
            "Not logged in. Run 'teams login' to authenticate.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=77)

    is_json = bool(ctx.obj and ctx.obj.get("json"))
    payload = {
        "username": creds.username,
        "tenant_id": creds.tenant_id,
        "client_id": creds.client_id,
        "user_aad_id": creds.user_aad_id,
        "shared_from": creds.shared_from,
        "name": creds.id_token_claims.get("name"),
    }
    if is_json:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"Username:    {payload['username']}")
        typer.echo(f"Tenant:      {payload['tenant_id']}")
        typer.echo(f"User OID:    {payload['user_aad_id']}")
        if payload["name"]:
            typer.echo(f"Name:        {payload['name']}")
        if payload["shared_from"]:
            typer.echo(f"Shared from: {payload['shared_from']}")
