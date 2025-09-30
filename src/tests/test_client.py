import threading
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, NoReturn, Protocol, cast

import pytest
import requests

from zoom_scribe.client import ZoomAPIClient


class StubResponse:
    def __init__(
        self,
        payload: Any,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Initialize the stub response with a payload, status, and headers."""
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = str(payload)
        self.reason = self.headers.get("reason", f"HTTP {status_code}")
        self.request_args: tuple[str, str, dict[str, Any]] | None = None

    def json(self) -> Any:
        """Return the stored payload."""
        return self._payload

    def iter_content(self, chunk_size: int = 8192) -> Iterator[bytes]:  # pragma: no cover
        _ = chunk_size
        if isinstance(self._payload, bytes):
            yield self._payload
        else:
            yield b""

    def close(self) -> None:  # pragma: no cover - needed for client cleanup
        return None


class DummySession:
    def __init__(self, responses: Sequence[StubResponse]) -> None:
        """Prepare the session with queued responses and reset the call log."""
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> StubResponse:
        """Record the request and return the next queued response."""
        self.calls.append((method, url, kwargs))
        response = self._responses.pop(0)
        response.request_args = (method, url, kwargs)
        return response


class ClientFactory(Protocol):
    def __call__(
        self,
        responses: Sequence[StubResponse],
        *,
        sleeper: Callable[[float], None] | None = None,
    ) -> tuple[ZoomAPIClient, DummySession]:
        """Return a configured API client and dummy session."""


@pytest.fixture
def client_factory() -> ClientFactory:
    """Return a factory that wires a ZoomAPIClient to a DummySession."""

    def _factory(
        responses: Sequence[StubResponse],
        *,
        sleeper: Callable[[float], None] | None = None,
    ) -> tuple[ZoomAPIClient, DummySession]:
        session = DummySession(responses)
        client = ZoomAPIClient(
            account_id="account",
            client_id="id",
            client_secret="secret",
            session=cast(requests.Session, session),
            access_token="token-123",
            max_retries=3,
            backoff_factor=0.01,
            sleeper=sleeper,
        )
        return client, session

    return _factory


def make_meeting(uuid: str) -> dict[str, Any]:
    """Create a deterministic meeting payload used by tests."""
    return {
        "uuid": uuid,
        "topic": "Weekly Sync",
        "host_email": "host@example.com",
        "start_time": "2025-09-28T10:00:00Z",
        "recording_files": [
            {
                "id": f"{uuid}-file",
                "file_type": "MP4",
                "file_extension": "mp4",
                "download_url": f"https://zoom.us/download/{uuid}",
                "download_access_token": None,
            }
        ],
    }


def test_list_recordings_returns_recording_models(client_factory: ClientFactory) -> None:
    responses = [
        StubResponse({"meetings": [make_meeting("uuid-1")], "next_page_token": ""}),
    ]
    client, session = client_factory(responses)

    recordings = client.list_recordings(
        start=datetime(2025, 9, 1, tzinfo=UTC),
        end=datetime(2025, 9, 30, tzinfo=UTC),
    )

    assert len(recordings) == 1
    assert recordings[0].uuid == "uuid-1"
    assert session.calls[0][0] == "GET"
    assert "/users/me/recordings" in session.calls[0][1]
    params = session.calls[0][2]["params"]
    assert params["from"] == "2025-09-01"
    assert params["to"] == "2025-09-30"
    assert session.calls[0][2]["timeout"] == 10.0


def test_list_recordings_rejects_naive_datetimes(client_factory: ClientFactory) -> None:
    responses = [
        StubResponse({"meetings": [make_meeting("uuid-1")], "next_page_token": ""}),
    ]
    client, _ = client_factory(responses)

    with pytest.raises(ValueError):
        client.list_recordings(
            start=datetime(2025, 9, 1),  # noqa: DTZ001 - intentional naive datetime
            end=datetime(2025, 9, 30),  # noqa: DTZ001 - intentional naive datetime
        )


def test_list_recordings_paginates_until_next_page_empty(
    client_factory: ClientFactory,
) -> None:
    responses = [
        StubResponse({"meetings": [make_meeting("uuid-1")], "next_page_token": "abc"}),
        StubResponse({"meetings": [make_meeting("uuid-2")], "next_page_token": ""}),
    ]
    client, session = client_factory(responses)

    recordings = client.list_recordings(
        start=datetime(2025, 9, 1, tzinfo=UTC),
        end=datetime(2025, 9, 30, tzinfo=UTC),
    )

    assert [rec.uuid for rec in recordings] == ["uuid-1", "uuid-2"]
    assert session.calls[1][2]["params"]["next_page_token"] == "abc"


def test_list_recordings_retries_on_rate_limit(client_factory: ClientFactory) -> None:
    responses = [
        StubResponse({}, status_code=429, headers={"Retry-After": "1"}),
        StubResponse({"meetings": [make_meeting("uuid-3")], "next_page_token": ""}),
    ]
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        """Record the requested sleep duration for assertions."""
        sleep_calls.append(seconds)

    client, session = client_factory(responses, sleeper=fake_sleep)

    recordings = client.list_recordings(
        start=datetime(2025, 9, 1, tzinfo=UTC),
        end=datetime(2025, 9, 30, tzinfo=UTC),
    )

    assert [rec.uuid for rec in recordings] == ["uuid-3"]
    assert len(session.calls) == 2
    assert sleep_calls, "Expected exponential backoff sleep to be triggered"
    assert session.calls[0][2]["timeout"] == 10.0


def test_list_recordings_enumerates_meeting_instances(
    client_factory: ClientFactory,
) -> None:
    responses = [
        StubResponse(
            {
                "meetings": [
                    {"uuid": "/abc//123"},
                    {"uuid": "plain-uuid"},
                ]
            }
        ),
        StubResponse(make_meeting("/abc//123")),
        StubResponse(make_meeting("plain-uuid")),
    ]
    client, session = client_factory(responses)

    recordings = client.list_recordings(
        start=datetime(2025, 9, 1, tzinfo=UTC),
        end=datetime(2025, 9, 30, tzinfo=UTC),
        meeting_id="123456",
    )

    assert [rec.uuid for rec in recordings] == ["/abc//123", "plain-uuid"]
    assert session.calls[0][1].endswith("past_meetings/123456/instances")
    encoded_path = session.calls[1][1]
    assert "%252Fabc%252F%252F123" in encoded_path
    params = session.calls[1][2]["params"]
    assert params["include_fields"] == "download_access_token"


def test_list_recordings_meeting_id_fallback_to_direct_lookup(
    client_factory: ClientFactory,
) -> None:
    responses = [
        StubResponse({"meetings": []}),
        StubResponse(make_meeting("123456")),
    ]
    client, session = client_factory(responses)

    recordings = client.list_recordings(
        start=datetime(2025, 9, 1, tzinfo=UTC),
        end=datetime(2025, 9, 30, tzinfo=UTC),
        meeting_id="123456",
    )

    assert [rec.uuid for rec in recordings] == ["123456"]
    assert session.calls[1][1].endswith("meetings/123456/recordings")


def test_list_recordings_meeting_id_requires_aware_datetimes(
    client_factory: ClientFactory,
) -> None:
    responses = [
        StubResponse({"meetings": [{"uuid": "uuid-5"}]}),
        StubResponse(make_meeting("uuid-5")),
    ]
    client, _ = client_factory(responses)

    with pytest.raises(ValueError):
        client.list_recordings(
            start=datetime(2025, 9, 1),  # noqa: DTZ001 - intentional naive datetime
            end=datetime(2025, 9, 30),  # noqa: DTZ001 - intentional naive datetime
            meeting_id="meeting-uuid",
        )


def test_list_recordings_meeting_id_honors_host_filter(
    client_factory: ClientFactory,
) -> None:
    responses = [
        StubResponse({"meetings": [{"uuid": "uuid-4"}]}),
        StubResponse(make_meeting("uuid-4")),
    ]
    client, _ = client_factory(responses)

    recordings = client.list_recordings(
        start=datetime(2025, 9, 1, tzinfo=UTC),
        end=datetime(2025, 9, 30, tzinfo=UTC),
        meeting_id="uuid-4",
        host_email="someoneelse@example.com",
    )

    assert recordings == []


def test_list_recordings_meeting_id_with_single_slash_is_not_double_encoded(
    client_factory: ClientFactory,
) -> None:
    """UUIDs with single slashes should be encoded only once."""

    uuid_with_slash = "a/b/c"
    responses = [
        StubResponse({"meetings": [{"uuid": uuid_with_slash}]}),
        StubResponse(make_meeting(uuid_with_slash)),
    ]
    client, session = client_factory(responses)

    client.list_recordings(
        start=datetime(2025, 9, 1, tzinfo=UTC),
        end=datetime(2025, 9, 30, tzinfo=UTC),
        meeting_id="some_meeting_id",
    )

    assert len(session.calls) == 2
    encoded_path = session.calls[1][1]
    assert "meetings/a%2Fb%2Fc/recordings" in encoded_path


def test_list_recordings_skips_missing_instance(client_factory: ClientFactory) -> None:
    responses = [
        StubResponse({"meetings": [{"uuid": "missing"}, {"uuid": "present"}]}),
        StubResponse({}, status_code=404),
        StubResponse(make_meeting("present")),
    ]
    client, session = client_factory(responses)

    recordings = client.list_recordings(
        start=datetime(2025, 9, 1, tzinfo=UTC),
        end=datetime(2025, 9, 30, tzinfo=UTC),
        meeting_id="123456",
    )

    assert [rec.uuid for rec in recordings] == ["present"]
    assert session.calls[1][1].endswith("meetings/missing/recordings")


def test_download_file_encodes_access_token(client_factory: ClientFactory) -> None:
    class StreamResponse(StubResponse):
        def __init__(self) -> None:
            super().__init__(b"ok")

        def json(self) -> Any:  # pragma: no cover - streaming payload
            raise ValueError("no json")

    responses = [StreamResponse()]
    client, session = client_factory(responses)

    client.download_file(url="https://zoom.us/download/file", access_token="abc+/=")

    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert "access_token=abc%2B%2F%3D" in url
    assert kwargs["timeout"] == 10.0
    headers = kwargs["headers"]
    assert headers.get("Accept") == "*/*"
    assert "Content-Type" not in headers


def test_download_file_allows_timeout_override(client_factory: ClientFactory) -> None:
    class StreamResponse(StubResponse):
        def __init__(self) -> None:
            super().__init__(b"ok")

        def json(self) -> Any:  # pragma: no cover - streaming payload
            raise ValueError("no json")

    responses = [StreamResponse()]
    client, session = client_factory(responses)

    client.download_file(
        url="https://zoom.us/download/file",
        access_token=None,
        timeout=5.0,
    )

    method, _url, kwargs = session.calls[0]
    assert method == "GET"
    assert kwargs["timeout"] == 5.0


def test_ensure_access_token_raises_when_expired_without_credentials() -> None:
    session = DummySession([])
    client = ZoomAPIClient(
        session=cast(requests.Session, session),
        access_token="token-1",
    )
    client._token_expiry = time.time() - 10

    with pytest.raises(RuntimeError):
        client._ensure_access_token()


def test_concurrent_client_access_is_thread_safe() -> None:
    """Ensure the client can be safely used from multiple threads."""
    # Create a client with a token that doesn't expire
    session = DummySession([])
    client = ZoomAPIClient(
        session=cast(requests.Session, session),
        access_token="shared-token",
    )

    errors: list[Exception] = []
    results: list[str] = []

    def access_token_concurrently() -> None:
        try:
            # Call _ensure_access_token from multiple threads
            client._ensure_access_token()
            # Access the token (simulating what _headers does)
            token = client._access_token
            if token is None:  # pragma: no cover - defensive for mypy
                raise AssertionError("Expected access token to be populated")
            results.append(token)
        except Exception as exc:
            errors.append(exc)

    # Start multiple threads that access the client simultaneously
    threads: list[threading.Thread] = [
        threading.Thread(target=access_token_concurrently) for _ in range(10)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Verify no errors occurred and all threads saw the same token
    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(results) == 10
    assert all(token == "shared-token" for token in results)


def test_download_file_rejects_non_zoom_host() -> None:
    """Ensure download_file refuses non-Zoom domains (SSRF protection)."""

    class DownloadSession:
        def get(self, *_args: Any, **_kwargs: Any) -> NoReturn:
            raise AssertionError("get should not be called for non-Zoom host")

        def request(self, *_args: Any, **_kwargs: Any) -> NoReturn:
            raise AssertionError("request should not be called for non-Zoom")

    session = DownloadSession()
    client = ZoomAPIClient(
        account_id="account",
        client_id="id",
        client_secret="secret",
        session=cast(requests.Session, session),
        access_token="token-123",
    )

    # Test various non-Zoom hosts
    bad_hosts: list[str] = [
        "https://evil.com/file.mp4",
        "https://zoom.us.attacker.com/file.mp4",
        "https://notzoom.us/file.mp4",
        "http://example.com/file.mp4",
        "https://api.zoom.us.evil.com/recording",
    ]

    for url in bad_hosts:
        with pytest.raises(ValueError, match="Refusing to download from non-Zoom host"):
            client.download_file(url=url)


def test_download_file_allows_zoom_hosts() -> None:
    """Ensure download_file accepts valid Zoom domains."""

    class DownloadSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, Any]]] = []

        def request(self, method: str, url: str, **kwargs: Any) -> StubResponse:
            self.calls.append((method, url, kwargs))
            return StubResponse(b"test-content")

    session = DownloadSession()
    client = ZoomAPIClient(
        account_id="account",
        client_id="id",
        client_secret="secret",
        session=cast(requests.Session, session),
        access_token="token-123",
    )

    # Test various valid Zoom hosts
    valid_hosts: list[str] = [
        "https://zoom.us/rec/download/file.mp4",
        "https://api.zoom.us/v2/recordings/download",
        "https://us01web.zoom.us/rec/download/file.mp4",
        "https://subdomain.zoom.us/recording/file.mp4",
    ]

    for url in valid_hosts:
        content = client.download_file(url=url)
        assert content == b"test-content"
        assert len(session.calls) > 0
        method, recorded_url, recorded_kwargs = session.calls[-1]
        assert method == "GET"
        assert recorded_url == url
        headers = recorded_kwargs["headers"]
        assert headers["Accept"] == "*/*"
        assert "Content-Type" not in headers
        assert recorded_kwargs["stream"] is True
        assert recorded_kwargs["allow_redirects"] is False


def test_download_file_with_access_token_validates_host() -> None:
    """Ensure host validation happens even when access_token is provided."""

    class DownloadSession:
        def get(self, *_args: Any, **_kwargs: Any) -> NoReturn:
            raise AssertionError("Session.get should not be called")

        def request(self, *_args: Any, **_kwargs: Any) -> NoReturn:
            raise AssertionError("Session.request should not be called")

    session = DownloadSession()
    client = ZoomAPIClient(
        account_id="account",
        client_id="id",
        client_secret="secret",
        session=cast(requests.Session, session),
        access_token="token-123",
    )

    # Even with access_token in URL, non-Zoom hosts should be rejected
    with pytest.raises(ValueError, match="Refusing to download from non-Zoom host"):
        client.download_file(url="https://evil.com/file.mp4", access_token="secret-token")
