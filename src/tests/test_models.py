from datetime import UTC, datetime

import pytest

from zoom_scribe.models import Recording, RecordingFile


@pytest.fixture
def recording_file_payload():
    return {
        "id": "file123",
        "file_type": "MP4",
        "file_extension": "mp4",
        "download_url": "https://zoom.us/download/123",
        "download_access_token": "token-123",
    }


def test_recording_file_from_api(recording_file_payload):
    recording_file = RecordingFile.from_api(recording_file_payload)

    assert recording_file.id == "file123"
    assert recording_file.file_type == "MP4"
    assert recording_file.file_extension == "mp4"
    assert recording_file.download_url.endswith("/123")
    assert recording_file.download_access_token == "token-123"


def test_recording_from_api_creates_recording_files(recording_file_payload):
    payload = {
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
