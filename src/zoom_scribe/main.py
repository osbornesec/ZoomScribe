from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import click

from .client import ZoomAPIClient
from .downloader import RecordingDownloader


def create_client() -> ZoomAPIClient:
    """Instantiate a ZoomAPIClient configured via environment variables."""
    return ZoomAPIClient.from_env()


def create_downloader(
    client: ZoomAPIClient, logger: logging.Logger | None = None
) -> RecordingDownloader:
    """Build a RecordingDownloader bound to the provided client and logger."""
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
    """CLI entrypoint for downloading Zoom cloud recordings to the local filesystem."""
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    logger = logging.getLogger("zoom_scribe.cli")

    start = from_date or (datetime.now(UTC) - timedelta(days=30))
    end = to_date or datetime.now(UTC)

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
