"""Tests for the FastAPI web adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from zoom_scribe.config import Config, DownloaderConfig, LoggingConfig, OAuthCredentials
from zoom_scribe.models import Recording, RecordingFile
from zoom_scribe.web_api import ApiContext, app, get_api_context


class StubZoomClient:
    """In-memory Zoom client used to stub out network calls."""

    def __init__(self, recordings: list[Recording]) -> None:
        self._recordings = recordings
        self.calls: list[dict[str, Any]] = []

    def list_recordings(
        self,
        *,
        start: datetime,
        end: datetime,
        host_email: str | None = None,
        meeting_id: str | None = None,
        page_size: int = 100,
    ) -> list[Recording]:
        self.calls.append(
            {
                "start": start,
                "end": end,
                "host_email": host_email,
                "meeting_id": meeting_id,
                "page_size": page_size,
            }
        )
        return list(self._recordings)


class SpyDownloader:
    """Spy implementation that records download invocations."""

    def __init__(self, downloader_config: DownloaderConfig) -> None:
        self.config = downloader_config
        self.calls: list[list[Recording]] = []

    def download(self, recordings: list[Recording]) -> None:
        self.calls.append(list(recordings))


@pytest.fixture
def sample_recordings() -> list[Recording]:
    start = datetime(2024, 1, 5, 15, 30, tzinfo=UTC)
    files = (
        RecordingFile(
            id="file-1",
            file_type="MP4",
            file_extension="mp4",
            download_url="https://zoom.us/recording",
            file_size=2048,
        ),
    )
    recording = Recording(
        uuid="uuid-123",
        meeting_topic="Weekly Sync",
        host_email="host@example.com",
        start_time=start,
        duration_minutes=55,
        recording_files=files,
    )
    return [recording]


@pytest.fixture
def context(sample_recordings: list[Recording]) -> ApiContext:
    credentials = OAuthCredentials(
        account_id="acct",
        client_id="client",
        client_secret="secret",
    )
    config = Config(
        credentials=credentials,
        logging=LoggingConfig(level="debug", format="json"),
        downloader=DownloaderConfig(target_dir=Path("downloads"), overwrite=False, dry_run=False),
    )
    client = StubZoomClient(sample_recordings)
    return ApiContext(config=config, client=client)


@pytest.fixture
def client(context: ApiContext) -> TestClient:
    app.dependency_overrides[get_api_context] = lambda: context
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()
        app.dependency_overrides.pop(get_api_context, None)


def test_health_endpoint() -> None:
    with TestClient(app) as test_client:
        response = test_client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_recordings_returns_summaries(client: TestClient, context: ApiContext) -> None:
    response = client.get("/api/recordings?from=2024-01-01&to=2024-01-31")
    assert response.status_code == 200
    payload = response.json()

    assert payload == [
        {
            "uuid": "uuid-123",
            "meeting_id": None,
            "topic": "Weekly Sync",
            "host_email": "host@example.com",
            "start_time": "2024-01-05T15:30:00Z",
            "duration_minutes": 55,
            "asset_count": 1,
            "total_size_bytes": 2048,
        }
    ]

    call = context.client.calls[-1]
    assert call["host_email"] is None
    assert call["meeting_id"] is None


def test_trigger_download_invokes_downloader(
    client: TestClient,
    context: ApiContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = SpyDownloader(context.config.downloader)

    def factory(config: Config, _: StubZoomClient) -> SpyDownloader:
        spy.config = config.downloader
        return spy

    monkeypatch.setattr("zoom_scribe.web_api.create_downloader", factory)

    response = client.post(
        "/api/download",
        json={"meeting_id_or_uuid": "uuid-123", "overwrite": True, "target_dir": "custom"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["ok"] is True
    assert payload["files_expected"] == 1
    assert spy.calls and len(spy.calls[0]) == 1
    assert spy.config.target_dir == Path("custom")
    assert spy.config.overwrite is True
