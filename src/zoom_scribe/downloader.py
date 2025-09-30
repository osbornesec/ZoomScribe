"""Filesystem downloader for Zoom recordings with atomic writes and logging."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import IO, Any, Protocol, runtime_checkable

from ._redact import redact_identifier, redact_uuid
from .models import Recording, RecordingFile

_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9._@-]")
_PART_SUFFIX = ".part"


@runtime_checkable
class _Readable(Protocol):
    def read(self) -> bytes:
        """Return the bytes content for a stream-like object."""


class DownloadError(RuntimeError):
    """Raised when a recording asset fails to download or persist."""


PostDownloadHook = Callable[[Path, "Recording", "RecordingFile"], None]


def _sanitize(value: str) -> str:
    """Sanitize a path component so it is safe to use on local filesystems."""
    sanitized = _SANITIZE_PATTERN.sub("_", value or "")
    if sanitized and set(sanitized) <= {"."}:
        sanitized = "_"
    sanitized = re.sub(r"_{3,}", "__", sanitized)
    return sanitized or "unknown"


class RecordingDownloader:
    """Coordinate on-disk storage for Zoom cloud recording assets."""

    def __init__(
        self,
        client: Any,
        *,
        logger: logging.Logger | None = None,
        max_workers: int = 2,
        progress_stream: IO[str] | None = None,
    ) -> None:
        """Initialise the downloader with a client capable of fetching bytes."""
        self.client = client
        self.logger = logger or logging.getLogger(__name__)
        self.max_workers = max(1, int(max_workers))
        self._progress_stream = progress_stream or sys.stderr
        self._progress_isatty = bool(getattr(self._progress_stream, "isatty", lambda: False)())

    def build_file_path(
        self,
        recording: Recording,
        recording_file: RecordingFile,
        target_dir: str | Path,
    ) -> Path:
        """Return the destination path for a recording file within ``target_dir``."""
        target = Path(target_dir)
        start = recording.start_time
        host_dir = _sanitize(recording.host_email)
        topic_dir = _sanitize(f"{recording.meeting_topic}-{recording.uuid}")
        dated_path = (
            target
            / host_dir
            / f"{start.year:04d}"
            / f"{start.month:02d}"
            / f"{start.day:02d}"
            / topic_dir
        )
        timestamp = start.strftime("%Y-%m-%dT%H-%M-%S")
        extension = recording_file.file_extension.lstrip(".")
        filename = f"{recording_file.file_type}-{timestamp}.{extension}"
        return dated_path / filename

    def download(
        self,
        recordings: Sequence[Recording],
        target_dir: str | Path,
        *,
        dry_run: bool = False,
        overwrite: bool = False,
        post_download: PostDownloadHook | None = None,
    ) -> None:
        """Download the supplied recordings into ``target_dir`` respecting flags.

        Args:
            recordings: Collection of meeting recordings to persist.
            target_dir: Root directory used for on-disk storage.
            dry_run: When ``True`` no files are written, only progress is logged.
            overwrite: When ``True`` existing files are replaced with freshly
                downloaded data.
            post_download: Optional callback invoked after each recording file is
                processed (including skips). Not executed during dry runs.
        """
        if dry_run:
            for recording in recordings:
                for recording_file in recording.recording_files:
                    destination = self.build_file_path(recording, recording_file, target_dir)
                    self._log_progress(
                        "dry_run",
                        destination,
                        recording,
                        recording_file,
                    )
            return

        futures: dict[Future[Path], tuple[Recording, RecordingFile, Path]] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            for recording in recordings:
                for recording_file in recording.recording_files:
                    destination = self.build_file_path(recording, recording_file, target_dir)
                    future = executor.submit(
                        self._download_single,
                        recording,
                        recording_file,
                        destination,
                        overwrite,
                        post_download,
                    )
                    futures[future] = (recording, recording_file, destination)

            for future in as_completed(futures):
                recording, recording_file, _ = futures[future]
                try:
                    future.result()
                except Exception as exc:  # pragma: no cover - futures propagate
                    for pending in futures:
                        if not pending.done():
                            pending.cancel()
                    raise DownloadError(f"Failed to download {recording_file.id}") from exc

    def _download_single(
        self,
        recording: Recording,
        recording_file: RecordingFile,
        destination: Path,
        overwrite: bool,
        post_download: PostDownloadHook | None,
    ) -> Path:
        """Download a single recording file atomically to ``destination``."""
        existed_before = destination.exists()
        if existed_before and not overwrite:
            self._log_progress("skip_existing", destination, recording, recording_file)
            self._invoke_post_download(post_download, destination, recording, recording_file)
            return destination

        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_suffix(destination.suffix + _PART_SUFFIX)
        if temp_path.exists():
            temp_path.unlink()

        try:
            with temp_path.open("wb") as temp_file:
                for chunk in self._download_contents(recording_file):
                    temp_file.write(chunk)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            temp_path.replace(destination)
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                temp_path.unlink()
            raise

        event = "overwritten" if existed_before else "downloaded"
        self._log_progress(event, destination, recording, recording_file)
        self._invoke_post_download(post_download, destination, recording, recording_file)
        return destination

    def _invoke_post_download(
        self,
        hook: PostDownloadHook | None,
        destination: Path,
        recording: Recording,
        recording_file: RecordingFile,
    ) -> None:
        """Invoke ``hook`` and suppress exceptions with logging."""
        if hook is None:
            return
        try:
            hook(destination, recording, recording_file)
        except Exception:  # pragma: no cover - defensive logging path
            self.logger.exception(
                "screenshare.post_download_failed",
                extra={
                    "destination": str(destination),
                    "recording_id": redact_identifier(recording.uuid),
                    "recording_file_id": redact_identifier(recording_file.id),
                },
            )

    def _download_contents(self, recording_file: RecordingFile) -> Iterable[bytes]:
        """Yield the binary payload for ``recording_file`` in chunks."""
        download_file = getattr(self.client, "download_file", None)
        if callable(download_file):
            data = download_file(
                url=recording_file.download_url,
                access_token=recording_file.download_access_token,
            )
        else:
            data = self.client.download_recording_file(recording_file)
        yield from self._iterate_bytes(data)

    def _iterate_bytes(self, data: Any) -> Iterable[bytes]:
        """Normalise download payloads into a byte iterator."""
        if isinstance(data, (bytes, bytearray, memoryview)):
            yield bytes(data)
            return
        if isinstance(data, _Readable):
            chunk = data.read()
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError("Stream read must return bytes-like data")
            yield bytes(chunk)
            return
        if isinstance(data, Iterable):
            for chunk in data:
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise TypeError("Download chunks must be bytes-like")
                yield bytes(chunk)
            return
        raise TypeError("Expected bytes or iterable of bytes from client download")

    def _log_progress(
        self,
        event: str,
        destination: Path,
        recording: Recording,
        recording_file: RecordingFile,
    ) -> None:
        """Emit structured progress updates respecting TTY settings."""
        extra = {
            "event": event,
            "path": str(destination),
            "recording_uuid": redact_uuid(recording.uuid),
            "recording_file_id": redact_identifier(recording_file.id),
            "file_type": recording_file.file_type,
            "host_email": redact_identifier(recording.host_email),
            "meeting_topic": redact_identifier(recording.meeting_topic),
        }
        if self._progress_isatty:
            pretty_event = event.replace("_", " ").title()
            self.logger.info(f"{pretty_event}: {destination}", extra=extra)
        else:
            self.logger.info(f"downloader.{event}", extra=extra)


__all__ = ["DownloadError", "RecordingDownloader", "_sanitize"]
