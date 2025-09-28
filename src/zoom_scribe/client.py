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
        """
        Return the provided dotenv path or an empty string.
        
        Parameters:
            dotenv_path (str | None): Path to a .env file. If None, an empty string is returned.
            raise_error_if_not_found (bool): Ignored by this implementation; kept for API compatibility.
        
        Returns:
            str: The given dotenv path, or an empty string when no path was provided.
        """
        return dotenv_path or ""

    def load_dotenv(*args, **kwargs):
        """
        No-op placeholder for loading a .env file when python-dotenv is not available.
        
        All positional and keyword arguments are ignored. This function always returns False to indicate that no .env file was loaded.
        """
        return False


from .models import Recording

_LOGGER = logging.getLogger(__name__)


def load_env_credentials(dotenv_path: str | None = None) -> dict[str, str]:
    """
    Load Zoom OAuth credentials from a .env file or the environment.
    
    If a dotenv path is provided and found, the file is loaded (without overriding existing environment variables).
    Parameters:
        dotenv_path (str | None): Optional path or pattern for a .env file to load.
    
    Returns:
        dict[str, str]: Mapping with keys `account_id`, `client_id`, and `client_secret` containing the corresponding environment values.
    
    Raises:
        RuntimeError: If any of ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, or ZOOM_CLIENT_SECRET are missing from the environment.
    """
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
    """
    Percent-encode the input string twice for safe inclusion in URLs.
    
    Parameters:
        value (str): The string to be percent-encoded.
    
    Returns:
        double_urlencoded (str): The input string after two rounds of URL percent-encoding.
    """
    once = quote(value, safe="")
    return quote(once, safe="")


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
        """
        Initialize the ZoomAPIClient with credentials, HTTP settings, and retry configuration.
        
        Parameters:
            account_id (str | None): Zoom account ID used for the account_credentials grant.
            client_id (str | None): OAuth client ID for token requests.
            client_secret (str | None): OAuth client secret for token requests.
            base_url (str): Base API URL; trailing slashes are normalized.
            token_url (str): OAuth token endpoint URL.
            session (requests.Session | None): Optional requests.Session to use for HTTP calls; a new Session is created if omitted.
            access_token (str | None): Optional pre-obtained OAuth access token to seed the client.
            max_retries (int): Maximum number of retries for rate-limited requests; negative values are clamped to 0.
            backoff_factor (float): Base backoff multiplier for retry delays; negative values are clamped to 0.0.
            logger (logging.Logger | None): Logger to use; a module-level default logger is used if omitted.
        """
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
        """
        Create a ZoomAPIClient using credentials loaded from the environment or a .env file, with optional overrides applied.
        
        Parameters:
            dotenv_path (str | None): Path to a .env file to load before reading environment variables. If `None`, the environment is used as-is.
            overrides: Keyword arguments that override or extend the loaded credentials (for example `account_id`, `client_id`, `client_secret`, or other constructor parameters).
        
        Returns:
            ZoomAPIClient: An instance configured with the merged environment credentials and overrides.
        """
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
        """
        Retrieve recordings for the authenticated account within a date range.
        
        If `meeting_id` is provided, results are limited to that meeting; otherwise recordings across the account are returned. Results can be restricted to a specific host and the `page_size` controls page size when listing user recordings.
        
        Parameters:
            start (datetime): Inclusive start of the date range to search.
            end (datetime): Inclusive end of the date range to search.
            host_email (str | None): Optional host email to filter recordings by owner.
            meeting_id (str | None): Optional meeting identifier to limit results to a single meeting.
            page_size (int): Maximum number of items per page when listing user recordings.
        
        Returns:
            list[Recording]: Recordings that match the supplied criteria.
        """
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
        """
        Retrieve recordings for a user account within a date range.
        
        Parameters:
            start (datetime): Start date for the query (used as the "from" date).
            end (datetime): End date for the query (used as the "to" date).
            host_email (str | None): If provided, fetch recordings for this user's email; otherwise fetch for the authenticated account.
            page_size (int): Number of records to request per page from the API.
        
        Returns:
            list[Recording]: List of Recording objects collected from all pages within the specified date range. 
        """
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
        """
        Collect recordings for a specific meeting ID within a time window, optionally filtered by host email.
        
        Parameters:
            meeting_id (str): Meeting identifier to query for instances and recordings.
            start (datetime): Inclusive lower bound for recording start time.
            end (datetime): Inclusive upper bound for recording start time.
            host_email (str | None): If provided, only include recordings whose host email matches (case-insensitive).
        
        Returns:
            list[Recording]: Recording objects whose start_time falls between `start` and `end` and, if `host_email` is set, whose host matches.
        """
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
            meeting_payload = self._fetch_meeting_recording(uuid)
            if not meeting_payload:
                continue
            recording = Recording.from_api(meeting_payload)
            if recording.start_time < start or recording.start_time > end:
                continue
            if host_email and recording.host_email.lower() != host_email.lower():
                continue
            recordings.append(recording)

        return recordings

    def _fetch_meeting_recording(self, uuid: str) -> dict:
        """
        Retrieve the recordings payload for a meeting UUID from the Zoom API.
        
        Requests the meeting's recording metadata and includes download access tokens when available.
        
        Parameters:
            uuid (str): Meeting or meeting-instance UUID to fetch recordings for.
        
        Returns:
            dict: JSON payload from the Zoom recordings endpoint containing recording metadata (includes `download_access_token` when provided).
        """
        path = f"meetings/{_double_urlencode(uuid)}/recordings"
        response = self._request(
            "GET",
            path,
            params={"include_fields": "download_access_token"},
        )
        return response.json()

    def download_file(self, *, url: str, access_token: str | None = None) -> bytes:
        """
        Download a Zoom-hosted file, optionally appending a download access token to the URL.
        
        Parameters:
            url (str): The file URL to download.
            access_token (str | None): Optional download access token to append as a query parameter.
        
        Returns:
            bytes: The raw response content of the downloaded file.
        """
        self._ensure_access_token()
        request_url = url
        if access_token:
            separator = "&" if "?" in url else "?"
            request_url = f"{url}{separator}access_token={access_token}"
        response = self.session.get(request_url, headers=self._headers(), stream=True)
        response.raise_for_status()
        self.logger.debug("zoom.download_file.success", extra={"url": url})
        return response.content

    def download_recording_file(self, recording_file) -> bytes:
        """
        Download the bytes of a recording represented by a RecordingFile-like object.
        
        Parameters:
            recording_file: An object exposing `download_url` (str) and `download_access_token` (str|None); the URL and optional token used to fetch the file.
        
        Returns:
            bytes: The raw bytes of the downloaded recording.
        """
        access_token = recording_file.download_access_token
        return self.download_file(
            url=recording_file.download_url, access_token=access_token
        )

    def _headers(self) -> dict[str, str]:
        """
        Build HTTP headers including an Authorization bearer token for API requests.
        
        Ensures a valid access token is present and returns a mapping of headers required for JSON API requests.
        
        Returns:
            dict[str, str]: Headers containing an `Authorization: Bearer <token>` entry and `Content-Type`/`Accept` set to `application/json`.
        """
        self._ensure_access_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _ensure_access_token(self) -> None:
        """
        Ensure a valid OAuth access token is available for API requests.
        
        If a cached token exists and is not expired (or has no expiry), this is a no-op. Otherwise the client requests a new token from `self.token_url` using the account credentials grant and stores `self._access_token` and `self._token_expiry` (set to current time plus `expires_in` when present, otherwise `None`). 
        
        Raises:
            RuntimeError: If OAuth credentials are missing and no cached access token exists.
            requests.HTTPError: If the token request returns a non-successful HTTP status.
        """
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
        """
        Send an HTTP request to the Zoom API and automatically retry on 429 (rate-limit) responses.
        
        Retries use the client's retry configuration and backoff calculation when the server returns 429; other HTTP errors are raised.
        
        Returns:
            requests.Response: The successful HTTP response.
        
        Raises:
            requests.HTTPError: If the final response has an HTTP error status.
        """
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
        """
        Compute the delay in seconds to wait before the next retry after receiving an HTTP 429 response.
        
        Uses the `Retry-After` response header if present and parseable as a float; otherwise computes exponential backoff as `backoff_factor * (2**attempt)`.
        
        Parameters:
            attempt (int): The retry attempt index (0 for the first retry).
            response: The HTTP response object containing headers (e.g., `requests.Response`).
        
        Returns:
            float: Delay in seconds to wait before retrying.
        """
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return self.backoff_factor * (2**attempt)


__all__ = ["ZoomAPIClient", "load_env_credentials"]
