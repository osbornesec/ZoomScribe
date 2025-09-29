from unittest.mock import Mock

import pytest

from zoom_scribe.downloader import RecordingDownloader
from zoom_scribe.models import Recording


@pytest.fixture
def sample_recording():
    payload = {
        "uuid": "uuid-1",
        "topic": "Project / Kickoff?",
        "host_email": "host@example.com",
        "start_time": "2025-09-28T10:00:00Z",
        "recording_files": [
            {
                "id": "file-1",
                "file_type": "MP4",
                "file_extension": "mp4",
                "download_url": "https://zoom.us/download/file-1",
                "download_access_token": "token",
            }
        ],
    }
    return Recording.from_api(payload)


def test_build_file_path_sanitizes_components(sample_recording):
    downloader = RecordingDownloader(Mock())
    recording_file = sample_recording.recording_files[0]

    destination = downloader.build_file_path(
        sample_recording, recording_file, "/downloads"
    )

    path_str = destination.as_posix()
    assert "/downloads" in path_str
    assert "host@example.com" in path_str
    assert "Project__Kickoff_" in path_str
    assert path_str.endswith(".mp4")
    assert "?" not in path_str


def test_download_creates_directories_and_writes_files(monkeypatch, sample_recording):
    client = Mock()
    client.download_file.return_value = b"binary-data"
    downloader = RecordingDownloader(client)

    created_dirs = []
    written = {}

    def fake_mkdir(self, parents=False, exist_ok=False):
        created_dirs.append((str(self), parents, exist_ok))

    def fake_write_bytes(self, data):
        written[str(self)] = data

    monkeypatch.setattr("zoom_scribe.downloader.Path.mkdir", fake_mkdir, raising=False)
    monkeypatch.setattr(
        "zoom_scribe.downloader.Path.write_bytes", fake_write_bytes, raising=False
    )
    monkeypatch.setattr(
        "zoom_scribe.downloader.Path.exists", lambda self: False, raising=False
    )

    downloader.download(
        [sample_recording], "/downloads", dry_run=False, overwrite=False
    )

    assert client.download_file.called
    assert written, "Expected bytes to be written to a file"
    destination = next(iter(written.keys()))
    assert "Project__Kickoff_" in destination
    assert created_dirs, "Expected directory creation to be attempted"


def test_download_skips_existing_file_without_overwrite(monkeypatch, sample_recording):
    client = Mock()
    downloader = RecordingDownloader(client)

    def fail_write(self, data):
        raise AssertionError("should not write")

    monkeypatch.setattr(
        "zoom_scribe.downloader.Path.exists", lambda self: True, raising=False
    )
    monkeypatch.setattr(
        "zoom_scribe.downloader.Path.write_bytes", fail_write, raising=False
    )

    downloader.download(
        [sample_recording], "/downloads", dry_run=False, overwrite=False
    )

    client.download_file.assert_not_called()


def test_download_in_dry_run_mode(monkeypatch, sample_recording):
    client = Mock()
    downloader = RecordingDownloader(client)

    def fail_write(self, data):
        raise AssertionError("dry run should not write")

    monkeypatch.setattr(
        "zoom_scribe.downloader.Path.exists", lambda self: False, raising=False
    )
    monkeypatch.setattr(
        "zoom_scribe.downloader.Path.write_bytes", fail_write, raising=False
    )

    downloader.download([sample_recording], "/downloads", dry_run=True, overwrite=False)

    client.download_file.assert_not_called()
