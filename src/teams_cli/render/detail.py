"""rich panel for a single message in detail view."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from teams_cli.api.models import Message
from teams_cli.dates import to_local


def render_message_detail(console: Console, message: Message, *, raw: bool = False) -> None:
    body = message.body
    if message.body_format == "html" and not raw:
        try:
            import html2text

            conv = html2text.HTML2Text()
            conv.body_width = 0
            body = conv.handle(body).strip()
        except Exception:
            pass
    sender = "me" if message.from_.from_me else message.from_.name
    when = to_local(message.created_at).strftime("%Y-%m-%d %H:%M:%S %Z")
    rxns = ", ".join(
        f"{r.reaction_type} ({'me' if r.user.from_me else r.user.name})" for r in message.reactions
    )
    title = f"{sender} • {when}"
    if rxns:
        title += f"  [{rxns}]"
    console.print(Panel(body, title=title, expand=False))
