"""`teams chat` command group."""

from __future__ import annotations

import json as _json
import logging
import sys
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import typer
from rich.console import Console

from teams_cli.api.client import ApiClient
from teams_cli.api.graph_chats import GraphChats, MarkChatResult
from teams_cli.api.people import PeopleResolver
from teams_cli.api.web_chats import WebChats
from teams_cli.auth import (
    GRAPH_SCOPE,
    IC3_SCOPE,
    TEAMS_SCOPE,
    SkypeTokenMinter,
    TokenRefresher,
    load,
)
from teams_cli.commands.composer import compose_body
from teams_cli.config import paths_for
from teams_cli.dates import parse_since
from teams_cli.errors import NotFound, SessionExpired, UserError
from teams_cli.index_cache import (
    ChatListing,
    MessageListing,
    save_chat_listing,
    save_message_listing,
)
from teams_cli.render.json_out import dump_chat_list, dump_messages
from teams_cli.render.tables import render_chat_list, render_messages
from teams_cli.resolve import is_chat_id, is_email, resolve_chat, resolve_message

log = logging.getLogger(__name__)

chat_app = typer.Typer(
    name="chat",
    help="Chat operations (DMs, group, meeting).",
    no_args_is_help=True,
)

_TYPE_MAP: Mapping[str, str] = {
    "oneonone": "oneOnOne",
    "group": "group",
    "meeting": "meeting",
}


def _build_clients() -> tuple[ApiClient, GraphChats, WebChats, str]:
    """Construct the API client + chat adapters from on-disk credentials.

    Returns ``(client, graph_chats, web_chats, my_user_id)``. ``web_chats``
    talks to chatsvc/trouter — used for list/read because many corporate tenants have
    not preauthorized Graph ``Chat.Read*`` for the Teams Web client.
    ``graph_chats`` is kept for send/search/react/ensure-one-on-one which
    use scopes that ARE preauthorized (``ChatMessage.Send``, Search, etc.).

    Raises SessionExpired if no credentials are stored.
    """
    p = paths_for()
    creds = load(p.credentials)
    if creds is None:
        raise SessionExpired("Not logged in. Run 'teams login' to authenticate.")
    refresher = TokenRefresher(creds_path=p.credentials, cache_path=p.access_tokens)
    skype = SkypeTokenMinter(cache_path=p.skype_token)
    aad_id = creds.user_aad_id
    client = ApiClient(
        get_graph_token=lambda: refresher.get_token(GRAPH_SCOPE),
        get_teams_token=lambda: refresher.get_token(TEAMS_SCOPE),
        get_skype_token=lambda: skype.get_skype_token(
            user_aad_id=aad_id,
            aad_teams_token=refresher.get_token(TEAMS_SCOPE),
        ),
        get_ic3_token=lambda: refresher.get_token(IC3_SCOPE),
    )
    gc = GraphChats(client=client, my_user_id=aad_id, tenant_id=creds.tenant_id)
    wc = WebChats(client=client, my_user_id=aad_id)
    return client, gc, wc, aad_id


def _safe_build_clients() -> tuple[ApiClient, GraphChats, WebChats, str]:
    """Build clients and translate SessionExpired into a clean typer.Exit(77).

    CliRunner.invoke doesn't pass through the main() TeamsError catch, so
    commands must turn SessionExpired into a typer.Exit themselves.
    """
    try:
        return _build_clients()
    except SessionExpired as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=77) from None


def _resolve_target_to_chat_id(target: str, *, client: ApiClient, gc: GraphChats) -> str:
    """Resolve a chat target (index | chat-id | email) to a chat_id.

    On `NotFound`, emits a red error to stderr and raises `typer.Exit(64)`.
    Emails resolve via the people cache and `ensure_one_on_one`.
    """
    p = paths_for()
    try:
        if is_email(target):
            resolver = PeopleResolver(client=client, cache_path=p.people_cache)
            person = resolver.resolve(target)
            return gc.ensure_one_on_one(person.user_id)
        return resolve_chat(target, chat_listing_path=p.last_chat_listing)
    except NotFound as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=64) from None


