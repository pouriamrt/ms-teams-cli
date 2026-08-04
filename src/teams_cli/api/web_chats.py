"""chatsvc-based chat list/read adapter.

This is the equivalent of ``graph_chats.py`` but talks to the legacy Teams
chatsvc/trouter API (the same one Teams web itself uses). many corporate tenants have
not preauthorized the Teams Web Client for Microsoft Graph ``Chat.Read*``
delegated permissions, so Graph ``/me/chats`` returns 403. chatsvc has its
own authorization mechanism (Skype token) that bypasses Graph's preauth list.

Endpoint base: ``https://teams.microsoft.com/api/chatsvc/{region}/v1``
Auth header:   ``Authentication: skypetoken=<jwt>`` (NOT Bearer)
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from teams_cli.api.client import ApiClient
from teams_cli.api.models import Chat, ChatType, Message, MessagePreview, Person, _parse_dt
from teams_cli.errors import ApiError, SessionExpired

log = logging.getLogger(__name__)

# messagetype values to skip when listing user messages.
_SYSTEM_MESSAGE_PREFIXES = ("Event/", "ThreadActivity/", "Control/")

# productThreadType → our ChatType. chatsvc returns many thread classes
# (channels, notification streams, call logs, mentions feeds, …). We keep
# only the three that correspond to Graph's /me/chats: Chat (group),
# OneToOneChat (1:1), and Meeting (meeting chat).
_PRODUCT_TYPE_TO_CHAT_TYPE: dict[str, ChatType] = {
    "Chat": ChatType.GROUP,
    "OneToOneChat": ChatType.ONE_ON_ONE,
    "Meeting": ChatType.MEETING,
}

_MRI_FROM_URL = re.compile(r"/contacts/([^/]+)$")


def _mri_to_oid(mri: str) -> str:
    """Parse `8:orgid:<oid>` → `<oid>`. Returns the input unchanged if it doesn't match."""
    if mri.startswith("8:orgid:"):
        return mri.split(":", 2)[2]
    return mri


def _parse_from_url(from_value: str | None) -> str:
    """Extract the MRI (e.g. `8:orgid:<oid>`) from chatsvc's `from` URL."""
    if not from_value:
        return ""
    m = _MRI_FROM_URL.search(from_value)
    return m.group(1) if m else from_value


def _parse_consumption_horizon(value: str | None) -> datetime | None:
    """Parse ``consumptionhorizon`` ("messageId;readAt_ms;...") to a UTC datetime.

    Format observed in samples: ``"<lastReadMessageId>;<lastReadAtEpochMs>;<...>"``.
    The second segment is the epoch-ms timestamp of the last read message.
    """
    if not value:
        return None
    parts = value.split(";")
    if len(parts) < 2:
        return None
    try:
        ts_ms = int(parts[1])
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)


def _classify_chat_type(conv: dict[str, Any]) -> ChatType:
    """Map chatsvc's ``productThreadType`` to our ChatType enum.

    chatsvc tells us the type directly via ``threadProperties.productThreadType``
    — no heuristics needed. Falls back to GROUP for unknown types.
    """
    tp = conv.get("threadProperties") or {}
    ptt = str(tp.get("productThreadType") or "")
    return _PRODUCT_TYPE_TO_CHAT_TYPE.get(ptt, ChatType.GROUP)


def _build_last_message_preview(
    raw: dict[str, Any] | None, my_user_id: str
) -> MessagePreview | None:
    if not raw:
        return None
    msg_type = str(raw.get("messagetype") or "")
    if msg_type.startswith(_SYSTEM_MESSAGE_PREFIXES):
        return None
    sender_mri = _parse_from_url(raw.get("from"))
    sender_oid = _mri_to_oid(sender_mri)
    created = (
        _parse_dt(raw.get("composetime"))
        or _parse_dt(raw.get("originalarrivaltime"))
        or datetime.now(UTC)
    )
    return MessagePreview.model_validate(
        {
            "from": Person(
                user_id=sender_oid,
                name=str(raw.get("imdisplayname") or "(unknown)"),
                from_me=bool(sender_oid) and sender_oid == my_user_id,
            ),
            "preview": str(raw.get("content") or ""),
            "created_at": created,
            "message_id": str(raw.get("id") or ""),
        }
    )


