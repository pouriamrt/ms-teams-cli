"""chatsvc/ca/v1 fallback — used when Graph can't reach a feature (notably reactions
and the per-user chat read state).

Reaction payload shape on chatsvc is INFERRED from public Skype/Teams references; not
captured in our reference HAR. Phase 5 includes a verification task to confirm or correct
this on a freshly captured event.

The chat read-state (consumption horizon) write endpoint is also INFERRED from the
chatsvc property-write convention used for reactions and from the read-side shape we
already parse in ``web_chats._parse_consumption_horizon``. If a future HAR capture
shows a different shape, only this module needs to change.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from teams_cli.api.client import ApiClient
from teams_cli.errors import ApiError

log = logging.getLogger(__name__)


def set_reaction_via_chatsvc(
    client: ApiClient,
    *,
    chat_id: str,
    message_id: str,
    user_aad_id: str,
    reaction_type: str,
    unreact: bool = False,
) -> None:
    """Apply (or remove) a reaction via the chatsvc 'emotions' property.

    Body shape (inferred):
        { "emotions": [
            { "key": "<reactionType>",
              "users": [{ "mri": "8:orgid:<userAadId>", "time": <epoch_ms> }] }
          ] }
    For unreact, send an empty `users` array for that emotion key.
    """
    users_list: list[dict[str, Any]] = (
        [] if unreact else [{"mri": f"8:orgid:{user_aad_id}", "time": int(time.time() * 1000)}]
    )
    payload = {"emotions": [{"key": reaction_type, "users": users_list}]}

    resp = client.chatsvc_post(
        f"/users/ME/conversations/{chat_id}/messages/{message_id}/properties",
        json=payload,
        params={"name": "emotions"},
    )
    if resp.status_code >= 400:
        raise ApiError(
            f"chatsvc properties returned {resp.status_code}: {resp.text}",
            status_code=resp.status_code,
        )


def mark_chat_consumption_via_chatsvc(
    client: ApiClient,
    *,
    chat_id: str,
    my_aad_id: str,
    is_read: bool,
    since: datetime | None,
) -> bool:
    """Set/clear the chat's UNREAD marker via ``consumptionHorizonBookmark``.

    Endpoint, body shape, and auth verified against a captured Teams-web HAR:

        PUT /users/ME/conversations/{chat_id}/properties?name=consumptionHorizonBookmark
        Authorization: Bearer <IC3 AAD AT>
        Content-Type: application/json
        behavioroverride: redirectAs404 + clientinfo/x-ms-* (see client._ic3_headers)

        {"consumptionHorizonBookmark": "<cursorMs>;<actionMs>;<lastMessageId>"}

    Crucial semantics (verified empirically against the live tenant): the
    bookmark is an UNREAD marker, and the SIGN of the leading position is
    inverted from a naive "read cursor" reading:
      - cursor **nonzero** -> the chat is marked UNREAD (the marker is set).
        ``--since X`` places the marker at X; whole-chat-unread uses ``now``.
      - cursor **0** -> the chat is marked READ (the marker is cleared).
    Writing ``now`` for a "mark read" wrongly marks it unread; writing ``0``
    for a "mark unread" wrongly marks it read. Hence the explicit ``is_read``.

    Returns True when the change is confirmed, False on a silent no-op.
    """
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    if is_read:
        cursor_ms = 0  # clear the unread marker
    elif since is not None:
        cursor_ms = int(since.astimezone(UTC).timestamp() * 1000)
    else:
        cursor_ms = now_ms  # whole-chat unread: any nonzero marker
    payload = {"consumptionHorizonBookmark": f"{cursor_ms};{now_ms};0"}
    resp = client.chatsvc_put(
        f"/users/ME/conversations/{chat_id}/properties",
        json=payload,
        params={"name": "consumptionHorizonBookmark"},
        ic3_bearer=True,
    )
    if resp.status_code >= 400:
        raise ApiError(
            f"chatsvc consumptionHorizonBookmark returned {resp.status_code}: {resp.text}",
            status_code=resp.status_code,
        )

    # chatsvc returns 200 even when it silently ignores the write, so we read
    # the marker back and confirm the change actually took effect — for BOTH
    # directions, so neither can falsely report success on a no-op.
    return _verify_consumption(
        client,
        chat_id=chat_id,
        my_aad_id=my_aad_id,
        is_read=is_read,
        expected_cursor_ms=cursor_ms,
        now_ms=now_ms,
    )


# Tolerance (ms) absorbing server-side normalization of the cursor timestamp
# between what we PUT and what the read-back reports.
_VERIFY_TOLERANCE_MS = 2000
# How recent the read-back cursor must be to count as "read up to now" for the
# read direction, covering the case where clearing advances the cursor instead
# of zeroing it.
_READ_RECENCY_WINDOW_MS = 60_000


def _verify_consumption(
    client: ApiClient,
    *,
    chat_id: str,
    my_aad_id: str,
    is_read: bool,
    expected_cursor_ms: int,
    now_ms: int,
) -> bool:
    """GET the authoritative per-member marker and confirm the write took.

    Read-back shape (verified from HAR):
        GET /threads/{chat_id}/consumptionhorizons
        {"consumptionhorizons": [
            {"id": "8:orgid:<oid>", "consumptionhorizon": "<cursorMs>;<actionMs>;<msgId>"}
        ]}

    Verification per direction (the leading position is the cursor):
    - UNREAD: the marker must equal the nonzero value we set (±tolerance).
    - READ: the marker must be cleared — reported as ``0`` OR advanced to ~now
      (some server representations record "read up to now" rather than zeroing).

    Returns False on any unusable read-back (non-200, unparseable, missing
    entry, stale cursor) so the caller never falsely reports success.
    """
    resp = client.chatsvc_get(f"/threads/{chat_id}/consumptionhorizons", ic3_bearer=True)
    if resp.status_code != 200:
        log.info("consumptionhorizons read-back returned %s", resp.status_code)
        return False
    try:
        body = resp.json() if resp.content else {}
    except ValueError:
        return False
    mri = f"8:orgid:{my_aad_id}"
    for entry in body.get("consumptionhorizons") or []:
        if entry.get("id") != mri:
            continue
        raw = str(entry.get("consumptionhorizon") or "")
        head = raw.split(";", 1)[0]
        try:
            actual_cursor_ms = int(head)
        except ValueError:
            return False
        if is_read:
            return actual_cursor_ms == 0 or actual_cursor_ms >= now_ms - _READ_RECENCY_WINDOW_MS
        return abs(actual_cursor_ms - expected_cursor_ms) <= _VERIFY_TOLERANCE_MS
    log.info("no consumptionhorizon entry found for %s", mri)
    return False
