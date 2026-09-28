"""UTC timestamp helpers for MySQL-compatible naive datetime columns."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current UTC time without timezone metadata for MySQL."""
    return datetime.now(UTC).replace(tzinfo=None)
