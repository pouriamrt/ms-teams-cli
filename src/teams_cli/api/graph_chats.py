"""Microsoft Graph adapters for chat operations.

Used for chat send (``ChatMessage.Send`` scope), search, reactions, and
``ensure_one_on_one`` — all of which need Graph scopes that ARE preauthorized
for the Teams Web client.

List and read operations live in ``web_chats.py`` (chatsvc/trouter) because
Many corporate tenants have not preauthorized Graph ``Chat.Read*`` for this client.

Reactions have a Graph-first / chatsvc-fallback path (``web_chatsvc.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from teams_cli.api.client import ApiClient
from teams_cli.api.models import (
    Chat,
    Message,
    _parse_dt,
    chat_from_graph,
    message_from_graph,
)
from teams_cli.api.web_chatsvc import (
    mark_chat_consumption_via_chatsvc,
    set_reaction_via_chatsvc,
)
from teams_cli.errors import ApiError, SessionExpired

log = logging.getLogger(__name__)

_CHAT_SELECT = (
    "id,topic,createdDateTime,lastUpdatedDateTime,chatType,viewpoint,members,lastMessagePreview"
)
_CHAT_EXPAND = "members,lastMessagePreview"


def _check(resp_status: int, text: str) -> None:
    """Raise SessionExpired on 401, ApiError on >=400, no-op on 2xx."""
    if resp_status == 401:
        raise SessionExpired("401 from Graph. Token may be expired.")
    if resp_status >= 400:
        raise ApiError(f"Graph returned {resp_status}: {text}", status_code=resp_status)


class GraphChats:
    """Graph adapter for chat list/read operations."""

    def __init__(self, client: ApiClient, my_user_id: str, tenant_id: str) -> None:
        self._c = client
        self._me = my_user_id
        self._tenant_id = tenant_id

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
        """List chats from Graph with optional pagination and client-side filters.

        Returns (chats, next_skip). next_skip is None when the listing is exhausted
        or follow_all consumed all pages; otherwise it's the offset for the next page.
        """
        params: dict[str, Any] = {
            "$top": top,
            "$expand": _CHAT_EXPAND,
            "$orderby": "lastMessagePreview/createdDateTime desc",
        }
        if skip:
            params["$skip"] = skip

        collected_nodes: list[dict[str, Any]] = []
        next_url: str | None = None

        while True:
            if next_url is None:
                resp = self._c.graph_get("/me/chats", params=params)
            else:
                resp = self._c.graph_get(next_url)
            _check(resp.status_code, resp.text)
            body = resp.json() if resp.content else {}
            collected_nodes.extend(body.get("value") or [])
            next_url = body.get("@odata.nextLink")
            if not follow_all or next_url is None:
                break
            params = {}  # nextLink already encodes its own query params

        chats: list[Chat] = []
        for node in collected_nodes:
            chat = chat_from_graph(node, my_user_id=self._me)
            if since and chat.last_updated and chat.last_updated < since:
                continue
            if chat_type and chat.chat_type.value != chat_type:
                continue
            if unread_only and not chat.has_unread:
                continue
            chats.append(chat)

        if with_counts:
            for chat in chats:
                if not chat.has_unread:
                    chat.unread_count = 0
                    continue
                last_read = self._last_read_for(collected_nodes, chat.id)
                chat.unread_count = self._count_unread_messages(chat.id, last_read)

        # next_skip: when Graph returned a nextLink and we did NOT follow_all,
        # surface the numeric offset as a hint for the caller.
        next_skip = None if next_url is None else (skip + top)
        return chats, next_skip

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
        """List messages in a chat from Graph with optional pagination and since filter.

        Returns (messages, next_skip). Messages are newest-first (Graph's default).
        next_skip is None when the listing is exhausted or follow_all consumed all
        pages; otherwise it's the offset for the next page.
        """
        params: dict[str, Any] = {"$top": top, "$orderby": "createdDateTime desc"}
        if skip:
            params["$skip"] = skip
        if since:
            params["$filter"] = f"createdDateTime gt {since.isoformat().replace('+00:00', 'Z')}"

        collected_nodes: list[dict[str, Any]] = []
        next_url: str | None = None

        while True:
            if next_url is None:
                resp = self._c.graph_get(f"/chats/{chat_id}/messages", params=params)
            else:
                resp = self._c.graph_get(next_url)
            _check(resp.status_code, resp.text)
            body = resp.json() if resp.content else {}
            collected_nodes.extend(body.get("value") or [])
            next_url = body.get("@odata.nextLink")
            if not follow_all or next_url is None:
                break
            params = {}  # nextLink already encodes its own query params

        msgs = [message_from_graph(n, my_user_id=self._me) for n in collected_nodes]
        # Carry the chat_id forward for messages where Graph omitted it.
        for m in msgs:
            if not m.chat_id:
                m.chat_id = chat_id
        next_skip = None if next_url is None else (skip + top)
        return msgs, next_skip

    # ----- internal helpers -----

    def _last_read_for(self, nodes: list[dict[str, Any]], chat_id: str) -> datetime | None:
        for n in nodes:
            if n.get("id") == chat_id:
                vp = n.get("viewpoint") or {}
                return _parse_dt(vp.get("lastMessageReadDateTime"))
        return None

    def _count_unread_messages(self, chat_id: str, last_read: datetime | None) -> int:
        params: dict[str, Any] = {"$top": 50, "$orderby": "createdDateTime desc"}
        if last_read:
            params["$filter"] = f"createdDateTime gt {last_read.isoformat().replace('+00:00', 'Z')}"
        resp = self._c.graph_get(f"/chats/{chat_id}/messages", params=params)
        if resp.status_code != 200:
            log.info("count-unread failed for %s: %d", chat_id, resp.status_code)
            return 0
        msgs = (resp.json() or {}).get("value") or []
        count = 0
        for m in msgs:
            sender = ((m.get("from") or {}).get("user") or {}).get("id")
            if sender != self._me:
                count += 1
        return count

    # ----- ensure_one_on_one -----

    def ensure_one_on_one(self, other_user_id: str) -> str:
        payload = {
            "chatType": "oneOnOne",
            "members": [
                {
                    "@odata.type": "#microsoft.graph.aadUserConversationMember",
                    "roles": ["owner"],
                    "user@odata.bind": (f"https://graph.microsoft.com/v1.0/users('{self._me}')"),
                },
                {
                    "@odata.type": "#microsoft.graph.aadUserConversationMember",
                    "roles": ["owner"],
                    "user@odata.bind": (
                        f"https://graph.microsoft.com/v1.0/users('{other_user_id}')"
                    ),
                },
            ],
        }
        resp = self._c.graph_post("/chats", json=payload)
        if resp.status_code in (200, 201):
            return str(resp.json()["id"])
        if resp.status_code == 409:
            # Conflict: chat already exists. Locate it by listing.
            existing = self._find_one_on_one(other_user_id)
            if existing:
                return existing
            raise ApiError(
                "Conflict creating 1:1 chat but could not locate the existing one.",
                status_code=409,
            )
        if resp.status_code == 403:
            raise ApiError(
                f"Cannot start chat with user {other_user_id} — "
                "tenant federation policy may block.",
                status_code=403,
            )
        raise ApiError(
            f"Graph POST /chats returned {resp.status_code}: {resp.text}",
            status_code=resp.status_code,
        )

    def _find_one_on_one(self, other_user_id: str) -> str | None:
        # Pull a wider window than usual; chat may not be in the first 25.
        params: dict[str, Any] = {
            "$top": 100,
            "$expand": "members",
            "$orderby": "lastUpdatedDateTime desc",
        }
        resp = self._c.graph_get("/me/chats", params=params)
        if resp.status_code != 200:
            return None
        for node in (resp.json() or {}).get("value") or []:
            if node.get("chatType") != "oneOnOne":
                continue
            member_ids = {(m.get("userId") or "") for m in (node.get("members") or [])}
            if {self._me, other_user_id} <= member_ids:
                return str(node["id"])
        return None

    # ----- send_message -----

    def send_message(
        self,
        chat_id: str,
        *,
        body: str,
        html: bool = False,
        importance: str = "normal",
        reply_to_message_id: str | None = None,
    ) -> Message:
        content_type = "html" if html or reply_to_message_id else "text"
        content = body

        if reply_to_message_id:
            quoted = self._fetch_message_for_quote(chat_id, reply_to_message_id)
            quote_html = self._render_quote(quoted)
            content = (
                f"{quote_html}\n<p>{_html_escape(body)}</p>"
                if not html
                else f"{quote_html}\n{body}"
            )

        payload: dict[str, Any] = {
            "body": {"contentType": content_type, "content": content},
        }
        if importance != "normal":
            payload["importance"] = importance

        resp = self._c.graph_post(f"/chats/{chat_id}/messages", json=payload)
        if resp.status_code == 403:
            raise ApiError(
                "Chat-write blocked by tenant policy. Run with -v for details.",
                status_code=403,
            )
        if resp.status_code not in (200, 201):
            raise ApiError(
                f"Graph send returned {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
            )
        return message_from_graph(resp.json(), my_user_id=self._me)

    def _fetch_message_for_quote(self, chat_id: str, message_id: str) -> Message:
        resp = self._c.graph_get(f"/chats/{chat_id}/messages/{message_id}")
        if resp.status_code != 200:
            raise ApiError(
                f"Could not fetch message to quote: {resp.status_code}",
                status_code=resp.status_code,
            )
        return message_from_graph(resp.json(), my_user_id=self._me)

    @staticmethod
    def _render_quote(msg: Message) -> str:
        from html import escape

        sender = "me" if msg.from_.from_me else msg.from_.name
        snippet = msg.body if msg.body_format == "text" else _strip_html(msg.body)
        snippet = snippet.strip().replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200].rstrip() + "..."
        return f"<blockquote><strong>{escape(sender)}:</strong> {escape(snippet)}</blockquote>"

    # ----- mark chat read / unread -----

    def mark_chat_read(self, chat_id: str) -> MarkChatResult:
        """Mark a chat as read for the signed-in user.

        Tries Graph ``markChatReadForUser`` first. On 403 (tenant has not
        preauthorized ``Chat.ReadWrite`` for the Teams Web client — see
        README.md's implementation notes), falls back to the chatsvc
        consumption-horizon write. Returns which backend served the request.
        """
        return self._post_mark_chat(chat_id, action="markChatReadForUser", since=None)

    def mark_chat_unread(self, chat_id: str, *, since: datetime | None = None) -> MarkChatResult:
        """Mark a chat as unread for the signed-in user.

        When ``since`` is supplied, messages created after that instant appear
        unread; when omitted, the entire chat is marked unread. Same
        Graph-then-chatsvc fallback as ``mark_chat_read``.
        """
        return self._post_mark_chat(chat_id, action="markChatUnreadForUser", since=since)

    def _post_mark_chat(
        self, chat_id: str, *, action: str, since: datetime | None
    ) -> MarkChatResult:
        body: dict[str, Any] = {
            "user": {"id": self._me, "tenantId": self._tenant_id},
        }
        if since is not None:
            body["lastMessageReadDateTime"] = (
                since.astimezone(UTC).isoformat().replace("+00:00", "Z")
            )
        resp = self._c.graph_post(f"/chats/{chat_id}/{action}", json=body)
        is_read = action == "markChatReadForUser"

        if resp.status_code in (200, 204):
            return MarkChatResult(ok=True, via="graph", is_read=is_read, verified=True)
        if resp.status_code == 401:
            raise SessionExpired(f"401 from Graph {action}; token may be expired.")
        if resp.status_code == 404:
            raise ApiError(f"Chat {chat_id} not found.", status_code=404)
        if resp.status_code != 403:
            raise ApiError(
                f"Graph {action} returned {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
            )

        # 403 path — tenant did not preauthorize Chat.ReadWrite for the Teams
        # Web client. Fall back to chatsvc's consumptionHorizonBookmark, which
        # uses an IC3 Bearer token and its own access path. The bookmark is an
        # UNREAD marker (nonzero=unread, 0=read) — see mark_chat_consumption.
        log.info(
            "Graph %s returned 403; falling back to chatsvc consumption horizon",
            action,
        )
        try:
            verified = mark_chat_consumption_via_chatsvc(
                self._c,
                chat_id=chat_id,
                my_aad_id=self._me,
                is_read=is_read,
                since=since,
            )
        except ApiError as exc:
            raise ApiError(
                f"{action} failed via both paths: graph=403, chatsvc={exc.status_code}",
                status_code=exc.status_code or 0,
            ) from exc
        return MarkChatResult(ok=True, via="chatsvc", is_read=is_read, verified=verified)

    # ----- set_reaction (implementation patched in below) -----

    def set_reaction(
        self,
        chat_id: str,
        message_id: str,
        *,
        reaction_type: str,
        unreact: bool = False,
    ) -> ReactionResult:
        # Actual body replaced by `_set_reaction` patched onto the class below.
        raise NotImplementedError  # pragma: no cover

    # ----- search_messages (implementation patched in below) -----

    def search_messages(
        self,
        query: str,
        *,
        top: int = 25,
        skip: int = 0,
        scope_chat_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        # Actual body replaced by `_search_messages` patched onto the class below.
        raise NotImplementedError  # pragma: no cover


def _html_escape(text: str) -> str:
    from html import escape

    return escape(text).replace("\n", "<br>")


def _strip_html(text: str) -> str:
    try:
        import html2text

        conv = html2text.HTML2Text()
        conv.body_width = 0
        return conv.handle(text).strip()
    except Exception:
        import re

        return re.sub(r"<[^>]+>", "", text).strip()


@dataclass(frozen=True)
class ReactionResult:
    ok: bool
    reaction_type: str
    via: str  # "graph" | "chatsvc"
    unreact: bool


@dataclass(frozen=True)
class MarkChatResult:
    ok: bool
    via: str  # "graph" | "chatsvc"
    is_read: bool  # True for mark-read, False for mark-unread
    verified: bool  # True if the change was confirmed (Graph 2xx, or chatsvc read-back)


# Graph v1.0 setReaction/unsetReaction expect `reactionType` as a unicode
# emoji, not the legacy keyword enum (which now returns 400). These are the
# canonical glyphs for Teams' six default reactions; the CLI surface keeps the
# keywords and maps to unicode only here at the wire boundary.
_REACTION_EMOJI = {
    "like": "👍",
    "heart": "❤️",
    "laugh": "😆",
    "surprised": "😮",
    "sad": "😢",
    "angry": "😡",
}


# Patch onto the existing GraphChats class.
def _set_reaction(
    self: GraphChats,
    chat_id: str,
    message_id: str,
    *,
    reaction_type: str,
    unreact: bool = False,
) -> ReactionResult:
    action = "unsetReaction" if unreact else "setReaction"
    wire_reaction = _REACTION_EMOJI.get(reaction_type, reaction_type)
    resp = self._c.graph_post(
        f"/chats/{chat_id}/messages/{message_id}/{action}",
        json={"reactionType": wire_reaction},
    )
    if 200 <= resp.status_code < 300:
        return ReactionResult(ok=True, reaction_type=reaction_type, via="graph", unreact=unreact)

    # Fall back to chatsvc on 4xx-non-auth or 5xx.
    # Don't fall back on 401/403 — those are auth/policy issues.
    if resp.status_code in (401, 403):
        raise ApiError(
            f"Graph reaction returned {resp.status_code}: {resp.text}",
            status_code=resp.status_code,
        )
    log.info("Graph reaction returned %d; falling back to chatsvc", resp.status_code)
    try:
        set_reaction_via_chatsvc(
            self._c,
            chat_id=chat_id,
            message_id=message_id,
            user_aad_id=self._me,
            reaction_type=reaction_type,
            unreact=unreact,
        )
    except ApiError as exc:
        raise ApiError(
            f"Reaction failed via both paths: graph={resp.status_code}, chatsvc={exc.status_code}",
            status_code=exc.status_code or 0,
        ) from exc
    return ReactionResult(ok=True, reaction_type=reaction_type, via="chatsvc", unreact=unreact)


GraphChats.set_reaction = _set_reaction  # type: ignore[method-assign]


def _parse_iso(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("Z", "+00:00") if value.endswith("Z") else value


def _search_messages(
    self: GraphChats,
    query: str,
    *,
    top: int = 25,
    skip: int = 0,
    scope_chat_id: str | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    payload = {
        "requests": [
            {
                "entityTypes": ["chatMessage"],
                "query": {"queryString": query},
                "from": skip,
                "size": top,
            }
        ]
    }
    resp = self._c.graph_post("/search/query", json=payload)
    if resp.status_code == 401:
        raise SessionExpired("401 from Graph search; token may be expired.")
    if resp.status_code >= 400:
        raise ApiError(
            f"Graph search returned {resp.status_code}: {resp.text}",
            status_code=resp.status_code,
        )
    body = resp.json()
    hits_out: list[dict[str, Any]] = []
    total: int | None = None
    for resp_block in body.get("value") or []:
        for container in resp_block.get("hitsContainers") or []:
            if total is None:
                total = container.get("total")
            for hit in container.get("hits") or []:
                res = hit.get("resource") or {}
                from_user = (res.get("from") or {}).get("user") or {}
                created_at = _parse_iso(res.get("createdDateTime"))
                hit_chat = str(res.get("chatId") or "")
                if scope_chat_id and hit_chat != scope_chat_id:
                    continue
                hits_out.append(
                    {
                        "message_id": str(res.get("id") or hit.get("hitId") or ""),
                        "chat_id": hit_chat,
                        "preview": str(
                            (res.get("body") or {}).get("content") or hit.get("summary") or ""
                        ),
                        "from": {
                            "user_id": str(from_user.get("id") or ""),
                            "name": str(from_user.get("displayName") or ""),
                            "email": "",
                            "from_me": str(from_user.get("id") or "") == self._me,
                        },
                        "created_at": created_at,
                        "score": hit.get("score"),
                    }
                )
    return hits_out, total


GraphChats.search_messages = _search_messages  # type: ignore[method-assign]
