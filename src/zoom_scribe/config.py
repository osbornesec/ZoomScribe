"""Configuration models and utilities for ZoomScribe."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, cast

from .screenshare.preprocess import PreprocessConfig

_LOGGER = logging.getLogger(__name__)


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
        stream: IO[str] | None = None,
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


REDACTION_SUFFIX_LENGTH = 4


def _mask(value: str) -> str:
    """Return a partially redacted form of ``value`` for logging."""
    if len(value) <= REDACTION_SUFFIX_LENGTH:
        return "***"
    return f"***{value[-REDACTION_SUFFIX_LENGTH:]}"


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Structured logging configuration."""

    level: str = "info"
    format: str = "auto"


@dataclass(frozen=True, slots=True)
class DownloaderConfig:
    """Configuration for the recording asset downloader."""

    target_dir: Path = field(default_factory=lambda: Path("downloads"))
    overwrite: bool = False
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class ScreenshareConfig:
    """Configuration for screenshare preprocessing."""

    enabled: bool = False
    output_dir: Path | None = None
    preprocess_config: PreprocessConfig = field(default_factory=PreprocessConfig)


@dataclass(frozen=True, slots=True)
class Config:
    """Unified application configuration."""

    credentials: OAuthCredentials
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    downloader: DownloaderConfig = field(default_factory=DownloaderConfig)
    screenshare: ScreenshareConfig = field(default_factory=ScreenshareConfig)
    client_overrides: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OAuthCredentials:
    """Zoom Server-to-Server OAuth credentials loaded from the environment."""

    account_id: str
    client_id: str
    client_secret: str

    def __post_init__(self) -> None:
        """Ensure all credential fields are populated."""
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

    def __repr__(self) -> str:  # pragma: no cover - human friendly representation  # noqa: D105
        return (
            "OAuthCredentials("
            f"account_id={_mask(self.account_id)}, "
            f"client_id={_mask(self.client_id)}, "
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
        env = cast(MutableMapping[str, str | None], os.environ)
        resolved_path = None
        if dotenv_path:
            resolved_path = find_dotenv(str(dotenv_path), raise_error_if_not_found=False)
        else:
            resolved_path = find_dotenv(raise_error_if_not_found=False)
        if resolved_path:
            _LOGGER.debug("config.load_dotenv", extra={"path": resolved_path})
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
    "ENV_ACCOUNT_ID",
    "ENV_CLIENT_ID",
    "ENV_CLIENT_SECRET",
    "Config",
    "ConfigurationError",
    "DownloaderConfig",
    "LoggingConfig",
    "OAuthCredentials",
    "ScreenshareConfig",
    "load_oauth_credentials",
]
