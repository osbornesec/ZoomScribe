from __future__ import annotations

import logging
import random
import os
import time
from datetime import datetime
from typing import IO, Any, TypedDict
from urllib.parse import quote, urljoin

import requests

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:  # pragma: no cover

    def find_dotenv(
        filename: str = "",
        raise_error_if_not_found: bool = False,
        usecwd: bool = False,
    ) -> str:
        """Return the provided dotenv filename or an empty string."""
        _ = raise_error_if_not_found  # pragma: no cover - unused in stub
        _ = usecwd  # pragma: no cover - unused in stub
        return filename or ""

    def load_dotenv(
        dotenv_path: str | os.PathLike[str] | None = None,
        stream: IO[str] | None = None,
        verbose: bool = False,
        override: bool = False,
        interpolate: bool = True,
        encoding: str | None = None,
    ) -> bool:
        """Stub load_dotenv that always reports no file was loaded."""
        _ = (dotenv_path, stream, verbose, override, interpolate, encoding)
        return False


from ._datetime import ensure_utc
from ._redact import redact_identifier, redact_uuid
from .models import Recording, RecordingFile


class OAuthCredentials(TypedDict):
    account_id: str
    client_id: str
    client_secret: str


_LOGGER = logging.getLogger(__name__)


class MissingCredentialsError(RuntimeError):
    """Raised when required OAuth credentials are missing."""


class TokenRefreshError(RuntimeError):
    """Raised when an access token cannot be refreshed."""


def load_env_credentials(dotenv_path: str | None = None) -> OAuthCredentials:
    """Load Zoom OAuth credentials from a .env file or the environment."""
    if dotenv_path:
        resolved_path = find_dotenv(dotenv_path, raise_error_if_not_found=False)
    else:
        resolved_path = find_dotenv(raise_error_if_not_found=False)
    if resolved_path:
        load_dotenv(resolved_path, override=False)
    account_id = os.getenv("ZOOM_ACCOUNT_ID")
    client_id = os.getenv("ZOOM_CLIENT_ID")
    client_secret = os.getenv("ZOOM_CLIENT_SECRET")
    missing = [
        name
        for name, value in (
            ("ZOOM_ACCOUNT_ID", account_id),
            ("ZOOM_CLIENT_ID", client_id),
            ("ZOOM_CLIENT_SECRET", client_secret),
        )
        if not value
    ]
    if missing:
        missing_list = ", ".join(missing)
        raise MissingCredentialsError(
            "Missing Zoom credentials in environment: " + missing_list
        )
    assert account_id is not None
    assert client_id is not None
    assert client_secret is not None
    return {
        "account_id": account_id,
        "client_id": client_id,
        "client_secret": client_secret,
    }


def _double_urlencode(value: str) -> str:
    """
    Percent-encode ``value`` twice so it is safe for Zoom path parameters.

    Parameters:
        value (str): Raw UUID or token fragment that may require double encoding.

    Returns:
        str: The input value after two rounds of percent-encoding.
    """
    once = quote(value, safe="")
    return quote(once, safe="")


def _encode_uuid(uuid: str) -> str:
    """
    Percent-encode a meeting UUID, double-encoding when Zoom requires it.

    Zoom mandates double encoding for UUIDs that either start with ``/`` or
    contain ``//``. Other UUIDs must only be encoded once to remain valid.

    Parameters:
        uuid (str): Meeting UUID returned by the Zoom API.

    Returns:
        str: URL-safe representation suitable for path parameters.
    """
    if uuid.startswith("/") or "//" in uuid:
        return _double_urlencode(uuid)
    return quote(uuid, safe="")


