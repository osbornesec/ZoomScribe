from unittest.mock import Mock

from click.testing import CliRunner

from zoom_scribe.main import cli


def test_cli_invokes_dry_run(monkeypatch):
    recordings = ["recording"]

    client = Mock()
    client.list_recordings.return_value = recordings
    downloader = Mock()

    monkeypatch.setattr("zoom_scribe.main.create_client", lambda: client)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda client, logger=None: downloader,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["--dry-run"])

    assert result.exit_code == 0
    client.list_recordings.assert_called_once()
    downloader.download.assert_called_once()
    args, kwargs = downloader.download.call_args
    assert kwargs["dry_run"] is True
    assert kwargs["overwrite"] is False


def test_cli_passes_date_filters(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.calls = []

        def list_recordings(self, *, start, end, host_email=None, meeting_id=None):
            self.calls.append((start, end, host_email, meeting_id))
            return []

    client_instance = FakeClient()

    downloader = Mock()

    monkeypatch.setattr("zoom_scribe.main.create_client", lambda: client_instance)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda client, logger=None: downloader,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["--from", "2025-09-01", "--to", "2025-09-15"])

    assert result.exit_code == 0
    assert client_instance.calls
    start, end, _, _ = client_instance.calls[0]
    assert start.year == 2025 and start.day == 1
    assert end.year == 2025 and end.day == 15


def test_cli_overwrite_option(monkeypatch):
    client = Mock()
    client.list_recordings.return_value = []
    downloader = Mock()

    monkeypatch.setattr("zoom_scribe.main.create_client", lambda: client)
    monkeypatch.setattr(
        "zoom_scribe.main.create_downloader",
        lambda client, logger=None: downloader,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["--overwrite"])

    assert result.exit_code == 0
    assert downloader.download.call_args[1]["overwrite"] is True
