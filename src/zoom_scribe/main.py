from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import click

from ._datetime import ensure_utc
from .client import ZoomAPIClient
from .downloader import RecordingDownloader


def create_client() -> ZoomAPIClient:
    """
    Instantiate a ZoomAPIClient configured from environment variables.

    Returns:
        ZoomAPIClient: A client configured using credentials and settings read from the environment.
    """
    return ZoomAPIClient.from_env()


def create_downloader(
    client: ZoomAPIClient, logger: logging.Logger | None = None
) -> RecordingDownloader:
    """
    Create a RecordingDownloader configured with the given ZoomAPIClient and optional logger.

    Parameters:
        logger (logging.Logger | None): Optional logger instance that the downloader will use for logging.

    Returns:
        RecordingDownloader: A downloader instance bound to the provided client and logger.
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
@click.option(
    "--meeting-id", default=None, help="Filter recordings by meeting id or UUID"
)
@click.option(
    "--dry-run", is_flag=True, default=False, help="List recordings without downloading"
)
@click.option(
    "--overwrite", is_flag=True, default=False, help="Overwrite existing files"
)
def cli(
    from_date: datetime | None,
    to_date: datetime | None,
    target_dir: str,
    host_email: str | None,
    meeting_id: str | None,
    dry_run: bool,
    overwrite: bool,
) -> None:
    """
    Run the CLI workflow to fetch Zoom cloud recordings for a date range and save them to disk.

    Parameters:
        from_date (datetime | None): Start of the date range (UTC). If None, defaults to 30 days before now.
        to_date (datetime | None): End of the date range (UTC). If None, defaults to now.
        target_dir (str): Filesystem directory where recordings will be saved.
        host_email (str | None): If set, filter recordings to meetings hosted by this email.
        meeting_id (str | None): If set, filter recordings to this meeting ID or UUID.
        dry_run (bool): If True, list matching recordings without downloading them.
        overwrite (bool): If True, overwrite existing files when downloading.

    Description:
        Configures logging, creates a Zoom API client and a RecordingDownloader, queries recordings
        within the specified UTC date range using the provided filters, and either performs downloads
        to `target_dir` or reports what would be processed when `dry_run` is True. Outputs a summary
        message via the CLI on completion.
    """
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    logger = logging.getLogger("zoom_scribe.cli")

    from_date_utc = ensure_utc(from_date) if from_date else None
    to_date_utc = ensure_utc(to_date) if to_date else None

    start = from_date_utc or (datetime.now(UTC) - timedelta(days=30))
    end = to_date_utc or datetime.now(UTC)

    logger.info(
        "cli.invoke",
        extra={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "host_email": host_email,
            "meeting_id": meeting_id,
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

    downloader.download(recordings, target_dir, dry_run=dry_run, overwrite=overwrite)

    if dry_run:
        click.echo(
            f"Dry run complete. {len(recordings)} recordings would be processed."
        )
    else:
        click.echo(f"Downloaded {len(recordings)} recordings.")


if __name__ == "__main__":
    cli()