class ZoomAPIClient:
    """Client wrapper around the Zoom REST API tailored for recording downloads."""

    def __init__(
        self,
        *,
        account_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str = "https://api.zoom.us/v2",
        token_url: str = "https://zoom.us/oauth/token",
        session: requests.Session | None = None,
        access_token: str | None = None,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        timeout: float | tuple[float, float] | None = 10.0,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the client with credentials, HTTP settings, and logging."""
        self.account_id = account_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/") + "/"
        self.token_url = token_url
        self.session = session or requests.Session()
        self.max_retries = max(0, max_retries)
        self.backoff_factor = max(0.0, backoff_factor)
        self.timeout = self._validate_timeout(timeout)
        self._access_token = access_token
        self._token_expiry: float | None = None
        self.logger = logger or _LOGGER

    @classmethod
    def from_env(
        cls,
        *,
        dotenv_path: str | None = None,
        **overrides: Any,
    ) -> ZoomAPIClient:
        """Create a client using environment credentials with optional overrides."""
        env_credentials = load_env_credentials(dotenv_path)
        return cls(**env_credentials, **overrides)

    def list_recordings(
        self,
        *,
        start: datetime,
        end: datetime,
        host_email: str | None = None,
        meeting_id: str | None = None,
        page_size: int = 100,
    ) -> list[Recording]:
        """Return recordings for the account within the provided date range."""
        start_utc = ensure_utc(start)
        end_utc = ensure_utc(end)
        if end_utc < start_utc:
            raise ValueError("end must be greater than or equal to start")

        log_params = {
            "from": start_utc.strftime("%Y-%m-%d"),
            "to": end_utc.strftime("%Y-%m-%d"),
            "page_size": page_size,
            "host_email_redacted": redact_identifier(host_email),
            "meeting_id_redacted": redact_identifier(meeting_id),
            "has_host_email": bool(host_email),
            "has_meeting_id": bool(meeting_id),
        }

        if meeting_id:
            recordings = self._list_recordings_for_meeting(
                meeting_id=meeting_id,
                start=start_utc,
                end=end_utc,
                host_email=host_email,
            )
        else:
            recordings = self._list_user_recordings(
                start=start_utc,
                end=end_utc,
                host_email=host_email,
                page_size=page_size,
            )

        self.logger.info(
            "zoom.list_recordings.completed",
            extra={"count": len(recordings), "params": log_params},
        )
        return recordings

    def _list_user_recordings(
        self,
        *,
        start: datetime,
        end: datetime,
        host_email: str | None,
        page_size: int,
    ) -> list[Recording]:
        """List user recordings within the supplied date range."""
        params: dict[str, str] = {
            "from": start.strftime("%Y-%m-%d"),
            "to": end.strftime("%Y-%m-%d"),
            "page_size": str(page_size),
            "include_fields": "download_access_token",
        }
        path = "users/me/recordings"
        if host_email:
            encoded_host_email = quote(host_email, safe="")
            path = f"users/{encoded_host_email}/recordings"

        recordings: list[Recording] = []
        next_page_token: str | None = None

        while True:
            request_params = dict(params)
            if next_page_token:
                request_params["next_page_token"] = next_page_token
            response = self._request("GET", path, params=request_params)
            payload = response.json()
            meetings = payload.get("meetings") or []
            for meeting in meetings:
                recordings.append(Recording.from_api(meeting))
            next_page_token = payload.get("next_page_token") or None
            if not next_page_token:
                break

        return recordings

    def _list_recordings_for_meeting(
        self,
        *,
        meeting_id: str,
        start: datetime,
        end: datetime,
        host_email: str | None,
    ) -> list[Recording]:
        """Collect meeting recordings within the window and optional host filter."""
        encoded_meeting_id = _encode_uuid(meeting_id)
        meetings: list[dict[str, Any]] = []
        try:
            response = self._request(
                "GET", f"past_meetings/{encoded_meeting_id}/instances"
            )
        except requests.HTTPError as exc:
            resp = getattr(exc, "response", None)
            status_code = getattr(resp, "status_code", None)
            if status_code != 404:
                raise
        else:
            payload = response.json()
            meetings = payload.get("meetings") or []

        recordings: list[Recording] = []
        seen_uuids: set[str] = set()
        instance_uuids: list[str] = []

        for meeting in meetings:
            uuid = meeting.get("uuid")
            if not uuid or uuid in seen_uuids:
                continue
            seen_uuids.add(uuid)
            instance_uuids.append(uuid)

        if not instance_uuids:
            instance_uuids = [meeting_id]

        for uuid in instance_uuids:
            try:
                meeting_payload = self._fetch_meeting_recording(uuid)
            except requests.HTTPError as exc:
                response = getattr(exc, "response", None)
                status_code = getattr(response, "status_code", None)
                if status_code == 404:
                    self.logger.info(
                        "zoom.list_recordings.missing_instance",
                        extra={"uuid_redacted": redact_uuid(uuid)},
                    )
                    continue
                raise
            if not meeting_payload.get("recording_files"):
                continue
            recording = Recording.from_api(meeting_payload)
            if recording.start_time < start or recording.start_time > end:
                continue
            if host_email and recording.host_email.lower() != host_email.lower():
                continue
            recordings.append(recording)

        return recordings

    def _fetch_meeting_recording(self, uuid: str) -> dict:
        """Fetch the recordings payload for a specific meeting UUID."""
        path = f"meetings/{_encode_uuid(uuid)}/recordings"
        response = self._request(
            "GET",
            path,
            params={"include_fields": "download_access_token"},
        )
        return response.json()

    def download_file(
        self,
        *,
        url: str,
        access_token: str | None = None,
        timeout: float | tuple[float, float] | None = None,
    ) -> bytes:
        """Download a file, appending the access token when provided."""
        self._ensure_access_token()
        request_url = url
        if access_token:
            separator = "&" if "?" in url else "?"
            encoded_token = quote(access_token, safe="")
            request_url = f"{url}{separator}access_token={encoded_token}"
        effective_timeout = (
            self.timeout if timeout is None else self._validate_timeout(timeout)
        )
        headers = self._headers()
        headers["Accept"] = "*/*"
        headers.pop("Content-Type", None)

        response = self.session.get(
            request_url,
            headers=headers,
            timeout=effective_timeout,
            stream=True,
        )
        response.raise_for_status()
        if hasattr(response, "iter_content"):
            chunks = []
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    chunks.append(chunk)
            content = b"".join(chunks)
        else:
            content = response.content
        close = getattr(response, "close", None)
        if callable(close):
            close()
        self.logger.debug(
            "zoom.download_file.success",
            extra={
                "bytes": len(content),
                "included_access_token": bool(access_token),
            },
        )
        return content

    def download_recording_file(self, recording_file: RecordingFile) -> bytes:
        """Download bytes for a RecordingFile-like object."""
        access_token = recording_file.download_access_token
        return self.download_file(
            url=recording_file.download_url, access_token=access_token
        )

    def _headers(self) -> dict[str, str]:
        """Return standard JSON headers including the bearer token."""
        self._ensure_access_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _ensure_access_token(self) -> None:
        """Ensure a valid OAuth access token is cached or fetch a new one."""
        skew = 60.0  # seconds
        token_expired = bool(
            self._token_expiry is not None
            and (self._token_expiry - skew) <= time.time()
        )
        if self._access_token and not token_expired:
            return
        if self._access_token and self._token_expiry is None:
            return
        if not all([self.account_id, self.client_id, self.client_secret]):
            if self._access_token and token_expired:
                raise TokenRefreshError(
                    "Cannot refresh access token without OAuth credentials"
                )
            raise MissingCredentialsError("OAuth credentials are required")

        self.logger.debug(
            "zoom.auth.request_token", extra={"token_url": self.token_url}
        )
        assert self.client_id is not None
        assert self.client_secret is not None
        assert self.account_id is not None
        response = self.session.post(
            self.token_url,
            data={"grant_type": "account_credentials", "account_id": self.account_id},
            auth=(self.client_id, self.client_secret),
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        self._access_token = payload["access_token"]
        expires_in = payload.get("expires_in")
        self._token_expiry = time.time() + float(expires_in) if expires_in else None
        self.logger.info(
            "zoom.auth.token_acquired",
            extra={"expires_in": expires_in, "has_expiry": bool(expires_in)},
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, str] | None = None,
        timeout: float | tuple[float, float] | None = None,
    ):
        """Send an HTTP request and retry automatically on HTTP 429 responses."""
        self._ensure_access_token()
        url = urljoin(self.base_url, path)
        attempt = 0
        effective_timeout = (
            self.timeout if timeout is None else self._validate_timeout(timeout)
        )
        safe_path = self._path_template_for_log(path)
        auth_refreshed = False
        while True:
            self.logger.debug(
                "zoom.request.dispatch",
                extra={"method": method, "path": safe_path, "attempt": attempt},
            )
            response = self.session.request(
                method,
                url,
                params=params,
                json=json,
                headers=self._headers(),
                timeout=effective_timeout,
            )
            if response.status_code == 429 and attempt < self.max_retries:
                delay = self._compute_backoff(attempt, response)
                self.logger.warning(
                    "zoom.request.rate_limited",
                    extra={
                        "path": safe_path,
                        "retry_after": delay,
                        "attempt": attempt,
                    },
                )
                time.sleep(delay)
                attempt += 1
                continue
            if response.status_code == 401 and not auth_refreshed:
                response.close()
                self.logger.info(
                    "zoom.request.unauthorized_retry",
                    extra={"path": safe_path},
                )
                self._access_token = None
                self._token_expiry = 0.0
                self._ensure_access_token()
                auth_refreshed = True
                continue
            response.raise_for_status()
            return response

    @staticmethod
    def _path_template_for_log(path: str) -> str:
        """Return a path string with identifiers masked for logging."""
        sensitive_containers = {"users", "meetings", "past_meetings"}
        masked_segments: list[str] = []
        mask_next = False
        path_only, _, _ = path.partition("?")
        for segment in path_only.split("/"):
            if mask_next and segment:
                masked_segments.append(":id")
                mask_next = False
                continue
            masked_segments.append(segment)
            mask_next = segment in sensitive_containers
        return "/".join(masked_segments)

    @staticmethod
    def _validate_timeout(
        timeout: float | tuple[float, float] | None,
    ) -> float | tuple[float, float] | None:
        """Ensure a timeout is either ``None``, a non-negative float, or tuple."""
        if timeout is None:
            return None
        if isinstance(timeout, bool):
            raise TypeError("Timeout must be a float, tuple, or None")
        if isinstance(timeout, (int, float)):
            numeric_timeout = float(timeout)
            if numeric_timeout < 0:
                raise ValueError("Timeout must be non-negative")
            return numeric_timeout
        if isinstance(timeout, tuple):
            if len(timeout) != 2:
                raise ValueError("Timeout tuple must contain exactly two values")
            connect, read = timeout
            normalized: list[float] = []
            for component in (connect, read):
                if isinstance(component, bool) or not isinstance(
                    component, (int, float)
                ):
                    raise TypeError("Timeout tuple values must be numeric")
                numeric_component = float(component)
                if numeric_component < 0:
                    raise ValueError("Timeout values must be non-negative")
                normalized.append(numeric_component)
            return (normalized[0], normalized[1])
        raise TypeError("Timeout must be a float, tuple, or None")

    def _compute_backoff(self, attempt: int, response) -> float:
        """Compute the retry delay using Retry-After or exponential backoff."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        jitter = random.uniform(0.5, 1.5)
        return self.backoff_factor * (2**attempt) * jitter


__all__ = [
    "MissingCredentialsError",
    "TokenRefreshError",
    "ZoomAPIClient",
    "load_env_credentials",
]
