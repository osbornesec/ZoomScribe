from __future__ import annotations

import hashlib
import hmac
import os
import warnings

_NEW_ENV_NAME = "ZOOMSCRIBE_REDACTION_KEY"
_LEGACY_ENV_NAMES = (
    "ZOOM_SCRIBE_REDACTION_KEY",
    "ZOOMScribe_REDACTION_KEY",
)

_REDACTION_KEY = os.getenv(_NEW_ENV_NAME)
if not _REDACTION_KEY:
    for legacy_name in _LEGACY_ENV_NAMES:
        legacy_value = os.getenv(legacy_name)
        if legacy_value:
            warnings.warn(
                f"{legacy_name} is deprecated; use {_NEW_ENV_NAME} instead",
                DeprecationWarning,
                stacklevel=2,
            )
            _REDACTION_KEY = legacy_value
            break


def _hash_str(value: str) -> str:
    """Return a deterministic hash using HMAC when a secret key is provided."""
    data = value.encode("utf-8")
    if _REDACTION_KEY:
        digest_hex = hmac.new(_REDACTION_KEY.encode("utf-8"), data, hashlib.sha256).hexdigest()
    else:
        digest_hex = hashlib.sha256(data).hexdigest()
    return f"sha256:{digest_hex[:32]}"


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
