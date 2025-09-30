import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from zoom_scribe.main import cli


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
    result = runner.invoke(cli, ["--dry-run"])

    assert result.exit_code == 0
    client.list_recordings.assert_called_once()
    downloader.download.assert_called_once()
    _args, kwargs = downloader.download.call_args
    assert kwargs["dry_run"] is True
    assert kwargs["overwrite"] is False


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
    result = runner.invoke(cli, ["--from", "2025-09-01", "--to", "2025-09-15"])

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
    result = runner.invoke(cli, ["--overwrite"])

    assert result.exit_code == 0
    assert downloader.download.call_args[1]["overwrite"] is True


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
        ["--dry-run", "--log-level", "DEBUG", "--log-format", "json"],
    )

    assert result.exit_code == 0
    assert captured["level"].lower() == "debug"
    assert captured["format"].lower() == "json"


def test_cli_rejects_file_target_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    result = runner.invoke(cli, ["--target-dir", str(file_path)])

    assert result.exit_code != 0
    assert "Target path exists" in result.output
