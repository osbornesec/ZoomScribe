from __future__ import annotations

import hashlib


def redact_identifier(value: str | None) -> str | None:
    """Return a deterministic non-identifying representation for log output."""
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return ""

    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:32]}"


def redact_uuid(value: str | None) -> str | None:
    """Specialized wrapper to redact UUID-like identifiers for logging."""
    return redact_identifier(value)


__all__ = ["redact_identifier", "redact_uuid"]