def _emit_mark_result(
    ctx: typer.Context,
    *,
    chat_id: str,
    state: str,
    since_iso: str | None,
    result: MarkChatResult,
) -> None:
    """Emit mark-read/unread output. Exits non-zero when the change could not
    be confirmed (chatsvc returned 200 but the read cursor never moved)."""
    error = (
        None
        if result.verified
        else (
            f"server accepted the request (via {result.via}) but the change "
            f"was not confirmed; your tenant likely blocks marking chats "
            f"{state} for the Teams Web client"
        )
    )
    is_json = bool(ctx.obj and ctx.obj.get("json"))
    payload: dict[str, Any] = {
        "ok": result.verified,
        "chat_id": chat_id,
        "state": state,
        "via": result.via,
        "verified": result.verified,
        "error": error,
    }
    if state == "unread":
        payload["since"] = since_iso

    if is_json:
        typer.echo(_json.dumps(payload, indent=2))
    elif result.verified:
        suffix = f" (since {since_iso})" if since_iso else ""
        typer.echo(f"Marked chat as {state}{suffix} (via {result.via}).")
    else:
        typer.secho(f"Could not mark chat as {state}: {error}.", fg=typer.colors.YELLOW, err=True)

    if not result.verified:
        raise typer.Exit(code=1)


@chat_app.command("list")
def list_cmd(
    ctx: typer.Context,
    unread: bool = typer.Option(False, "--unread", help="Only chats with unread messages."),
    top: int = typer.Option(25, "--top", help="Page size."),
    all_pages: bool = typer.Option(False, "--all", help="Follow @odata.nextLink to the end."),
    chat_type: str | None = typer.Option(
        None, "--type", help="Filter: oneonone | group | meeting."
    ),
    since: str | None = typer.Option(
        None, "--since", help="Filter to chats updated since '2d' / 'yesterday'."
    ),
    with_counts: bool = typer.Option(
        False,
        "--with-counts",
        help="Populate exact unread_count (N+1 Graph calls; default off).",
    ),
) -> None:
    """List recent chats."""
    if chat_type is not None and chat_type not in _TYPE_MAP:
        typer.secho(
            f"Bad --type: {chat_type!r}. Use: oneonone | group | meeting.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    since_dt: datetime | None = None
    if since:
        since_dt = parse_since(since)
        if since_dt is None:
            typer.secho(
                f"Could not parse --since {since!r}.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)

    try:
        _client, _gc, wc, _me = _build_clients()
        chats, next_skip = wc.list_chats(
            top=top,
            follow_all=all_pages,
            unread_only=unread,
            chat_type=_TYPE_MAP[chat_type] if chat_type else None,
            since=since_dt,
            with_counts=with_counts,
        )
    except SessionExpired as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=77) from None

    indices: dict[int, str] = {i + 1: c.id for i, c in enumerate(chats)}
    p = paths_for()
    save_chat_listing(
        ChatListing(captured_at=datetime.utcnow().isoformat() + "Z", entries=indices),
        p.last_chat_listing,
    )

    is_json = bool(ctx.obj and ctx.obj.get("json"))
    console = Console()
    if is_json:
        dump_chat_list(console, chats, indices=indices, next_skip=next_skip)
    else:
        render_chat_list(console, chats, indices=indices)


@chat_app.command("read")
def read_cmd(
    ctx: typer.Context,
    target: str = typer.Argument(
        ..., help="Chat index (from `chat list`), chat-id, or email (1:1 only)."
    ),
    top: int = typer.Option(25, "--top", help="Number of messages."),
    all_pages: bool = typer.Option(False, "--all", help="Follow @odata.nextLink to the end."),
    since: str | None = typer.Option(
        None, "--since", help="Only messages newer than '1h' / 'yesterday'."
    ),
    raw: bool = typer.Option(False, "--raw", help="Don't convert HTML bodies to markdown."),
) -> None:
    """Read messages in a chat."""
    p = paths_for()

    if is_chat_id(target):
        chat_id = target
    else:
        try:
            chat_id = resolve_chat(target, chat_listing_path=p.last_chat_listing)
        except NotFound as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=64) from None

    since_dt: datetime | None = None
    if since:
        since_dt = parse_since(since)
        if since_dt is None:
            typer.secho(
                f"Could not parse --since {since!r}.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)

    _client, _gc, wc, _me = _safe_build_clients()
    msgs, next_skip = wc.list_messages(chat_id, top=top, follow_all=all_pages, since=since_dt)

    # Display order is oldest-first; assign indices accordingly so
    # `chat react 1` is the oldest visible message.
    ordered = sorted(msgs, key=lambda m: m.created_at)
    indices: dict[int, str] = {i + 1: m.id for i, m in enumerate(ordered)}
    save_message_listing(
        MessageListing(
            captured_at=datetime.utcnow().isoformat() + "Z",
            chat_id=chat_id,
            entries=indices,
        ),
        p.last_message_listing,
    )

    is_json = bool(ctx.obj and ctx.obj.get("json"))
    console = Console()
    if is_json:
        dump_messages(console, ordered, chat_id=chat_id, indices=indices, next_skip=next_skip)
    else:
        render_messages(console, ordered, indices=indices)


@chat_app.command("send")
def send_cmd(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Chat index, chat-id, or email (1:1 to user)."),
    body: str | None = typer.Option(
        None, "--body", help="Message body. '@-' or '-' to read stdin."
    ),
    html: bool = typer.Option(False, "--html", help="Send the body as HTML."),
    reply_to: int | None = typer.Option(
        None,
        "--reply-to",
        help="Index of a message to quote (from last `chat read`).",
    ),
    importance: str = typer.Option("normal", "--importance", help="normal | high | urgent."),
) -> None:
    """Send a new message to a chat (or a user by email)."""
    if importance not in {"normal", "high", "urgent"}:
        typer.secho(f"Bad --importance: {importance!r}.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    stdin_data: str | None = None
    if body in {"@-", "-"} and not sys.stdin.isatty():
        stdin_data = sys.stdin.read()

    try:
        text = compose_body(body, stdin_data)
    except UserError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=exc.exit_code) from None

    p = paths_for()
    client, gc, _wc, _me = _safe_build_clients()

    chat_id = _resolve_target_to_chat_id(target, client=client, gc=gc)

    # Resolve --reply-to message index, if any.
    reply_to_msg_id: str | None = None
    if reply_to is not None:
        try:
            _, reply_to_msg_id = resolve_message(
                str(reply_to), message_listing_path=p.last_message_listing
            )
        except NotFound as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=64) from None

    msg = gc.send_message(
        chat_id,
        body=text,
        html=html,
        importance=importance,
        reply_to_message_id=reply_to_msg_id,
    )

    is_json = bool(ctx.obj and ctx.obj.get("json"))
    if is_json:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": msg.id,
            "created_at": msg.created_at.isoformat().replace("+00:00", "Z"),
        }
        if reply_to_msg_id:
            payload["reply_to_id"] = reply_to_msg_id
        typer.echo(_json.dumps(payload, indent=2))
    else:
        # Recipient label is best-effort (Person object isn't always available).
        typer.echo(f"Sent → {target}")


