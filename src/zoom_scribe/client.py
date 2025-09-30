"""HTTP client for Zoom APIs with retries, logging, and typed responses."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Final, cast
from urllib.parse import quote, urljoin, urlparse

import requests

from ._datetime import ensure_utc
from ._redact import redact_identifier, redact_uuid
from .config import ConfigurationError, load_oauth_credentials
from .models import ModelValidationError, Recording, RecordingFile, RecordingPage

_LOGGER = logging.getLogger(__name__)

REQUEST_ID_HEADER: Final = "x-zm-trackingid"
RETRY_AFTER_HEADER: Final = "Retry-After"
RATE_LIMIT_TYPE_HEADER: Final = "x-ratelimit-type"
RATE_LIMIT_REMAINING_HEADER: Final = "x-ratelimit-remaining"
DEFAULT_TIMEOUT: Final[float] = 10.0
RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
HTTP_STATUS_MULTIPLE_CHOICES: Final[int] = 300
HTTP_STATUS_BAD_REQUEST: Final[int] = 400
HTTP_STATUS_UNAUTHORIZED: Final[int] = 401
HTTP_STATUS_NOT_FOUND: Final[int] = 404
HTTP_STATUS_TOO_MANY_REQUESTS: Final[int] = 429
HTTP_STATUS_SERVER_ERROR: Final[int] = 500
TIMEOUT_COMPONENTS: Final[int] = 2

Timeout = float | tuple[float, float] | None
JsonMapping = Mapping[str, Any]


class ZoomAPIError(RuntimeError):
    """Base class for Zoom API failures with structured context."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        request_id: str | None = None,
        retry_after: float | None = None,
        error_code: str | None = None,
        details: Any | None = None,
    ) -> None:
        """Initialise the error message and capture relevant context."""
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after = retry_after
        self.error_code = error_code
        self.details = details

    def context(self) -> dict[str, Any]:
        """Return error metadata useful for logging."""
        return {
            "status_code": self.status_code,
            "request_id": self.request_id,
            "retry_after": self.retry_after,
            "error_code": self.error_code,
            "details": self.details,
        }


class ZoomAuthError(ZoomAPIError):
    """Raised when authentication against the Zoom API fails."""


class ZoomNotFoundError(ZoomAPIError):
    """Raised when a requested Zoom resource cannot be found."""


class ZoomRateLimitError(ZoomAPIError):
    """Raised when the Zoom API reports a rate limit violation."""


class ZoomRetryableError(ZoomAPIError):
    """Raised for transient errors that may succeed on retry."""


class MissingCredentialsError(ConfigurationError):
    """Raised when required OAuth credentials are missing."""


class TokenRefreshError(RuntimeError):
    """Raised when an access token cannot be refreshed."""


def load_env_credentials(dotenv_path: str | None = None) -> dict[str, str]:
    """Load Zoom OAuth credentials from the environment into a plain dictionary."""
    try:
        credentials = load_oauth_credentials(dotenv_path=dotenv_path)
    except ConfigurationError as exc:  # pragma: no cover - translated in tests
        raise MissingCredentialsError(str(exc)) from exc
    return dict(credentials.to_dict())


def _double_urlencode(value: str) -> str:
    """Percent-encode ``value`` twice so it is safe for Zoom path parameters."""
    once = quote(value, safe="")
    return quote(once, safe="")


