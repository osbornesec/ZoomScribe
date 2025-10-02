from datetime import UTC, datetime
from typing import Any

import pytest

from zoom_scribe.models import (
    ModelValidationError,
    Recording,
    RecordingFile,
    RecordingPage,
)


@pytest.fixture
def recording_file_payload() -> dict[str, Any]:
    return {
        "id": "file123",
        "file_type": "MP4",
        "file_extension": "mp4",
        "download_url": "https://zoom.us/download/123",
        "download_access_token": "token-123",
    }


def test_recording_file_from_api(recording_file_payload: dict[str, Any]) -> None:
    recording_file = RecordingFile.from_api(recording_file_payload)

    assert recording_file.id == "file123"
    assert recording_file.file_type == "MP4"
    assert recording_file.file_extension == "mp4"
    assert recording_file.download_url.endswith("/123")
    assert recording_file.download_access_token == "token-123"


def test_recording_from_api_creates_recording_files(recording_file_payload: dict[str, Any]) -> None:
    payload: dict[str, Any] = {
        "uuid": "meeting-uuid",
        "topic": "Team Sync",
        "host_email": "host@example.com",
        "start_time": "2025-09-28T10:00:00Z",
        "recording_files": [recording_file_payload],
    }

    recording = Recording.from_api(payload)

    assert recording.uuid == "meeting-uuid"
    assert recording.meeting_topic == "Team Sync"
    assert recording.host_email == "host@example.com"
    assert recording.start_time == datetime(2025, 9, 28, 10, 0, tzinfo=UTC)
    assert len(recording.recording_files) == 1
    assert recording.recording_files[0].id == "file123"


def test_recording_from_api_requires_uuid(recording_file_payload: dict[str, Any]) -> None:
    payload: dict[str, Any] = {
        "topic": "Team Sync",
        "host_email": "host@example.com",
        "start_time": "2025-09-28T10:00:00Z",
        "recording_files": [recording_file_payload],
    }

    with pytest.raises(ModelValidationError):
        Recording.from_api(payload)


def test_recording_page_from_api_handles_pagination(recording_file_payload: dict[str, Any]) -> None:
    payload: dict[str, Any] = {
        "meetings": [
            {
                "uuid": "meeting-uuid",
                "topic": "Team Sync",
                "host_email": "host@example.com",
                "start_time": "2025-09-28T10:00:00Z",
                "recording_files": [recording_file_payload],
            }
        ],
        "next_page_token": "abc",
        "total_records": 10,
    }

    page = RecordingPage.from_api(payload)

    assert page.has_next_page() is True
    assert page.total_records == 10
    assert len(page.recordings) == 1
    assert page.recordings[0].uuid == "meeting-uuid"
