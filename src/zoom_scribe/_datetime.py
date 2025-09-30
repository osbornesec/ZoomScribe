from __future__ import annotations

from datetime import UTC, datetime


def ensure_utc(value: datetime, *, assume_utc_if_naive: bool = False) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    Args:
        value: The datetime to normalise.
        assume_utc_if_naive: When ``True``, treat naive datetimes as already in UTC and
            attach ``datetime.UTC`` rather than raising.

    Raises:
        ValueError: If ``value`` is naive and ``assume_utc_if_naive`` is ``False``.
    """
    if value.tzinfo is None:
        if not assume_utc_if_naive:
            raise ValueError(
                "Expected an aware datetime; supply a value with tzinfo or pre-normalize to UTC."
            )
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["ensure_utc"]
