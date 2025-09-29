from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from urllib.parse import quote, urljoin

import requests

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:  # pragma: no cover

    def find_dotenv(
        dotenv_path: str | None = None,
        raise_error_if_not_found: bool = False,
    ):
        return dotenv_path or ""

    def load_dotenv(*args, **kwargs):
        return False


from .models import Recording

_LOGGER = logging.getLogger(__name__)


def load_env_credentials(dotenv_path: str | None = None) -> dict[str, str]:
    """Load Zoom OAuth credentials from a .env file or the ambient environment."""
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
        raise RuntimeError("Missing Zoom credentials in environment: " + missing_list)
    return {
        "account_id": account_id,
        "client_id": client_id,
        "client_secret": client_secret,
    }


def _double_urlencode(value: str) -> str:
    """Double URL-encode a string."""
    return quote(quote(value, safe=""), safe="")


def _encode_uuid(uuid: str) -> str:
    """
    URL-encode a meeting UUID.

    The Zoom API requires double encoding for UUIDs that start
    with a / or contain //.
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
        logger: logging.Logger | None = None,
    ) -> None:
        self.account_id = account_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/") + "/"
        self.token_url = token_url
        self.session = session or requests.Session()
        self.max_retries = max(0, max_retries)
        self.backoff_factor = max(0.0, backoff_factor)
        self._access_token = access_token
        self._token_expiry: float | None = None
        self.logger = logger or _LOGGER

    @classmethod
    def from_env(cls, *, dotenv_path: str | None = None, **overrides) -> ZoomAPIClient:
        """Instantiate the client using credentials from environment variables."""
        credentials = load_env_credentials(dotenv_path)
        credentials.update(overrides)
        return cls(**credentials)

    def list_recordings(
        self,
        *,
        start: datetime,
        end: datetime,
        host_email: str | None = None,
        meeting_id: str | None = None,
        page_size: int = 100,
    ) -> list[Recording]:
        """Return the set of recordings available for the authenticated account."""
        log_params = {
            "from": start.strftime("%Y-%m-%d"),
            "to": end.strftime("%Y-%m-%d"),
            "host_email": host_email,
            "meeting_id": meeting_id,
            "page_size": page_size,
        }

        if meeting_id:
            recordings = self._list_recordings_for_meeting(
                meeting_id=meeting_id,
                start=start,
                end=end,
                host_email=host_email,
            )
        else:
            recordings = self._list_user_recordings(
                start=start,
                end=end,
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
        params: dict[str, str] = {
            "from": start.strftime("%Y-%m-%d"),
            "to": end.strftime("%Y-%m-%d"),
            "page_size": str(page_size),
            "include_fields": "download_access_token",
        }
        path = "users/me/recordings"
        if host_email:
            path = f"users/{host_email}/recordings"

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
        response = self._request(
            "GET", f"past_meetings/{meeting_id}/instances"
        )
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
                        extra={"uuid": uuid},
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
        path = f"meetings/{_encode_uuid(uuid)}/recordings"
        response = self._request(
            "GET",
            path,
            params={"include_fields": "download_access_token"},
        )
        return response.json()

    def download_file(self, *, url: str, access_token: str | None = None) -> bytes:
        """Download a Zoom file, appending an access token when provided."""
        self._ensure_access_token()
        request_url = url
        if access_token:
            separator = "&" if "?" in url else "?"
            encoded_token = quote(access_token, safe="")
            request_url = f"{url}{separator}access_token={encoded_token}"
        response = self.session.get(request_url, headers=self._headers(), stream=True)
        response.raise_for_status()
        self.logger.debug("zoom.download_file.success", extra={"url": url})
        return response.content

    def download_recording_file(self, recording_file) -> bytes:
        """Convenience wrapper to download a RecordingFile instance."""
        access_token = recording_file.download_access_token
        return self.download_file(
            url=recording_file.download_url, access_token=access_token
        )

    def _headers(self) -> dict[str, str]:
        """Construct the HTTP headers required for authenticated requests."""
        self._ensure_access_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _ensure_access_token(self) -> None:
        """Fetch a fresh OAuth token when the cached value is missing or expired."""
        if (
            self._access_token
            and self._token_expiry
            and self._token_expiry > time.time()
        ):
            return
        if self._access_token and self._token_expiry is None:
            return
        if not all([self.account_id, self.client_id, self.client_secret]):
            if self._access_token:
                return
            raise RuntimeError(
                "OAuth credentials are required to fetch an access token"
            )

        self.logger.debug(
            "zoom.auth.request_token", extra={"token_url": self.token_url}
        )
        response = self.session.post(
            self.token_url,
            data={"grant_type": "account_credentials", "account_id": self.account_id},
            auth=(self.client_id, self.client_secret),
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
    ):
        """Perform an HTTP request with retry semantics for Zoom rate limits."""
        self._ensure_access_token()
        url = urljoin(self.base_url, path)
        attempt = 0
        while True:
            self.logger.debug(
                "zoom.request.dispatch",
                extra={"method": method, "url": url, "attempt": attempt},
            )
            response = self.session.request(
                method,
                url,
                params=params,
                json=json,
                headers=self._headers(),
            )
            if response.status_code == 429 and attempt < self.max_retries:
                delay = self._compute_backoff(attempt, response)
                self.logger.warning(
                    "zoom.request.rate_limited",
                    extra={"url": url, "retry_after": delay, "attempt": attempt},
                )
                time.sleep(delay)
                attempt += 1
                continue
            response.raise_for_status()
            return response

    def _compute_backoff(self, attempt: int, response) -> float:
        """Determine the next retry delay when receiving an HTTP 429 response."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return self.backoff_factor * (2**attempt)


__all__ = ["ZoomAPIClient", "load_env_credentials"]
