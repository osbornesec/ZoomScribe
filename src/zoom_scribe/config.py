from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, MutableMapping

try:  # pragma: no cover - imported lazily in production environments
    from dotenv import find_dotenv, load_dotenv
except ImportError:  # pragma: no cover - fallback for minimal environments

    def find_dotenv(
        filename: str = "",
        raise_error_if_not_found: bool = False,
        usecwd: bool = False,
    ) -> str:
        _ = (filename, raise_error_if_not_found, usecwd)
        return ""

    def load_dotenv(
        dotenv_path: str | os.PathLike[str] | None = None,
        stream=None,
        verbose: bool = False,
        override: bool = False,
        interpolate: bool = True,
        encoding: str | None = None,
    ) -> bool:
        _ = (dotenv_path, stream, verbose, override, interpolate, encoding)
        return False


ENV_ACCOUNT_ID = "ZOOM_ACCOUNT_ID"
ENV_CLIENT_ID = "ZOOM_CLIENT_ID"
ENV_CLIENT_SECRET = "ZOOM_CLIENT_SECRET"


class ConfigurationError(RuntimeError):
    """Raised when Zoom configuration is missing or invalid."""


def _mask(value: str) -> str:
    """Return a partially redacted form of ``value`` for logging."""

    if len(value) <= 4:
        return "***"
    return f"***{value[-4:]}"


@dataclass(frozen=True, slots=True)
class OAuthCredentials:
    """Zoom Server-to-Server OAuth credentials loaded from the environment."""

    account_id: str
    client_id: str
    client_secret: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("account_id", self.account_id),
            ("client_id", self.client_id),
            ("client_secret", self.client_secret),
        ):
            if not value:
                raise ConfigurationError(f"Missing OAuth credential: {field_name}")

    def to_dict(self) -> Mapping[str, str]:
        """Return the credentials as a plain mapping for convenience."""

        return {
            "account_id": self.account_id,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

    def __repr__(self) -> str:  # pragma: no cover - human friendly representation
        return (
            "OAuthCredentials(" \
            f"account_id={_mask(self.account_id)}, " \
            f"client_id={_mask(self.client_id)}, " \
            "client_secret=***"
            ")"
        )


def load_oauth_credentials(
    *,
    dotenv_path: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str | None] | None = None,
) -> OAuthCredentials:
    """Load Zoom OAuth credentials from the environment and optional ``.env`` file.

    Args:
        dotenv_path: Optional path to a dotenv file. When supplied, the file is
            loaded before reading environment variables. The default behaviour
            mirrors :func:`python_dotenv.find_dotenv`.
        environ: Optional mapping used instead of :data:`os.environ`. Primarily
            intended for testing.

    Returns:
        Loaded :class:`OAuthCredentials` instance with validated values.

    Raises:
        ConfigurationError: When any credential is missing.
    """

    env: MutableMapping[str, str | None]
    if environ is not None:
        env = dict(environ)
    else:
        env = os.environ
        resolved_path = None
        if dotenv_path:
            resolved_path = find_dotenv(str(dotenv_path), raise_error_if_not_found=False)
        else:
            resolved_path = find_dotenv(raise_error_if_not_found=False)
        if resolved_path:
            load_dotenv(resolved_path, override=False)

    account_id = env.get(ENV_ACCOUNT_ID)
    client_id = env.get(ENV_CLIENT_ID)
    client_secret = env.get(ENV_CLIENT_SECRET)

    missing = [
        name
        for name, value in (
            (ENV_ACCOUNT_ID, account_id),
            (ENV_CLIENT_ID, client_id),
            (ENV_CLIENT_SECRET, client_secret),
        )
        if not value
    ]
    if missing:
        joined = ", ".join(missing)
        raise ConfigurationError(f"Missing Zoom OAuth credentials: {joined}")

    return OAuthCredentials(
        account_id=str(account_id),
        client_id=str(client_id),
        client_secret=str(client_secret),
    )


__all__ = [
    "ConfigurationError",
    "ENV_ACCOUNT_ID",
    "ENV_CLIENT_ID",
    "ENV_CLIENT_SECRET",
    "OAuthCredentials",
    "load_oauth_credentials",
]