@chat_app.command("reply")
def reply_cmd(
    ctx: typer.Context,
    msg_idx: int = typer.Argument(..., help="Message index from the last `chat read`."),
    body: str | None = typer.Option(None, "--body", help="Body text. '@-' or '-' for stdin."),
    html: bool = typer.Option(False, "--html", help="Treat body as HTML."),
    importance: str = typer.Option("normal", "--importance", help="normal | high | urgent."),
) -> None:
    """Reply to a message in the last-read chat (quotes the source message)."""
    if importance not in {"normal", "high", "urgent"}:
        typer.secho(f"Bad --importance: {importance!r}.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    stdin_data: str | None = None
    if body in {"@-", "-"} and not sys.stdin.isatty():
        stdin_data = sys.stdin.read()

    try:
        text = compose_body(body, stdin_data)
    except UserError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=exc.exit_code) from None

    p = paths_for()
    _client, gc, _wc, _me = _safe_build_clients()

    try:
        chat_id, src_msg_id = resolve_message(
            str(msg_idx), message_listing_path=p.last_message_listing
        )
    except NotFound as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=64) from None

    msg = gc.send_message(
        chat_id,
        body=text,
        html=html,
        importance=importance,
        reply_to_message_id=src_msg_id,
    )

    is_json = bool(ctx.obj and ctx.obj.get("json"))
    if is_json:
        typer.echo(
            _json.dumps(
                {
                    "chat_id": chat_id,
                    "message_id": msg.id,
                    "reply_to_id": src_msg_id,
                    "created_at": msg.created_at.isoformat().replace("+00:00", "Z"),
                },
                indent=2,
            )
        )
    else:
        typer.echo(f"Replied (msg {msg.id})")


_SUPPORTED_REACTIONS = ("like", "heart", "laugh", "surprised", "sad", "angry")


