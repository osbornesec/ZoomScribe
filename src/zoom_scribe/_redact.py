from __future__ import annotations

import hashlib
import hmac
import os


_REDACTION_KEY = os.getenv("ZOOMScribe_REDACTION_KEY")


def _hash_str(value: str) -> str:
    """Return a deterministic hash using HMAC when a secret key is provided."""
    data = value.encode("utf-8")
    if _REDACTION_KEY:
        digest = hmac.new(_REDACTION_KEY.encode("utf-8"), data, hashlib.sha256)
    else:
        digest = hashlib.sha256(data)
    return f"sha256:{digest.hexdigest()[:32]}"


def redact_identifier(value: str | None) -> str | None:
    """Return a deterministic non-identifying representation for log output."""
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return ""

    return _hash_str(normalized)


def redact_uuid(value: str | None) -> str | None:
    """Specialized wrapper to redact UUID-like identifiers for logging."""
    return redact_identifier(value)


__all__ = ["redact_identifier", "redact_uuid"]
