"""Date parsing and timezone conversion."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import dateparser
from tzlocal import get_localzone

_REL = re.compile(r"^\s*(\d+)\s*(s|m|h|d|w)\s*$", re.IGNORECASE)
_UNITS: dict[str, str] = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_since(text: str) -> datetime | None:
    """Parse a 'since' expression to a UTC datetime, or None if uninterpretable.

    Accepts: '1h', '30m', '2d', '1w', ISO 8601, 'yesterday', 'last monday', etc.
    Relative durations are interpreted as `now - duration`.
    """
    m = _REL.match(text)
    if m:
        qty = int(m.group(1))
        unit = _UNITS[m.group(2).lower()]
        return datetime.now(UTC) - timedelta(**{unit: qty})
    parsed = dateparser.parse(text, settings={"RETURN_AS_TIMEZONE_AWARE": True})
    if parsed is None:
        return None
    return parsed.astimezone(UTC)


def to_local(dt: datetime) -> datetime:
    """Convert a timezone-aware datetime to the host local timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(get_localzone())