def chat_from_chatsvc(conv: dict[str, Any], my_user_id: str) -> Chat | None:
    """Map a single chatsvc conversation node to our Chat model.

    Returns None for:
    - Non-chat threads (channels, notification streams).
    - Chats with no actual user-visible last message (e.g. meeting chats
      that exist as placeholders for scheduled meetings but contain no
      conversation). Teams web's main "Chats" view also hides these.
    """
    tp = conv.get("threadProperties") or {}
    if str(tp.get("productThreadType") or "") not in _PRODUCT_TYPE_TO_CHAT_TYPE:
        return None

    last_message = _build_last_message_preview(conv.get("lastMessage"), my_user_id)
    # Filter out empty placeholders (mostly scheduled-meeting threads with
    # no conversation yet). They get a recent lastimreceivedtime from Teams
    # bookkeeping updates and would otherwise dominate the sorted top.
    if last_message is None:
        return None

    props = conv.get("properties") or {}
    last_received = _parse_dt(props.get("lastimreceivedtime"))
    last_read = _parse_consumption_horizon(props.get("consumptionhorizon"))
    last_msg_time = last_message.created_at if last_message else None

    # Unread ⇔ our read cursor (consumptionhorizon) is behind the last message.
    # We intentionally do NOT suppress chats whose last message is from us:
    # `chat mark-unread` is authoritative even when you sent last (matching
    # Teams desktop). In normal use, sending a message advances your own
    # cursor past it, so from-me chats don't false-positive here anyway.
    has_unread = bool(last_msg_time and (last_read is None or last_read < last_msg_time))

    last_updated = last_received or last_msg_time or datetime.now(UTC)

    return Chat(
        id=str(conv["id"]),
        topic=tp.get("topic"),
        chat_type=_classify_chat_type(conv),
        members=[],  # chatsvc list view doesn't include members; fetched lazily later
        last_message=last_message,
        has_unread=has_unread,
        unread_count=None,
        is_muted=False,
        last_updated=last_updated,
    )


def message_from_chatsvc(raw: dict[str, Any], my_user_id: str) -> Message:
    """Map a single chatsvc message node to our Message model."""
    msg_type = str(raw.get("messagetype") or "Text")
    body_format = "html" if msg_type.startswith("RichText") else "text"

    sender_mri = _parse_from_url(raw.get("from"))
    sender_oid = _mri_to_oid(sender_mri)
    sender = Person(
        user_id=sender_oid,
        name=str(raw.get("imdisplayname") or "(unknown)"),
        from_me=bool(sender_oid) and sender_oid == my_user_id,
    )
    created = (
        _parse_dt(raw.get("composetime"))
        or _parse_dt(raw.get("originalarrivaltime"))
        or datetime.now(UTC)
    )
    properties = raw.get("properties") or {}
    return Message.model_validate(
        {
            "id": str(raw["id"]),
            "chat_id": str(raw.get("conversationid") or ""),
            "from": sender,
            "created_at": created,
            "body": str(raw.get("content") or ""),
            "body_format": body_format,
            # chatsvc doesn't surface importance on the basic message; default it.
            "importance": "normal",
            "reactions": [],  # not in basic chatsvc message list
            "is_deleted": bool(raw.get("deletetime")),
            "reply_to_id": properties.get("replyToId"),
        }
    )


def _check(resp_status: int, text: str) -> None:
    if resp_status == 401:
        raise SessionExpired("401 from chatsvc. Skype token may be expired.")
    if resp_status >= 400:
        raise ApiError(f"chatsvc returned {resp_status}: {text}", status_code=resp_status)


