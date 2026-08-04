"""rich tables for `chat list` and `chat read`."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from teams_cli.api.models import Chat, ChatType, Message
from teams_cli.dates import to_local

_UNREAD = "●"
_READ = " "


def _chat_label(chat: Chat) -> str:
    if chat.topic:
        return chat.topic
    others = [m for m in chat.members if not m.from_me]
    if others:
        if chat.chat_type == ChatType.ONE_ON_ONE:
            return others[0].name
        names = ", ".join(m.name for m in others[:3])
        if len(others) > 3:
            names += f" +{len(others) - 3}"
        return names
    # No members (e.g. chatsvc list view doesn't return them) but we may
    # still have the last sender's name when it isn't from me — use that as
    # a sensible label for 1:1s. Falls through to "(self)" only when we
    # truly have nothing.
    if chat.last_message and not chat.last_message.from_.from_me:
        return chat.last_message.from_.name
    return "(self)"


def _kind(chat: Chat) -> str:
    return {
        ChatType.ONE_ON_ONE: "1:1",
        ChatType.GROUP: "grp",
        ChatType.MEETING: "mtg",
    }.get(chat.chat_type, "?")


def render_chat_list(console: Console, chats: list[Chat], indices: dict[int, str]) -> None:
    """Render a chat list. `indices` maps short-index → chat_id (1-based, in the order shown)."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", width=3)
    table.add_column("", width=1)
    table.add_column("Type", width=4)
    table.add_column("Chat")
    table.add_column("Last")
    table.add_column("Updated")

    reverse = {v: k for k, v in indices.items()}
    for chat in chats:
        idx = reverse.get(chat.id, 0)
        last_text = ""
        if chat.last_message:
            preview = chat.last_message.preview[:60].replace("\n", " ")
            tag = (
                "me" if chat.last_message.from_.from_me else chat.last_message.from_.name.split()[0]
            )
            last_text = f"{tag}: {preview}"
        updated = to_local(chat.last_updated).strftime("%a %H:%M")
        table.add_row(
            str(idx) if idx else "-",
            _UNREAD if chat.has_unread else _READ,
            _kind(chat),
            _chat_label(chat),
            last_text,
            updated,
        )
    console.print(table)


def render_messages(console: Console, messages: list[Message], indices: dict[int, str]) -> None:
    """Render messages oldest-first. `indices` maps short-index → message_id (1-based)."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", width=3)
    table.add_column("Time", width=8)
    table.add_column("From")
    table.add_column("Message")
    table.add_column("Rxn", width=12)

    reverse = {v: k for k, v in indices.items()}
    ordered = sorted(messages, key=lambda m: m.created_at)
    for msg in ordered:
        idx = reverse.get(msg.id, 0)
        when = to_local(msg.created_at).strftime("%H:%M")
        sender = "me" if msg.from_.from_me else msg.from_.name
        body = msg.body
        if msg.body_format == "html":
            try:
                import html2text  # local to avoid import cost when --raw

                conv = html2text.HTML2Text()
                conv.body_width = 0
                body = conv.handle(body).strip()
            except Exception:
                pass
        # Truncate visually; full body available via chat read --raw.
        flat = body.replace("\n", " ").strip()
        if len(flat) > 80:
            flat = flat[:77] + "..."
        rxns = ",".join({r.reaction_type for r in msg.reactions}) if msg.reactions else ""
        table.add_row(str(idx) if idx else "-", when, sender, flat, rxns)
    console.print(table)
