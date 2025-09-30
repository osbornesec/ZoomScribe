"""Command-line interface for ZoomScribe."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import click

from ._datetime import ensure_utc
from ._redact import redact_identifier
from .client import ZoomAPIClient
from .downloader import RecordingDownloader

RESERVED_LOG_RECORD_ATTRS: Final[set[str]] = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
}


class JsonFormatter(logging.Formatter):
    """Serialize log records to JSON, preserving custom ``extra`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Return a JSON-encoded representation of ``record``."""
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in RESERVED_LOG_RECORD_ATTRS or key.startswith("_"):
                continue
            if key in {"host_email", "meeting_id", "meeting_topic", "recording_uuid", "recording_file_id"}:
                payload[key] = redact_identifier(str(value))
            else:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str, fmt: str) -> logging.Logger:
    """Configure the ``zoom_scribe`` logger hierarchy.

    Args:
        level: Logging level name (e.g., ``"info"``).
        fmt: Requested format (``"auto"``, ``"json"``, or ``"text"``).

    Returns:
        logging.Logger: Root logger for the Zoom Scribe namespace.
    """
    resolved_level = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    resolved_format = fmt.lower()
    if resolved_format == "auto":
        stream = getattr(handler, "stream", sys.stderr)
        is_tty = bool(getattr(stream, "isatty", lambda: False)())
        resolved_format = "text" if is_tty else "json"

    if resolved_format == "json":
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)
    logger = logging.getLogger("zoom_scribe")
    logger.handlers.clear()
    logger.setLevel(resolved_level)
    logger.propagate = False
    logger.addHandler(handler)
    logging.captureWarnings(True)
    return logger


def create_client() -> ZoomAPIClient:
    """Instantiate a ZoomAPIClient configured from the environment.

    Returns:
        ZoomAPIClient: Client ready to communicate with the Zoom API.
    """
    return ZoomAPIClient.from_env()


def create_downloader(
    client: ZoomAPIClient, logger: logging.Logger | None = None
) -> RecordingDownloader:
    """Build a RecordingDownloader bound to the provided client and logger.

    Args:
        client: Configured Zoom API client used to fetch recording bytes.
        logger: Optional logger for progress reporting.

    Returns:
        RecordingDownloader: Downloader instance tied to ``client``.
    """
    return RecordingDownloader(client, logger=logger)


@click.command()
@click.option(
    "--from",
    "from_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--to",
    "to_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="End date (YYYY-MM-DD)",
)
@click.option(
    "--target-dir",
    default="downloads",
    show_default=True,
    help="Directory to save recordings",
)
@click.option("--host-email", default=None, help="Filter recordings by host email")
@click.option("--meeting-id", default=None, help="Filter recordings by meeting id or UUID")
@click.option("--dry-run", is_flag=True, default=False, help="List recordings without downloading")
@click.option("--overwrite", is_flag=True, default=False, help="Overwrite existing files")
@click.option(
    "--log-level",
    type=click.Choice(["debug", "info", "warning", "error", "critical"], case_sensitive=False),
    default="info",
    show_default=True,
    help="Logging verbosity",
)
@click.option(
    "--log-format",
    type=click.Choice(["auto", "json", "text"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Logging output format",
)
def cli(  # noqa: PLR0913
    from_date: datetime | None,
    to_date: datetime | None,
    target_dir: str,
    host_email: str | None,
    meeting_id: str | None,
    dry_run: bool,
    overwrite: bool,
    log_level: str,
    log_format: str,
) -> None:
    """Run the CLI workflow to fetch and optionally download Zoom recordings.

    Args:
        from_date: Optional start date filter.
        to_date: Optional end date filter.
        target_dir: Destination directory for downloaded recordings.
        host_email: Optional host email filter.
        meeting_id: Optional meeting identifier filter.
        dry_run: When ``True``, skip downloading and report planned work.
        overwrite: When ``True``, replace existing files on disk.
        log_level: Logging verbosity level supplied by the user.
        log_format: Preferred logging output format.
    """
    configure_logging(log_level, log_format)
    logger = logging.getLogger("zoom_scribe.cli")

    if from_date and from_date.tzinfo is None:
        from_date = from_date.replace(tzinfo=UTC)
    if to_date and to_date.tzinfo is None:
        to_date = to_date.replace(tzinfo=UTC)

    from_date_utc = ensure_utc(from_date) if from_date else None
    to_date_utc = ensure_utc(to_date) if to_date else None

    end = to_date_utc or datetime.now(UTC)
    start = from_date_utc or (end - timedelta(days=30))

    if start > end:
        raise click.BadParameter(
            "Start date must be on or before end date.",
            param_hint="--from/--to",
        )

    target_path = Path(target_dir)
    if target_path.exists() and not target_path.is_dir():
        raise click.BadParameter(
            "Target path exists and is not a directory.", param_hint="--target-dir"
        )

    logger.info(
        "cli.invoke",
        extra={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "host_email": redact_identifier(host_email),
            "meeting_id": redact_identifier(meeting_id),
            "dry_run": dry_run,
            "overwrite": overwrite,
            "target_dir": target_dir,
        },
    )

    client = create_client()
    downloader = create_downloader(client, logger=logger)

    recordings = client.list_recordings(
        start=start,
        end=end,
        host_email=host_email,
        meeting_id=meeting_id,
    )

    downloader.download(recordings, target_path, dry_run=dry_run, overwrite=overwrite)

    if dry_run:
        click.echo(f"Dry run complete. {len(recordings)} recordings would be processed.")
    else:
        click.echo(f"Downloaded {len(recordings)} recordings.")


if __name__ == "__main__":
    cli()