class WebChats:
    """chatsvc adapter providing the same interface as GraphChats.list_chats / list_messages."""

    def __init__(self, client: ApiClient, my_user_id: str) -> None:
        self._c = client
        self._me = my_user_id

    # ----- list_chats -----

    def list_chats(
        self,
        *,
        top: int = 25,
        skip: int = 0,
        follow_all: bool = False,
        unread_only: bool = False,
        chat_type: str | None = None,
        since: datetime | None = None,
        with_counts: bool = False,
    ) -> tuple[list[Chat], int | None]:
        """List chats via chatsvc with optional pagination and client-side filters.

        chatsvc returns ALL thread types (channels, notification streams, chats)
        in a single feed. Graph's ``/me/chats`` is implicitly server-side
        filtered to chats only. To match Graph's UX we keep paging until we
        have ``top`` *chats* post-filter (or the feed is exhausted).
        """
        del with_counts  # chatsvc doesn't surface a cheap unread-count; treat as no-op
        # Over-fetch on the wire: most pages contain channels/notification
        # streams that get filtered out. Ask for at least 25 per page so we
        # don't burn round-trips when the user wants 5 chats.
        page_size = max(top * 3, 25)
        params: dict[str, Any] = {"pageSize": page_size, "view": "msnp24Equivalent"}
        if skip:
            params["startTime"] = skip  # chatsvc uses startTime (epoch ms) for cursor

        # Cap how many pages we'll fetch when not in --all mode, so an
        # adversarial feed of pure non-chat threads can't hang us forever.
        max_pages_without_follow_all = 5

        chats: list[Chat] = []
        sync_state: str | None = None
        pages_fetched = 0

        while True:
            if sync_state:
                resp = self._c.chatsvc_get(sync_state)
            else:
                resp = self._c.chatsvc_get("/users/ME/conversations", params=params)
            _check(resp.status_code, resp.text)
            body = resp.json() if resp.content else {}
            pages_fetched += 1

            for node in body.get("conversations") or []:
                chat = chat_from_chatsvc(node, my_user_id=self._me)
                if chat is None:
                    continue
                if since and chat.last_updated and chat.last_updated < since:
                    continue
                if chat_type and chat.chat_type.value != chat_type:
                    continue
                if unread_only and not chat.has_unread:
                    continue
                chats.append(chat)

            sync_state = (body.get("_metadata") or {}).get("syncState")
            if not sync_state:
                break
            if follow_all:
                continue  # exhaust everything
            # Non-follow-all: over-fetch (top*3) before stopping so the
            # final sort can pick a representative set, not just whatever
            # arrived in the first page. chatsvc doesn't guarantee
            # lastimreceivedtime-desc order, so we must sort ourselves.
            if len(chats) >= top * 3:
                break
            if pages_fetched >= max_pages_without_follow_all:
                break

        # Sort by recency (last_updated desc) and truncate to the user's top.
        chats.sort(key=lambda c: c.last_updated, reverse=True)
        chats = chats[:top]

        # Backfill members for 1:1 chats whose label would otherwise fall through
        # to "(self)" — namely, 1:1s where chatsvc gave us no last_message or
        # the last sender is me. The renderer prefers members[] over the
        # last-sender fallback, so populating it fixes the label.
        chats = [self._maybe_backfill_one_on_one(c) for c in chats]

        next_skip = None if not sync_state else (skip or 0) + top
        return chats, next_skip

    # ----- 1:1 member backfill -----

    def _maybe_backfill_one_on_one(self, chat: Chat) -> Chat:
        """Return ``chat`` unchanged unless it's a 1:1 with no usable label hint,
        in which case fetch members from chatsvc + Graph and return a new Chat."""
        if chat.chat_type != ChatType.ONE_ON_ONE:
            return chat
        if chat.members:
            return chat
        if chat.last_message is not None and not chat.last_message.from_.from_me:
            # We can label from the last sender's name; no need for the extra calls.
            return chat
        members = self._fetch_thread_members(chat.id)
        # If the only member we got back is me (degenerate self-thread or a
        # corrupted roster), populating members=[me_only] would still render
        # as "(self)" but would have cost us two HTTP calls. Skip the update.
        if not any(not m.from_me for m in members):
            return chat
        return chat.model_copy(update={"members": members})

    def _fetch_thread_members(self, chat_id: str) -> list[Person]:
        """Fetch members of a chatsvc thread and resolve display names via Graph.

        Returns ``[]`` on any error so the caller can fall through to the
        existing "(self)" behavior rather than crashing the whole chat list.
        """
        try:
            resp = self._c.chatsvc_get(f"/threads/{chat_id}")
        except Exception as exc:  # noqa: BLE001 — network-shaped errors must not crash list
            log.debug("thread fetch failed for %s: %s", chat_id, exc)
            return []
        if resp.status_code >= 400:
            log.debug("thread fetch %s returned %s", chat_id, resp.status_code)
            return []
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            return []

        members: list[Person] = []
        for raw in body.get("members") or []:
            mri = str(raw.get("id") or "")
            oid = _mri_to_oid(mri)
            if not oid:
                continue
            if oid == self._me:
                members.append(Person(user_id=oid, name="", email="", from_me=True))
                continue
            members.append(self._resolve_member(oid))
        return members

    def _resolve_member(self, oid: str) -> Person:
        """Look up displayName+email for a non-me OID via Graph /users/{oid}.

        Falls back to a (unknown) Person on any error.
        """
        try:
            resp = self._c.graph_get(f"/users/{oid}")
        except Exception as exc:  # noqa: BLE001
            log.debug("graph user fetch failed for %s: %s", oid, exc)
            return Person(user_id=oid, name="(unknown)", email="", from_me=False)
        if resp.status_code >= 400:
            log.debug("graph user fetch %s returned %s", oid, resp.status_code)
            return Person(user_id=oid, name="(unknown)", email="", from_me=False)
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            return Person(user_id=oid, name="(unknown)", email="", from_me=False)
        return Person(
            user_id=str(body.get("id") or oid),
            name=str(body.get("displayName") or "(unknown)"),
            email=str(body.get("mail") or body.get("userPrincipalName") or ""),
            from_me=False,
        )

    # ----- list_messages -----

    def list_messages(
        self,
        chat_id: str,
        *,
        top: int = 25,
        skip: int = 0,
        follow_all: bool = False,
        since: datetime | None = None,
    ) -> tuple[list[Message], int | None]:
        """List messages in a chat via chatsvc with optional pagination and since filter."""
        params: dict[str, Any] = {"pageSize": top}
        if since:
            # chatsvc uses startTime as epoch-ms — convert from datetime.
            params["startTime"] = int(since.timestamp() * 1000)

        collected: list[dict[str, Any]] = []
        next_url: str | None = None

        while True:
            if next_url:
                resp = self._c.chatsvc_get(next_url)
            else:
                resp = self._c.chatsvc_get(
                    f"/users/ME/conversations/{chat_id}/messages",
                    params=params,
                )
            _check(resp.status_code, resp.text)
            body = resp.json() if resp.content else {}
            collected.extend(body.get("messages") or [])
            next_url = (body.get("_metadata") or {}).get("backwardLink")
            if not follow_all or not next_url:
                break

        msgs: list[Message] = []
        for raw in collected:
            msg_type = str(raw.get("messagetype") or "")
            if msg_type.startswith(_SYSTEM_MESSAGE_PREFIXES):
                continue
            msgs.append(message_from_chatsvc(raw, my_user_id=self._me))

        # Carry the chat_id forward in case chatsvc omitted it on any message.
        for m in msgs:
            if not m.chat_id:
                m.chat_id = chat_id

        next_skip = None if not next_url else (skip or 0) + top
        return msgs, next_skip
