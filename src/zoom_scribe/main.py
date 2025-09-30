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
    """
    Create a RecordingDownloader bound to the given Zoom API client and optional logger.
    
    Parameters:
        client (ZoomAPIClient): Configured Zoom API client used to fetch recording files.
        logger (logging.Logger | None): Optional logger for progress reporting and structured events.
    
    Returns:
        RecordingDownloader: Downloader configured with the provided client and logger.
    """
    return RecordingDownloader(client, logger=logger)


@click.group()
def cli() -> None:
    """Root entry point for ZoomScribe commands."""


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
    """
    Run the CLI workflow to list Zoom recordings and optionally download them to disk with optional screenshare preprocessing.
    
    This command configures logging, resolves the requested date range and destination, lists recordings from the Zoom API, and uses a RecordingDownloader to download files. When screenshare preprocessing is enabled, it will create a PreprocessConfig factory and invoke a per-file post-download hook that may write frame→time mapping files. The function prints a brief summary to stdout when complete and emits structured log events.
    
    Parameters:
        **options: Any
            A mapping of CLI option names to values. Recognized keys:
            - "from_date" (datetime | None): Start of the date range (assumed UTC if naive).
            - "to_date" (datetime | None): End of the date range (assumed UTC if naive).
            - "target_dir" (str): Destination directory for downloads.
            - "host_email" (str | None): Filter recordings by host email.
            - "meeting_id" (str | None): Filter recordings by meeting ID.
            - "dry_run" (bool): If true, list recordings without writing files.
            - "overwrite" (bool): If true, overwrite existing files.
            - "screenshare_preprocess" (bool): If true, run screenshare preprocessing for screenshare files.
            - "screenshare_output_dir" (Path | None): Directory to write frame mapping outputs (overrides per-download destination).
            - "screenshare_target_fps" (float): Target FPS for preprocessing.
            - "screenshare_roi_seconds" (float): Seconds of video used for ROI detection.
            - "screenshare_ssim_threshold" (float): SSIM threshold for frame similarity during bundling.
            - "screenshare_bundle_max_frames" (int): Maximum frames per preprocessing bundle.
            - "screenshare_bundle_gap" (float): Maximum time gap (seconds) to start a new bundle.
            - "log_level" (str): Logging level name.
            - "log_format" (str): Logging format ("auto", "text", or "json").
    
    Raises:
        click.BadParameter: If the resolved start date is after the end date, or if the specified target path exists but is not a directory.
        OSError: Propagates filesystem errors encountered when writing mapping files or creating directories (when not handled by internal hooks).
    
    Side effects:
        - Configures the "zoom_scribe" logger and emits structured log events.
        - Calls out to the Zoom API via create_client() and list_recordings().
        - May download files to disk and overwrite existing files depending on options.
        - When enabled, may run video preprocessing and write frame→time mapping files.
        - Prints a short completion message to stdout.
    
    Preconditions and invariants:
        - If present, provided datetimes are interpreted as UTC when naive.
        - The effective start date will be set to (end - 30 days) when no start date is supplied.
        - The target path must be a directory or not exist prior to invoking downloads.
    
    Concurrency:
        - This function is not thread-safe with respect to global logging configuration; callers should avoid concurrent invocations that reconfigure logging.
    """
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
            """
            Create a PreprocessConfig populated from the enclosing screenshare option values.
            
            Returns:
                PreprocessConfig: Configuration with:
                    - target_fps set to `screenshare_target_fps`
                    - roi_detection_duration_sec set to `screenshare_roi_seconds`
                    - ssim_threshold set to `screenshare_ssim_threshold`
                    - bundle_max_frames set to `screenshare_bundle_max_frames`
                    - bundle_max_time_gap_sec set to `screenshare_bundle_gap`
            """
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
    """
    Click command group exposing screenshare-related CLI commands.
    
    Registers screenshare-related subcommands (for example, `preprocess`) for the application's command-line interface so they can be invoked as `zoom_scribe screenshare <subcommand>`.
    """


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
    """
    Run screenshare preprocessing for a single video file and emit a frame→time mapping.
    
    Configures logging from options, builds a PreprocessConfig from provided options, runs preprocessing on the given video, builds a frame-to-time mapping, and either writes the mapping to the specified output file or prints it to stdout.
    
    Parameters:
        options (dict-like): Expect the following keys:
            - "log_level" (str): Logging level name (e.g., "INFO").
            - "log_format" (str): Logging format identifier ("auto", "json", "text").
            - "video" (Path): Path to the input video to preprocess.
            - "target_fps" (float): Target frames per second for preprocessing.
            - "roi_seconds" (float): Duration in seconds used for ROI detection.
            - "ssim_threshold" (float): SSIM threshold for frame similarity grouping.
            - "bundle_max_frames" (int): Maximum frames per bundle.
            - "bundle_gap" (float): Maximum time gap (seconds) allowed between frames in a bundle.
            - "output" (Path | None): Optional path to write the frame mapping; when omitted the mapping is printed.
    
    Side effects:
        - Configures the module logger via configure_logging.
        - May create parent directories and write the mapping file with a trailing newline if "output" is provided.
        - Writes status or mapping text to stdout via click.echo.
    
    Raises:
        click.ClickException: If preprocessing fails (wraps PreprocessingError) or if writing the output file fails due to an OSError.
    """
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
    """
    Create a post-download hook that runs screenshare preprocessing for downloaded recording files.
    
    When preprocessing is enabled and not a dry run, returns a callable that:
    - Is a no-op for files that are not identified as screenshare videos.
    - Runs preprocess_video(...) with a PreprocessConfig obtained from `config_factory()`.
    - Builds a frame→time mapping via build_frame_time_mapping(...) and writes it to a file named
      "{destination.stem}_frame_map.txt" in `destination_dir` (if provided) or alongside `destination`.
    - Ensures the mapping directory exists before writing.
    - Emits structured log events for failures and for successful completion.
    
    Parameters:
        enabled: Whether preprocessing was requested; when False the function returns None.
        dry_run: If True the function returns None (preprocessing is skipped).
        destination_dir: Optional directory where the mapping file will be written; when None the mapping
            file is written next to the downloaded `destination` file.
        config_factory: Callable returning a PreprocessConfig to use for preprocessing; when None the
            function returns None.
        logger: Logger used to record structured progress, warnings, and errors.
    
    Returns:
        A callable with signature (destination: Path, recording: Recording, recording_file: RecordingFile)
        -> None that performs the preprocessing and writes the mapping file, or `None` when preprocessing
        should be skipped because `enabled` is False, `dry_run` is True, or `config_factory` is None.
    
    Side effects:
        - May create directories (mapping parent) and write a mapping text file to disk.
        - Logs warnings on preprocessing failures and errors on file write failures.
        - Does not raise exceptions for preprocessing failures or write errors; those are logged and the
          post-download hook returns early.
    
    Preconditions and concurrency:
        - `config_factory()` must return a valid PreprocessConfig; invalid configs may cause preprocessing
          to fail and will be handled by logging.
        - The returned callable makes no explicit concurrency guarantees; callers should synchronize
          concurrent invocations if `preprocess_video` or downstream code require it.
    """
    if not enabled or dry_run or config_factory is None:
        return None

    config = config_factory()

    def _post_download(
        destination: Path,
        recording: Recording,
        recording_file: RecordingFile,
    ) -> None:
        """
        Run screenshare preprocessing for a downloaded recording file and write a frame→time mapping file.
        
        If the provided recording_file is identified as a screenshare video, this function runs preprocess_video() to produce bundles, builds a frame-to-time mapping, and writes the mapping to a file named "{destination.stem}_frame_map.txt" in the mapping directory.
        
        Parameters:
            destination (Path): Filesystem path to the downloaded recording file.
            recording (Recording): Recording metadata for the download; used only for logging.
            recording_file (RecordingFile): Metadata for the specific file; used to determine whether preprocessing should run.
        
        Side effects:
            - May create the mapping directory (mapping_parent) if it does not exist.
            - Writes a UTF-8 text file with the frame→time mapping and a trailing newline.
            - Emits structured log events:
                - "screenshare.preprocess_failed" when preprocessing fails (PreprocessingError).
                - "screenshare.mapping_write_failed" when writing the mapping file fails (OSError).
                - "screenshare.preprocess_complete" on successful completion, including destination, mapping_path, total frames, and bundle count.
        
        Error handling:
            - Catches PreprocessingError from preprocess_video(), logs a warning with redacted identifiers, and returns without raising.
            - Catches OSError when writing the mapping file, logs an error with exc_info, and returns without raising.
        """
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
    """
    Determine whether a RecordingFile represents a screenshare video.
    
    Checks the recording file metadata to decide if it is a screenshare recording: the file's type contains the substring "SCREEN" (case-insensitive) and the file extension is one of "mp4", "mkv", or "mov".
    
    Parameters:
        recording_file (RecordingFile): Recording file metadata to inspect.
    
    Returns:
        bool: `true` if the recording_file is identified as a screenshare video, `false` otherwise.
    """
    file_type = recording_file.file_type.upper()
    if "SCREEN" not in file_type:
        return False
    return recording_file.file_extension.lower() in {"mp4", "mkv", "mov"}


if __name__ == "__main__":
    cli()