@chat_app.command("react")
def react_cmd(
    ctx: typer.Context,
    msg_idx: int = typer.Argument(..., help="Message index from the last `chat read`."),
    emoji: str = typer.Argument(..., help=f"One of: {', '.join(_SUPPORTED_REACTIONS)}."),
    unreact: bool = typer.Option(False, "--unreact", help="Remove the reaction instead of adding."),
) -> None:
    """React (or unreact with --unreact) to a message in the last-read chat."""
    if emoji not in _SUPPORTED_REACTIONS:
        typer.secho(
            f"Unsupported reaction {emoji!r}. "
            f"Supported reactions: {', '.join(_SUPPORTED_REACTIONS)}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    p = paths_for()
    _client, gc, _wc, _me = _safe_build_clients()

    try:
        chat_id, msg_id = resolve_message(str(msg_idx), message_listing_path=p.last_message_listing)
    except NotFound as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=64) from None

    result = gc.set_reaction(chat_id, msg_id, reaction_type=emoji, unreact=unreact)

    is_json = bool(ctx.obj and ctx.obj.get("json"))
    if is_json:
        typer.echo(
            _json.dumps(
                {
                    "ok": result.ok,
                    "reaction": result.reaction_type,
                    "via": result.via,
                    "unreact": result.unreact,
                },
                indent=2,
            )
        )
    else:
        verb = "Removed reaction" if unreact else "Reacted"
        typer.echo(f"{verb} '{emoji}' (via {result.via})")


@chat_app.command("mark-read")
def mark_read_cmd(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Chat index, chat-id, or email (1:1 to user)."),
) -> None:
    """Mark a chat as read for the signed-in user."""
    client, gc, _wc, _me = _safe_build_clients()
    chat_id = _resolve_target_to_chat_id(target, client=client, gc=gc)
    result = gc.mark_chat_read(chat_id)
    _emit_mark_result(ctx, chat_id=chat_id, state="read", since_iso=None, result=result)


@chat_app.command("mark-unread")
def mark_unread_cmd(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Chat index, chat-id, or email (1:1 to user)."),
    since: str | None = typer.Option(
        None,
        "--since",
        help=(
            "Optional cutoff (ISO 8601 or '1h', '2d', 'yesterday'). "
            "Messages after this point appear unread; omit to mark the whole chat."
        ),
    ),
) -> None:
    """Mark a chat as unread (optionally from a cutoff timestamp)."""
    since_dt = None
    if since is not None:
        since_dt = parse_since(since)
        if since_dt is None:
            typer.secho(
                f"Could not parse --since {since!r}.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)

    client, gc, _wc, _me = _safe_build_clients()
    chat_id = _resolve_target_to_chat_id(target, client=client, gc=gc)
    result = gc.mark_chat_unread(chat_id, since=since_dt)
    since_iso = since_dt.isoformat().replace("+00:00", "Z") if since_dt else None
    _emit_mark_result(ctx, chat_id=chat_id, state="unread", since_iso=since_iso, result=result)


@chat_app.command("search")
def search_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query string (KQL-ish)."),
    top: int = typer.Option(25, "--top", help="Page size."),
    all_pages: bool = typer.Option(False, "--all", help="Follow paging to the end."),
    in_chat: int | None = typer.Option(
        None,
        "--in",
        help="Restrict to one chat (index from last `chat list`).",
    ),
) -> None:
    """Search chat messages via Microsoft Search."""
    p = paths_for()
    _client, gc, _wc, _me = _safe_build_clients()

    scope_chat_id: str | None = None
    if in_chat is not None:
        try:
            scope_chat_id = resolve_chat(str(in_chat), chat_listing_path=p.last_chat_listing)
        except NotFound as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=64) from None

    all_hits: list[dict[str, Any]] = []
    total_total: int | None = None
    skip = 0
    while True:
        hits, total = gc.search_messages(query, top=top, skip=skip, scope_chat_id=scope_chat_id)
        all_hits.extend(hits)
        if total_total is None:
            total_total = total
        if not all_pages or len(hits) < top:
            break
        skip += top

    is_json = bool(ctx.obj and ctx.obj.get("json"))
    if is_json:
        payload = {"items": all_hits, "total_estimated": total_total}
        typer.echo(_json.dumps(payload, indent=2, default=str))
    else:
        if not all_hits:
            typer.echo("No matches.")
            if total_total == 0:
                typer.echo("(Microsoft Search index may be delayed for very recent messages.)")
            return
        for i, hit in enumerate(all_hits, start=1):
            who = hit["from"]["name"] or "(unknown)"
            preview = (hit["preview"] or "").replace("\n", " ")
            if len(preview) > 100:
                preview = preview[:97] + "..."
            typer.echo(f"[{i}] {who}: {preview}")
