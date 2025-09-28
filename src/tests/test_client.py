from datetime import UTC, datetime

import pytest
import requests

from zoom_scribe.client import ZoomAPIClient


class StubResponse:
    def __init__(self, payload, status_code=200, headers=None):
        """
        Initialize the stub HTTP response with a payload, status code, and headers.
        
        Parameters:
            payload (Any): The body of the response; stored and returned by the response's `json()` and also converted to `text`.
            status_code (int): HTTP status code for the response (e.g., 200, 404, 429).
            headers (Optional[dict]): Mapping of response header names to values; defaults to an empty dict.
        """
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = str(payload)

    def json(self):
        """
        Retrieve the stored JSON payload for this stubbed response.
        
        Returns:
            The original payload object that was provided to the stub response.
        """
        return self._payload

    def raise_for_status(self):
        """
        Raise an HTTPError when the response has an error status code.
        
        Raises:
            requests.HTTPError: If the response's status_code is greater than or equal to 400.
        """
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class DummySession:
    def __init__(self, responses):
        """
        Initialize the DummySession with a sequence of predefined responses and an empty call log.
        
        Parameters:
            responses (iterable): An iterable of response objects to serve in FIFO order when request() is called. The iterable is copied into an internal list.
        """
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        """
        Record an outgoing request and return the next predefined mock response.
        
        Records the (method, url, kwargs) tuple in the session's call log, removes and returns the next response from the internal responses queue, and attaches the original request arguments to the returned response as `request_args`.
        
        Parameters:
        	method (str): HTTP method for the request (e.g., "GET", "POST").
        	url (str): Request URL.
        	**kwargs: Additional request arguments (query params, headers, json/body, etc.) which are recorded and forwarded to the returned response.
        
        Returns:
        	response: The next predefined response object from the session's response queue with `request_args` set to (method, url, kwargs).
        """
        self.calls.append((method, url, kwargs))
        response = self._responses.pop(0)
        response.request_args = (method, url, kwargs)
        return response


@pytest.fixture
def client_factory(monkeypatch):
    """
    Provide a factory for tests that creates a ZoomAPIClient wired to a DummySession.
    
    The returned factory accepts a list of prebuilt response objects and returns a tuple of
    (ZoomAPIClient, DummySession) suitable for unit tests.
    
    Parameters:
        monkeypatch: pytest's monkeypatch fixture (passed through by the test harness).
    
    Returns:
        factory (callable): A function that takes `responses` (a list of mock response objects)
        and returns a tuple `(client, session)` where `client` is a ZoomAPIClient configured
        for testing and `session` is the DummySession that will serve the provided responses.
    """
    def _factory(responses):
        session = DummySession(responses)
        client = ZoomAPIClient(
            account_id="account",
            client_id="id",
            client_secret="secret",
            session=session,
            access_token="token-123",
            max_retries=3,
            backoff_factor=0.01,
        )
        return client, session

    return _factory


def make_meeting(uuid):
    """
    Create a deterministic meeting payload dictionary used by tests.
    
    Parameters:
        uuid (str): Meeting UUID to inject into the payload and file identifiers.
    
    Returns:
        dict: A meeting dictionary with keys:
            - "uuid": the provided UUID.
            - "topic": a fixed topic string.
            - "host_email": a fixed host email.
            - "start_time": an ISO 8601 timestamp string.
            - "recording_files": a list containing a single file dictionary with
              "id", "file_type", "file_extension", "download_url", and
              "download_access_token" (set to None).
    """
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


def test_list_recordings_returns_recording_models(client_factory):
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


def test_list_recordings_paginates_until_next_page_empty(client_factory):
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


def test_list_recordings_retries_on_rate_limit(monkeypatch, client_factory):
    responses = [
        StubResponse({}, status_code=429, headers={"Retry-After": "1"}),
        StubResponse({"meetings": [make_meeting("uuid-3")], "next_page_token": ""}),
    ]
    client, session = client_factory(responses)

    sleep_calls = []

    def fake_sleep(seconds):
        """
        Record a requested sleep duration by appending it to the module-level `sleep_calls` list.
        
        Parameters:
            seconds (float): Number of seconds that would have been slept; this value is appended to `sleep_calls`.
        """
        sleep_calls.append(seconds)

    monkeypatch.setattr("zoom_scribe.client.time.sleep", fake_sleep)

    recordings = client.list_recordings(
        start=datetime(2025, 9, 1, tzinfo=UTC),
        end=datetime(2025, 9, 30, tzinfo=UTC),
    )

    assert [rec.uuid for rec in recordings] == ["uuid-3"]
    assert len(session.calls) == 2
    assert sleep_calls, "Expected exponential backoff sleep to be triggered"


def test_list_recordings_enumerates_meeting_instances(client_factory):
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


def test_list_recordings_meeting_id_fallback_to_direct_lookup(client_factory):
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


def test_list_recordings_meeting_id_honors_host_filter(client_factory):
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
