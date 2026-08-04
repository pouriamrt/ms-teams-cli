from datetime import UTC, datetime, timedelta

from freezegun import freeze_time

from teams_cli.dates import parse_since, to_local


@freeze_time("2026-05-22T18:00:00Z")
def test_parse_since_relative_durations() -> None:
    now = datetime.now(UTC)
    assert parse_since("1h") == now - timedelta(hours=1)
    assert parse_since("2d") == now - timedelta(days=2)
    assert parse_since("30m") == now - timedelta(minutes=30)


@freeze_time("2026-05-22T18:00:00Z")
def test_parse_since_human() -> None:
    result = parse_since("yesterday")
    assert result is not None
    assert result.date() == (datetime.now(UTC) - timedelta(days=1)).date()


def test_parse_since_iso() -> None:
    iso = "2026-05-20T15:00:00Z"
    assert parse_since(iso) == datetime(2026, 5, 20, 15, 0, tzinfo=UTC)


def test_parse_since_invalid_returns_none() -> None:
    assert parse_since("not a date") is None


def test_to_local_converts_utc() -> None:
    utc = datetime(2026, 5, 22, 18, 0, tzinfo=UTC)
    local = to_local(utc)
    assert local.tzinfo is not None
    # We don't assert the offset because it's host-TZ-dependent, but it must round-trip.
    assert local.astimezone(UTC) == utc
