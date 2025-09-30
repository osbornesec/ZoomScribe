"""Command-line interface for ZoomScribe."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

import click

from ._datetime import ensure_utc
from ._redact import redact_identifier
from .client import ZoomAPIClient
from .downloader import RecordingDownloader
from .models import Recording, RecordingFile
from .screenshare.preprocess import (
    PreprocessConfig,
    PreprocessingError,
    build_frame_time_mapping,
    preprocess_video,
)

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

REDACTED_EXTRA_FIELDS: Final[set[str]] = {
    "host_email",
    "meeting_id",
    "meeting_topic",
    "recording_uuid",
    "recording_file_id",
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
            if key in REDACTED_EXTRA_FIELDS:
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


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Root entry point for ZoomScribe commands."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(download)


@cli.command()
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
    "--screenshare-preprocess/--no-screenshare-preprocess",
    default=False,
    show_default=True,
    help="Run screenshare preprocessing on shared-screen video files after download",
)
@click.option(
    "--screenshare-output-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory to write screenshare frame→time mapping files (defaults to alongside video)",
)
@click.option(
    "--screenshare-target-fps",
    type=float,
    default=PreprocessConfig().target_fps,
    show_default=True,
    help="Target FPS for screenshare sampling when preprocessing",
)
@click.option(
    "--screenshare-roi-seconds",
    type=float,
    default=PreprocessConfig().roi_detection_duration_sec,
    show_default=True,
    help="Window (seconds) considered for ROI detection",
)
@click.option(
    "--screenshare-ssim-threshold",
    type=float,
    default=PreprocessConfig().ssim_threshold,
    show_default=True,
    help="Minimum (1-SSIM) delta required to keep a frame",
)
@click.option(
    "--screenshare-bundle-max-frames",
    type=int,
    default=PreprocessConfig().bundle_max_frames,
    show_default=True,
    help="Maximum frames per bundle during preprocessing",
)
@click.option(
    "--screenshare-bundle-gap",
    type=float,
    default=PreprocessConfig().bundle_max_time_gap_sec,
    show_default=True,
    help="Maximum time gap (seconds) allowed within a bundle",
)
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
def download(**options: Any) -> None:
    """Run the CLI workflow to fetch and optionally download Zoom recordings."""
    configure_logging(
        cast(str, options["log_level"]),
        cast(str, options["log_format"]),
    )
    logger = logging.getLogger("zoom_scribe.cli")

    from_date = cast(datetime | None, options["from_date"])
    to_date = cast(datetime | None, options["to_date"])
    target_dir = cast(str, options["target_dir"])
    host_email = cast(str | None, options["host_email"])
    meeting_id = cast(str | None, options["meeting_id"])
    dry_run = cast(bool, options["dry_run"])
    overwrite = cast(bool, options["overwrite"])
    screenshare_preprocess = cast(bool, options["screenshare_preprocess"])
    screenshare_output_dir = cast(Path | None, options["screenshare_output_dir"])
    screenshare_target_fps = cast(float, options["screenshare_target_fps"])
    screenshare_roi_seconds = cast(float, options["screenshare_roi_seconds"])
    screenshare_ssim_threshold = cast(float, options["screenshare_ssim_threshold"])
    screenshare_bundle_max_frames = cast(int, options["screenshare_bundle_max_frames"])
    screenshare_bundle_gap = cast(float, options["screenshare_bundle_gap"])

    from_date_utc = ensure_utc(from_date, assume_utc_if_naive=True) if from_date else None
    to_date_utc = ensure_utc(to_date, assume_utc_if_naive=True) if to_date else None

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
            "screenshare_preprocess": screenshare_preprocess,
        },
    )

    config_factory: Callable[[], PreprocessConfig] | None = None
    if screenshare_preprocess:

        def _factory() -> PreprocessConfig:
            return PreprocessConfig(
                target_fps=screenshare_target_fps,
                roi_detection_duration_sec=screenshare_roi_seconds,
                ssim_threshold=screenshare_ssim_threshold,
                bundle_max_frames=screenshare_bundle_max_frames,
                bundle_max_time_gap_sec=screenshare_bundle_gap,
            )

        config_factory = _factory

    post_download = _build_screenshare_post_download(
        enabled=screenshare_preprocess,
        dry_run=dry_run,
        destination_dir=screenshare_output_dir,
        config_factory=config_factory,
        logger=logger,
    )

    client = create_client()
    downloader = create_downloader(client, logger=logger)

    recordings = client.list_recordings(
        start=start,
        end=end,
        host_email=host_email,
        meeting_id=meeting_id,
    )

    downloader.download(
        recordings,
        target_path,
        dry_run=dry_run,
        overwrite=overwrite,
        post_download=post_download,
    )

    if dry_run:
        click.echo(f"Dry run complete. {len(recordings)} recordings would be processed.")
    else:
        click.echo(f"Downloaded {len(recordings)} recordings.")


@cli.group()
def screenshare() -> None:
    """Screenshare preprocessing utilities."""


@screenshare.command("preprocess")
@click.argument("video", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option(
    "--target-fps",
    type=float,
    default=PreprocessConfig().target_fps,
    show_default=True,
    help="Target sampling FPS",
)
@click.option(
    "--roi-seconds",
    type=float,
    default=PreprocessConfig().roi_detection_duration_sec,
    show_default=True,
    help="Duration (seconds) inspected for ROI detection",
)
@click.option(
    "--ssim-threshold",
    type=float,
    default=PreprocessConfig().ssim_threshold,
    show_default=True,
    help="Minimum (1-SSIM) delta required to keep a frame",
)
@click.option(
    "--bundle-max-frames",
    type=int,
    default=PreprocessConfig().bundle_max_frames,
    show_default=True,
    help="Maximum frames per bundle",
)
@click.option(
    "--bundle-gap",
    type=float,
    default=PreprocessConfig().bundle_max_time_gap_sec,
    show_default=True,
    help="Maximum inter-frame gap (seconds) within a bundle",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Optional destination file for the frame→time mapping",
)
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
def preprocess_command(**options: Any) -> None:
    """Run standalone screenshare preprocessing for a single video."""
    configure_logging(
        cast(str, options["log_level"]),
        cast(str, options["log_format"]),
    )

    video = cast(Path, options["video"])
    config = PreprocessConfig(
        target_fps=cast(float, options["target_fps"]),
        roi_detection_duration_sec=cast(float, options["roi_seconds"]),
        ssim_threshold=cast(float, options["ssim_threshold"]),
        bundle_max_frames=cast(int, options["bundle_max_frames"]),
        bundle_max_time_gap_sec=cast(float, options["bundle_gap"]),
    )
    output = cast(Path | None, options["output"])

    try:
        bundles = preprocess_video(video, config)
    except PreprocessingError as exc:  # pragma: no cover - exercised via tests
        raise click.ClickException(str(exc)) from exc

    mapping = build_frame_time_mapping(bundles)

    if output:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(mapping + "\n", encoding="utf-8")
        except OSError as exc:  # pragma: no cover - filesystem dependent
            raise click.ClickException(
                f"Failed to write frame mapping to {output}: {exc}"
            ) from exc
        click.echo(f"Frame mapping written to {output}")
    else:
        click.echo(mapping)


def _build_screenshare_post_download(
    *,
    enabled: bool,
    dry_run: bool,
    destination_dir: Path | None,
    config_factory: Callable[[], PreprocessConfig] | None,
    logger: logging.Logger,
) -> Callable[[Path, Recording, RecordingFile], None] | None:
    """Create a post-download hook that runs screenshare preprocessing.

    Args:
        enabled: Whether preprocessing is requested by the caller.
        dry_run: Whether the downloader is running in dry-run mode.
        destination_dir: Optional directory for mapping outputs.
        config_factory: Factory returning the preprocessing configuration.
        logger: Logger used for structured progress and error reporting.

    Returns:
        Callable that performs preprocessing for each recording file, or ``None``
        when preprocessing should be skipped.
    """
    if not enabled or dry_run or config_factory is None:
        return None

    config = config_factory()

    def _post_download(
        destination: Path,
        recording: Recording,
        recording_file: RecordingFile,
    ) -> None:
        """Process a downloaded recording file to emit screenshare bundles."""
        if not _is_screenshare_file(recording_file):
            return
        mapping_parent = destination_dir or destination.parent
        mapping_parent.mkdir(parents=True, exist_ok=True)
        try:
            bundles = preprocess_video(destination, config)
        except PreprocessingError as exc:
            logger.warning(
                "screenshare.preprocess_failed",
                extra={
                    "destination": str(destination),
                    "error": str(exc),
                    "recording_id": redact_identifier(recording.uuid),
                    "recording_file_id": redact_identifier(recording_file.id),
                },
            )
            return

        mapping_text = build_frame_time_mapping(bundles)
        mapping_path = mapping_parent / f"{destination.stem}_frame_map.txt"
        try:
            mapping_path.write_text(mapping_text + "\n", encoding="utf-8")
        except OSError as exc:  # pragma: no cover - filesystem dependent
            logger.error(
                "screenshare.mapping_write_failed",
                exc_info=True,
                extra={
                    "destination": str(destination),
                    "mapping_path": str(mapping_path),
                    "error": str(exc),
                },
            )
            return
        logger.info(
            "screenshare.preprocess_complete",
            extra={
                "destination": str(destination),
                "mapping_path": str(mapping_path),
                "frames": sum(len(bundle.frames) for bundle in bundles),
                "bundles": len(bundles),
            },
        )

    return _post_download


def _is_screenshare_file(recording_file: RecordingFile) -> bool:
    """Return ``True`` when ``recording_file`` represents a screenshare video."""
    file_type = recording_file.file_type.upper()
    if "SCREEN" not in file_type:
        return False
    return recording_file.file_extension.lower() in {"mp4", "mkv", "mov"}


if __name__ == "__main__":
    cli()