def _encode_uuid(uuid: str) -> str:
    """Percent-encode a meeting UUID, double-encoding when Zoom requires it."""
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
        timeout: Timeout = DEFAULT_TIMEOUT,
        logger: logging.Logger | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        retry_status_codes: frozenset[int] | None = None,
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
        self.retry_status_codes = retry_status_codes or RETRYABLE_STATUS_CODES
        self.logger = logger or _LOGGER
        self._access_token = access_token
        self._token_expiry: float | None = None
        self._clock = clock or time.time
        self._sleep = sleeper or time.sleep
        self._lock = threading.RLock()

    @classmethod
    def from_env(
        cls,
        *,
        dotenv_path: str | None = None,
        **overrides: Any,
    ) -> ZoomAPIClient:
        """Create a client using environment credentials with optional overrides."""
        env_credentials = cast(dict[str, Any], load_env_credentials(dotenv_path))
        initial_kwargs = {**env_credentials, **overrides}
        return cls(**initial_kwargs)

    def list_recordings(
        self,
        *,
        start: datetime,
        end: datetime,
        host_email: str | None = None,
        meeting_id: str | None = None,
        page_size: int = 100,
    ) -> list[Recording]:
        """Return recordings for the account within the provided date range.

        Args:
            start: Inclusive start datetime in UTC.
            end: Inclusive end datetime in UTC.
            host_email: Optional host email filter applied server-side.
            meeting_id: Optional meeting UUID to restrict the search.
            page_size: Number of results per page (Zoom maximum 300).

        Returns:
            List of :class:`Recording` models matching the supplied criteria.

        Raises:
            ValueError: If ``end`` is earlier than ``start``.
        """
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
        effective_page_size = max(1, min(int(page_size), 300))
        params: dict[str, str] = {
            "from": start.strftime("%Y-%m-%d"),
            "to": end.strftime("%Y-%m-%d"),
            "page_size": str(effective_page_size),
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
            payload = self._request_json("GET", path, params=request_params)
            page = RecordingPage.from_api(payload)
            recordings.extend(page.recordings)
            next_page_token = page.next_page_token
            if not page.has_next_page():
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
        try:
            response = self._request_json("GET", f"past_meetings/{encoded_meeting_id}/instances")
        except ZoomNotFoundError:
            response = {"meetings": []}

        instance_payloads = response.get("meetings") or []
        seen_uuids: set[str] = set()
        instance_uuids: list[str] = []

        for meeting in instance_payloads:
            uuid = meeting.get("uuid")
            if not uuid or uuid in seen_uuids:
                continue
            seen_uuids.add(uuid)
            instance_uuids.append(uuid)

        if not instance_uuids:
            instance_uuids = [meeting_id]

        recordings: list[Recording] = []
        for uuid in instance_uuids:
            try:
                recording = self._fetch_meeting_recording(uuid)
            except ZoomNotFoundError:
                self.logger.info(
                    "zoom.list_recordings.missing_instance",
                    extra={"uuid_redacted": redact_uuid(uuid)},
                )
                continue
            if recording.start_time < start or recording.start_time > end:
                continue
            if host_email and recording.host_email.lower() != host_email.lower():
                continue
            recordings.append(recording)

        return recordings

    def _fetch_meeting_recording(self, uuid: str) -> Recording:
        """Fetch the recordings payload for a specific meeting UUID."""
        path = f"meetings/{_encode_uuid(uuid)}/recordings"
        payload = self._request_json(
            "GET",
            path,
            params={"include_fields": "download_access_token"},
        )
        try:
            return Recording.from_api(payload)
        except ModelValidationError as exc:  # pragma: no cover - defensive
            raise ZoomAPIError(
                f"Invalid recording payload for meeting {redact_uuid(uuid)}",
                status_code=200,
                details=str(exc),
            ) from exc

    def download_file(
        self,
        *,
        url: str,
        access_token: str | None = None,
        timeout: Timeout = None,
    ) -> bytes:
        """Download a file, appending the access token when provided."""
        self._ensure_access_token()
        request_url = url
        if access_token:
            separator = "&" if "?" in url else "?"
            encoded_token = quote(access_token, safe="")
            request_url = f"{url}{separator}access_token={encoded_token}"
        effective_timeout = self.timeout if timeout is None else self._validate_timeout(timeout)

        # Enforce Zoom host allowlist to reduce SSRF risk when URLs come from the API.
        host = (urlparse(request_url).hostname or "").lower()
        if host not in {"zoom.us"} and not host.endswith(".zoom.us"):
            raise ValueError(f"Refusing to download from non-Zoom host: {host}")

        response = self._request(
            "GET",
            request_url,
            timeout=effective_timeout,
            stream=True,
            include_authorization=True,
            extra_headers={"Accept": "*/*", "Content-Type": None},
            allow_redirects=False,
        )
        if HTTP_STATUS_MULTIPLE_CHOICES <= response.status_code < HTTP_STATUS_BAD_REQUEST:
            # Redact sensitive query parameters from Location before attaching to error details
            location_raw = response.headers.get("Location")
            # Redact query to avoid leaking tokens in logs/errors
            try:
                parsed = urlparse(location_raw) if location_raw else None
                location = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed else None
            except Exception:
                location = None
            response.close()
            raise ZoomAPIError(
                "Zoom download responded with redirect; refusing to follow",
                status_code=response.status_code,
                details=location,
            )
        # Read streamed content in a way that always closes the response on error
        content: list[bytes] = []
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    content.append(chunk)  # noqa: PERF401
        finally:
            response.close()

        self.logger.info(
            "zoom.download_file.success",
            extra={
                "bytes": sum(len(chunk) for chunk in content),
                "included_access_token": bool(access_token),
            },
        )
        return b"".join(content)

    def download_recording_file(self, recording_file: RecordingFile) -> bytes:
        """Download bytes for a :class:`RecordingFile` instance."""
        access_token = recording_file.download_access_token
        return self.download_file(url=recording_file.download_url, access_token=access_token)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        timeout: Timeout = None,
        stream: bool = False,
        include_authorization: bool = True,
        extra_headers: Mapping[str, Any] | None = None,
        allow_redirects: bool = True,
    ) -> requests.Response:
        """Send an HTTP request with Zoom-specific retry and error handling."""
        self._ensure_access_token()
        url = urljoin(self.base_url, path)
        attempt = 0
        effective_timeout = self.timeout if timeout is None else self._validate_timeout(timeout)
        safe_path = self._path_template_for_log(path)
        auth_refreshed = False

        while True:
            headers = dict(self._headers() if include_authorization else {})
            if extra_headers:
                for header_name, header_value in extra_headers.items():
                    if header_value is None:
                        headers.pop(str(header_name), None)
                    else:
                        headers[str(header_name)] = str(header_value)
            self.logger.debug(
                "zoom.request.dispatch",
                extra={
                    "method": method,
                    "path": safe_path,
                    "attempt": attempt,
                    "timeout": effective_timeout,
                },
            )
            with self._lock:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                    timeout=effective_timeout,
                    stream=stream,
                    allow_redirects=allow_redirects,
                )
            status_code = response.status_code
            request_id = response.headers.get(REQUEST_ID_HEADER)

            if (
                status_code == HTTP_STATUS_UNAUTHORIZED
                and not auth_refreshed
                and include_authorization
            ):
                response.close()
                self.logger.info(
                    "zoom.request.unauthorized_retry",
                    extra={"path": safe_path, "request_id": request_id},
                )
                with self._lock:
                    self._access_token = None
                    self._token_expiry = 0.0
                self._ensure_access_token()
                auth_refreshed = True
                continue

            if status_code in self.retry_status_codes and attempt < self.max_retries:
                delay = self._retry_delay(attempt, response)
                response.close()
                self.logger.warning(
                    "zoom.request.retry",
                    extra={
                        "path": safe_path,
                        "status_code": status_code,
                        "retry_after": delay,
                        "attempt": attempt,
                        "request_id": request_id,
                    },
                )
                self._sleep(delay)
                attempt += 1
                continue

            if status_code >= HTTP_STATUS_BAD_REQUEST:
                try:
                    self._raise_api_error(response, safe_path, attempt)
                finally:
                    response.close()
            return response

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        timeout: Timeout = None,
    ) -> JsonMapping:
        """Send a request and return the decoded JSON payload."""
        response = self._request(
            method,
            path,
            params=params,
            json=json,
            timeout=timeout,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            request_id = response.headers.get(REQUEST_ID_HEADER)
            response.close()
            raise ZoomAPIError(
                "Zoom API returned invalid JSON",
                status_code=response.status_code,
                request_id=request_id,
            ) from exc
        if not isinstance(payload, Mapping):
            request_id = response.headers.get(REQUEST_ID_HEADER)
            response.close()
            raise ZoomAPIError(
                "Zoom API returned an unexpected payload shape",
                status_code=response.status_code,
                request_id=request_id,
                details=payload,
            )
        response.close()
        return payload

    def _retry_delay(self, attempt: int, response: requests.Response) -> float:
        """Compute the retry delay using Retry-After or exponential backoff."""
        retry_after_header = response.headers.get(RETRY_AFTER_HEADER)
        if retry_after_header:
            try:
                retry_after_value = float(retry_after_header)
                if retry_after_value >= 0:
                    return retry_after_value
            except ValueError:  # pragma: no cover - defensive
                pass
        jitter = random.uniform(0.5, 1.5)
        return float(self.backoff_factor * (2**attempt) * jitter)

    def _raise_api_error(
        self,
        response: requests.Response,
        path: str,
        attempt: int,
    ) -> None:
        """Normalize HTTP errors into rich Zoom-specific exceptions."""
        status_code = response.status_code
        request_id = response.headers.get(REQUEST_ID_HEADER)
        retry_after_header = response.headers.get(RETRY_AFTER_HEADER)
        retry_after = None
        if retry_after_header:
            try:
                parsed = float(retry_after_header)
                if parsed >= 0:
                    retry_after = parsed
            except ValueError:  # pragma: no cover - defensive
                retry_after = None
        rate_limit_type = response.headers.get(RATE_LIMIT_TYPE_HEADER)
        rate_limit_remaining = response.headers.get(RATE_LIMIT_REMAINING_HEADER)

        payload: JsonMapping | None = None
        message = response.reason or f"HTTP {status_code}"
        error_code: str | None = None
        details: Any | None = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, Mapping):
            payload_message = payload.get("message")
            if payload_message:
                message = str(payload_message)
            error_code_raw = payload.get("code") or payload.get("error_code")
            if error_code_raw is not None:
                error_code = str(error_code_raw)
            detail_value = payload.get("details") or payload.get("errors")
            if detail_value:
                details = detail_value

        extra = {
            "path": path,
            "status_code": status_code,
            "request_id": request_id,
            "retry_after": retry_after,
            "rate_limit_type": rate_limit_type,
            "rate_limit_remaining": rate_limit_remaining,
            "attempt": attempt,
            "error_code": error_code,
        }
        self.logger.error("zoom.request.error", extra=extra)

        def _raise(exception_type: type[ZoomAPIError]) -> None:
            raise exception_type(
                message,
                status_code=status_code,
                request_id=request_id,
                retry_after=retry_after,
                error_code=error_code,
                details=details,
            )

        if status_code == HTTP_STATUS_UNAUTHORIZED:
            _raise(ZoomAuthError)
        elif status_code == HTTP_STATUS_NOT_FOUND:
            _raise(ZoomNotFoundError)
        elif status_code == HTTP_STATUS_TOO_MANY_REQUESTS:
            _raise(ZoomRateLimitError)
        elif status_code >= HTTP_STATUS_SERVER_ERROR:
            _raise(ZoomRetryableError)
        else:
            _raise(ZoomAPIError)

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
        with self._lock:
            if self._access_token and self._token_expiry is None:
                return
            skew = 60.0  # seconds
            token_expired = bool(
                self._token_expiry is not None and (self._token_expiry - skew) <= self._clock()
            )
            if self._access_token and not token_expired:
                return
            if not all([self.account_id, self.client_id, self.client_secret]):
                if self._access_token and token_expired:
                    raise TokenRefreshError("Cannot refresh access token without OAuth credentials")
                raise MissingCredentialsError("OAuth credentials are required")

            assert self.client_id is not None
            assert self.client_secret is not None
            assert self.account_id is not None
            self.logger.debug("zoom.auth.request_token", extra={"token_url": self.token_url})
            response = self.session.post(
                self.token_url,
                data={"grant_type": "account_credentials", "account_id": self.account_id},
                auth=(self.client_id, self.client_secret),
                timeout=self.timeout,
            )
            if response.status_code >= HTTP_STATUS_BAD_REQUEST:
                response.close()
                raise ZoomAuthError(
                    "Failed to obtain Zoom access token",
                    status_code=response.status_code,
                    request_id=response.headers.get(REQUEST_ID_HEADER),
                )
            payload = response.json()
            response.close()
            self._access_token = payload["access_token"]
            expires_in = payload.get("expires_in")
            self._token_expiry = self._clock() + float(expires_in) if expires_in else None
            self.logger.info(
                "zoom.auth.token_acquired",
                extra={"expires_in": expires_in, "has_expiry": bool(expires_in)},
            )

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
    def _validate_timeout(timeout: Timeout) -> Timeout:
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
            if len(timeout) != TIMEOUT_COMPONENTS:
                raise ValueError("Timeout tuple must contain exactly two values")
            connect, read = timeout
            normalized: list[float] = []
            for component in (connect, read):
                if isinstance(component, bool) or not isinstance(component, (int, float)):
                    raise TypeError("Timeout tuple values must be numeric")
                numeric_component = float(component)
                if numeric_component < 0:
                    raise ValueError("Timeout values must be non-negative")
                normalized.append(numeric_component)
            return (normalized[0], normalized[1])
        raise TypeError("Timeout must be a float, tuple, or None")

    def _compute_backoff(self, attempt: int, response: requests.Response) -> float:
        """Backward-compatible shim for legacy tests using ``_compute_backoff``."""
        return self._retry_delay(attempt, response)


__all__ = [
    "MissingCredentialsError",
    "TokenRefreshError",
    "ZoomAPIClient",
    "ZoomAPIError",
    "ZoomAuthError",
    "ZoomNotFoundError",
    "ZoomRateLimitError",
    "ZoomRetryableError",
    "load_env_credentials",
]
