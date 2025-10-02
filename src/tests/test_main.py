import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from zoom_scribe.config import Config, DownloaderConfig, LoggingConfig, OAuthCredentials
from zoom_scribe.main import cli
from zoom_scribe.screenshare.preprocess import PreprocessingError


@pytest.fixture
def mock_config() -> Config:
    return Config(
        credentials=OAuthCredentials("id", "id", "secret"),
        logging=LoggingConfig(),
        downloader=DownloaderConfig(),
    )


def test_cli_invokes_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    recordings: list[str] = ["recording"]

    client = Mock()
    client.list_recordings.return_value = recordings
    downloader = Mock()
    downloader.config = DownloaderConfig(dry_run=True)

    monkeypatch.setattr("zoom_scribe.main.load_oauth_credentials", lambda: Mock())
    monkeypatch.setattr("zoom_scribe.main.create_client", lambda _: client)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda _, __: downloader,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["download", "--dry-run"])

    assert result.exit_code == 0
    client.list_recordings.assert_called_once()
    downloader.download.assert_called_once()
    assert downloader.config.dry_run is True
    assert downloader.config.overwrite is False


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

    monkeypatch.setattr("zoom_scribe.main.load_oauth_credentials", lambda: Mock())
    monkeypatch.setattr("zoom_scribe.main.create_client", lambda _: client_instance)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda _, __: downloader,
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
    """CLI propagates the overwrite flag when invoked with `download --overwrite`."""
    client = Mock()
    client.list_recordings.return_value = []
    downloader = Mock()
    downloader.config = DownloaderConfig(overwrite=True)

    monkeypatch.setattr("zoom_scribe.main.load_oauth_credentials", lambda: Mock())
    monkeypatch.setattr("zoom_scribe.main.create_client", lambda _: client)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda _, __: downloader,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["download", "--overwrite"])

    assert result.exit_code == 0
    assert downloader.config.overwrite is True


def test_cli_configures_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_configure(config: LoggingConfig) -> logging.Logger:
        captured["config"] = config
        return logging.getLogger("zoom_scribe.test")

    client = Mock()
    client.list_recordings.return_value = []
    downloader = Mock()

    monkeypatch.setattr("zoom_scribe.main.load_oauth_credentials", lambda: Mock())
    monkeypatch.setattr("zoom_scribe.main.configure_logging", fake_configure)
    monkeypatch.setattr("zoom_scribe.main.create_client", lambda _: client)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda _, __: downloader,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["download", "--dry-run", "--log-level", "DEBUG", "--log-format", "json"],
    )

    assert result.exit_code == 0
    assert captured["config"].level.lower() == "debug"
    assert captured["config"].format.lower() == "json"


def test_cli_rejects_file_target_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI exits with an error when `--target-dir` points to an existing file."""
    file_path = tmp_path / "existing.txt"
    file_path.write_text("content", encoding="utf-8")

    monkeypatch.setattr("zoom_scribe.main.load_oauth_credentials", lambda: Mock())
    monkeypatch.setattr("zoom_scribe.main.create_client", lambda _: Mock())
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda _, __: Mock(),
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["download", "--target-dir", str(file_path)])

    assert result.exit_code != 0
    assert "Target path exists" in result.output


def test_cli_enables_screenshare_preprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Mock()
    client.list_recordings.return_value = []
    downloader = Mock()
    downloader.config = DownloaderConfig()

    monkeypatch.setattr("zoom_scribe.main.load_oauth_credentials", lambda: Mock())
    monkeypatch.setattr("zoom_scribe.main.create_client", lambda _: client)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda _, __: downloader,
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
        assert path == video
        return []

    monkeypatch.setattr("zoom_scribe.main.preprocess_video", fake_preprocess)
    monkeypatch.setattr(
        "zoom_scribe.main.build_frame_time_mapping",
        lambda _bundles: "Frame->Time (s):\n1 -> 0.100",
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
        raise PreprocessingError("failed")

    monkeypatch.setattr("zoom_scribe.main.preprocess_video", raise_error)

    runner = CliRunner()
    result = runner.invoke(cli, ["screenshare", "preprocess", str(video)])

    assert result.exit_code != 0
    assert "failed" in result.output
