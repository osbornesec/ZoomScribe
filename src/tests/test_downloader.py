from unittest.mock import Mock

import pytest

from zoom_scribe.downloader import DownloadError, RecordingDownloader, _sanitize
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

    destination = downloader.build_file_path(sample_recording, recording_file, "/downloads")

    path_str = destination.as_posix()
    assert "/downloads" in path_str
    assert "host@example.com" in path_str
    assert "Project__Kickoff_" in path_str
    assert path_str.endswith(".mp4")
    assert "?" not in path_str


def test_download_creates_directories_and_writes_files(tmp_path, sample_recording):
    client = Mock()
    client.download_file.return_value = b"binary-data"
    downloader = RecordingDownloader(client, max_workers=1)

    downloader.download([sample_recording], tmp_path, dry_run=False, overwrite=False)

    recording_file = sample_recording.recording_files[0]
    destination = downloader.build_file_path(sample_recording, recording_file, tmp_path)
    assert destination.exists()
    assert destination.read_bytes() == b"binary-data"
    assert not destination.with_suffix(destination.suffix + ".part").exists()
    client.download_file.assert_called_once()


def test_download_skips_existing_file_without_overwrite(tmp_path, sample_recording):
    client = Mock()
    downloader = RecordingDownloader(client, max_workers=1)

    recording_file = sample_recording.recording_files[0]
    destination = downloader.build_file_path(sample_recording, recording_file, tmp_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"existing")

    downloader.download([sample_recording], tmp_path, dry_run=False, overwrite=False)

    client.download_file.assert_not_called()
    assert destination.read_bytes() == b"existing"


def test_download_overwrites_when_requested(tmp_path, sample_recording):
    client = Mock()
    client.download_file.return_value = b"new-data"
    downloader = RecordingDownloader(client, max_workers=1)

    recording_file = sample_recording.recording_files[0]
    destination = downloader.build_file_path(sample_recording, recording_file, tmp_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"old-data")

    downloader.download([sample_recording], tmp_path, dry_run=False, overwrite=True)

    client.download_file.assert_called_once()
    assert destination.read_bytes() == b"new-data"


def test_download_in_dry_run_mode(tmp_path, sample_recording):
    client = Mock()
    downloader = RecordingDownloader(client, max_workers=1)

    downloader.download([sample_recording], tmp_path, dry_run=True, overwrite=False)

    client.download_file.assert_not_called()
    recording_file = sample_recording.recording_files[0]
    destination = downloader.build_file_path(sample_recording, recording_file, tmp_path)
    assert not destination.exists()


def test_download_cleans_temp_on_failure(tmp_path, monkeypatch, sample_recording):
    client = Mock()
    client.download_file.return_value = b"binary-data"
    downloader = RecordingDownloader(client, max_workers=1)

    recording_file = sample_recording.recording_files[0]
    destination = downloader.build_file_path(sample_recording, recording_file, tmp_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def fail_replace(src, dst):
        raise OSError("boom")

    monkeypatch.setattr("zoom_scribe.downloader.os.replace", fail_replace)

    with pytest.raises(DownloadError):
        downloader.download([sample_recording], tmp_path, dry_run=False, overwrite=False)

    temp_path = destination.with_suffix(destination.suffix + ".part")
    assert not temp_path.exists()


@pytest.mark.parametrize("value", [".", "..", "...", "...."])
def test_sanitize_replaces_dot_only_values(value):
    assert _sanitize(value) == "_"
