import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Any
from unittest.mock import Mock

import pytest

from zoom_scribe.config import DownloaderConfig
from zoom_scribe.downloader import DownloadError, RecordingDownloader, _sanitize
from zoom_scribe.models import Recording

RecordingFactory = Callable[[str], Recording]


@pytest.fixture
def recording_factory() -> RecordingFactory:
    """Return a factory for creating sample ``Recording`` instances."""

    def _factory(uuid: str) -> Recording:
        payload: dict[str, Any] = {
            "uuid": uuid,
            "topic": "Project / Kickoff?",
            "host_email": "host@example.com",
            "start_time": "2025-09-28T10:00:00Z",
            "recording_files": [
                {
                    "id": f"file-{uuid}",
                    "file_type": "MP4",
                    "file_extension": "mp4",
                    "download_url": f"https://zoom.us/download/{uuid}",
                    "download_access_token": "token",
                }
            ],
        }
        return Recording.from_api(payload)

    return _factory


def test_build_file_path_sanitizes_components(
    recording_factory: RecordingFactory,
) -> None:
    sample_recording = recording_factory("uuid-1")
    config = DownloaderConfig()
    downloader = RecordingDownloader(Mock(), config=config)
    recording_file = sample_recording.files[0]

    destination = downloader.build_file_path(sample_recording, recording_file, Path("/downloads"))

    path_str = destination.as_posix()
    assert "/downloads" in path_str
    assert "host@example.com" in path_str
    assert "Project__Kickoff_" in path_str
    assert path_str.endswith(".mp4")
    assert "?" not in path_str


def test_download_creates_directories_and_writes_files(
    tmp_path: Path, recording_factory: RecordingFactory
) -> None:
    sample_recording = recording_factory("uuid-1")
    client = Mock()
    client.download_recording_file.return_value = b"binary-data"
    config = DownloaderConfig(target_dir=tmp_path)
    downloader = RecordingDownloader(client, config=config, max_workers=1)

    downloader.download([sample_recording])

    recording_file = sample_recording.files[0]
    destination = downloader.build_file_path(sample_recording, recording_file, tmp_path)
    assert destination.exists()
    assert destination.read_bytes() == b"binary-data"
    assert not destination.with_suffix(destination.suffix + ".part").exists()
    client.download_recording_file.assert_called_once()


def test_download_skips_existing_file_without_overwrite(
    tmp_path: Path, recording_factory: RecordingFactory
) -> None:
    sample_recording = recording_factory("uuid-1")
    client = Mock()
    config = DownloaderConfig(target_dir=tmp_path, overwrite=False)
    downloader = RecordingDownloader(client, config=config, max_workers=1)

    recording_file = sample_recording.files[0]
    destination = downloader.build_file_path(sample_recording, recording_file, tmp_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"existing")

    downloader.download([sample_recording])

    client.download_recording_file.assert_not_called()
    assert destination.read_bytes() == b"existing"


def test_download_overwrites_when_requested(
    tmp_path: Path, recording_factory: RecordingFactory
) -> None:
    sample_recording = recording_factory("uuid-1")
    client = Mock()
    client.download_recording_file.return_value = b"new-data"
    config = DownloaderConfig(target_dir=tmp_path, overwrite=True)
    downloader = RecordingDownloader(client, config=config, max_workers=1)

    recording_file = sample_recording.files[0]
    destination = downloader.build_file_path(sample_recording, recording_file, tmp_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"old-data")

    downloader.download([sample_recording])

    client.download_recording_file.assert_called_once()
    assert destination.read_bytes() == b"new-data"


def test_download_in_dry_run_mode(tmp_path: Path, recording_factory: RecordingFactory) -> None:
    sample_recording = recording_factory("uuid-1")
    client = Mock()
    config = DownloaderConfig(target_dir=tmp_path, dry_run=True)
    downloader = RecordingDownloader(client, config=config, max_workers=1)

    hook_called = False

    def hook(*_args: Any) -> None:
        nonlocal hook_called
        hook_called = True

    downloader.download(
        [sample_recording],
        post_download=hook,
    )

    client.download_recording_file.assert_not_called()
    recording_file = sample_recording.files[0]
    destination = downloader.build_file_path(sample_recording, recording_file, tmp_path)
    assert not destination.exists()
    assert hook_called is False


def test_download_cleans_temp_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_factory: RecordingFactory,
) -> None:
    sample_recording = recording_factory("uuid-1")
    client = Mock()
    client.download_recording_file.return_value = b"binary-data"
    config = DownloaderConfig(target_dir=tmp_path)
    downloader = RecordingDownloader(client, config=config, max_workers=1)

    recording_file = sample_recording.files[0]
    destination = downloader.build_file_path(sample_recording, recording_file, tmp_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def fail_replace(_self: Path, _target: Path | str) -> None:
        raise OSError("boom")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(DownloadError):
        downloader.download([sample_recording])

    temp_path = destination.with_suffix(destination.suffix + ".part")
    assert not temp_path.exists()


@pytest.mark.parametrize("value", [".", "..", "...", "...."])
def test_sanitize_replaces_dot_only_values(value: str) -> None:
    assert _sanitize(value) == "_"


def test_concurrent_downloads_are_thread_safe(
    tmp_path: Path, recording_factory: RecordingFactory
) -> None:
    """Ensure multiple files can be downloaded concurrently without race conditions."""
    call_count = 0
    call_lock = Lock()

    # Create a mock client that simulates concurrent downloads with shared state
    client = Mock()

    def download_with_delay(*_args: Any, **_kwargs: Any) -> bytes:
        nonlocal call_count
        with call_lock:
            call_count += 1
        # Simulate network delay to increase chance of race conditions
        time.sleep(0.01)
        return b"data"

    client.download_recording_file.side_effect = download_with_delay

    # Create multiple recordings to download in parallel
    recordings = [recording_factory(f"uuid-{i}") for i in range(5)]

    config = DownloaderConfig(target_dir=tmp_path)
    downloader = RecordingDownloader(client, config=config, max_workers=3)
    downloader.download(recordings)

    # Verify all files were downloaded
    assert call_count == 5
    assert client.download_recording_file.call_count == 5

    # Verify all files exist on disk
    for recording in recordings:
        recording_file = recording.files[0]
        destination = downloader.build_file_path(recording, recording_file, tmp_path)
        assert destination.exists()
        assert destination.read_bytes() == b"data"


def test_download_invokes_post_download_hook(
    tmp_path: Path, recording_factory: RecordingFactory
) -> None:
    sample_recording = recording_factory("uuid-1")
    client = Mock()
    client.download_recording_file.return_value = b"binary-data"
    config = DownloaderConfig(target_dir=tmp_path)
    downloader = RecordingDownloader(client, config=config, max_workers=1)
    calls: list[Path] = []

    def hook(path: Path, *_args: Any) -> None:
        calls.append(path)

    downloader.download(
        [sample_recording],
        post_download=hook,
    )

    assert calls
    recording_file = sample_recording.files[0]
    expected_path = downloader.build_file_path(sample_recording, recording_file, tmp_path)
    assert calls[0] == expected_path
