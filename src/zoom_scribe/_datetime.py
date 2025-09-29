from __future__ import annotations

from datetime import UTC, datetime


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime."""
    if value.tzinfo is None:
        raise ValueError(
            "Expected an aware datetime; supply a value with tzinfo or pre-normalize to UTC."
        )
    return value.astimezone(UTC)


__all__ = ["ensure_utc"]
