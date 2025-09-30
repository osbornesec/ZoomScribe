import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from zoom_scribe.main import cli
from zoom_scribe.screenshare.preprocess import PreprocessingError


def test_cli_invokes_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    recordings: list[str] = ["recording"]

    client = Mock()
    client.list_recordings.return_value = recordings
    downloader = Mock()

    monkeypatch.setattr("zoom_scribe.main.create_client", lambda: client)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda _client, **_kwargs: downloader,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["download", "--dry-run"])

    assert result.exit_code == 0
    client.list_recordings.assert_called_once()
    downloader.download.assert_called_once()
    _args, kwargs = downloader.download.call_args
    assert kwargs["dry_run"] is True
    assert kwargs["overwrite"] is False
    assert kwargs["post_download"] is None


def test_cli_passes_date_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[datetime, datetime, str | None, str | None]] = []

        def list_recordings(
            self,
            *,
            start: datetime,
            end: datetime,
            host_email: str | None = None,
            meeting_id: str | None = None,
        ) -> list[str]:
            self.calls.append((start, end, host_email, meeting_id))
            return []

    client_instance = FakeClient()

    downloader = Mock()

    monkeypatch.setattr("zoom_scribe.main.create_client", lambda: client_instance)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda _client, **_kwargs: downloader,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["download", "--from", "2025-09-01", "--to", "2025-09-15"],
    )

    assert result.exit_code == 0
    assert client_instance.calls
    start, end, _, _ = client_instance.calls[0]
    assert start.year == 2025 and start.day == 1
    assert end.year == 2025 and end.day == 15


def test_cli_overwrite_option(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Mock()
    client.list_recordings.return_value = []
    downloader = Mock()

    monkeypatch.setattr("zoom_scribe.main.create_client", lambda: client)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda _client, **_kwargs: downloader,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["download", "--overwrite"])

    assert result.exit_code == 0
    assert downloader.download.call_args[1]["overwrite"] is True
    assert downloader.download.call_args[1]["post_download"] is None


def test_cli_configures_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_configure(level: str, fmt: str) -> logging.Logger:
        captured["level"] = level
        captured["format"] = fmt
        return logging.getLogger("zoom_scribe.test")

    client = Mock()
    client.list_recordings.return_value = []
    downloader = Mock()

    monkeypatch.setattr("zoom_scribe.main.configure_logging", fake_configure)
    monkeypatch.setattr("zoom_scribe.main.create_client", lambda: client)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda _client, **_kwargs: downloader,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["download", "--dry-run", "--log-level", "DEBUG", "--log-format", "json"],
    )

    assert result.exit_code == 0
    assert captured["level"].lower() == "debug"
    assert captured["format"].lower() == "json"
    assert downloader.download.call_args[1]["post_download"] is None


def test_cli_rejects_file_target_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file_path = tmp_path / "existing.txt"
    file_path.write_text("content", encoding="utf-8")

    def fake_logging(*_args: Any, **_kwargs: Any) -> logging.Logger:
        return logging.getLogger("zoom_scribe.test")

    monkeypatch.setattr("zoom_scribe.main.configure_logging", fake_logging)
    monkeypatch.setattr("zoom_scribe.main.create_client", lambda: Mock())
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda _client, **_kwargs: Mock(),
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["download", "--target-dir", str(file_path)])

    assert result.exit_code != 0
    assert "Target path exists" in result.output


def test_cli_enables_screenshare_preprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Mock()
    client.list_recordings.return_value = []
    downloader = Mock()

    monkeypatch.setattr("zoom_scribe.main.create_client", lambda: client)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda _client, **_kwargs: downloader,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["download", "--screenshare-preprocess", "--screenshare-output-dir", "out"],
    )

    assert result.exit_code == 0
    _, kwargs = downloader.download.call_args
    hook = kwargs["post_download"]
    assert callable(hook)


def test_screenshare_preprocess_command_writes_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"data")

    def fake_preprocess(path: Path, _config: Any) -> list[Any]:
        """
        Test stub for a video preprocessing function used in unit tests.
        
        Parameters:
            path (Path): Path to the video file to preprocess; this stub asserts that `path` matches the expected test video and will raise AssertionError if it does not.
            _config (Any): Ignored configuration parameter kept for signature compatibility.
        
        Returns:
            list[Any]: An empty list representing no preprocessing results.
        """
        assert path == video
        return []

    monkeypatch.setattr("zoom_scribe.main.preprocess_video", fake_preprocess)
    monkeypatch.setattr(
        "zoom_scribe.main.build_frame_time_mapping",
        lambda _bundles: "Frame→Time (s):\n1 -> 0.100",
    )

    runner = CliRunner()
    mapping_path = tmp_path / "mapping.txt"
    result = runner.invoke(
        cli,
        [
            "screenshare",
            "preprocess",
            str(video),
            "--output",
            str(mapping_path),
        ],
    )

    assert result.exit_code == 0
    assert mapping_path.exists()
    assert "Frame mapping written" in result.output


def test_screenshare_preprocess_command_handles_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"data")

    def raise_error(*_args: Any, **_kwargs: Any) -> list[Any]:
        """
        Always raises a PreprocessingError with the message "failed".
        
        Parameters:
            *_args: Ignored positional arguments.
            **_kwargs: Ignored keyword arguments.
        
        Raises:
            PreprocessingError: Always raised with the message "failed".
        """
        raise PreprocessingError("failed")

    monkeypatch.setattr("zoom_scribe.main.preprocess_video", raise_error)

    runner = CliRunner()
    result = runner.invoke(cli, ["screenshare", "preprocess", str(video)])

    assert result.exit_code != 0
    assert "failed" in result.output
