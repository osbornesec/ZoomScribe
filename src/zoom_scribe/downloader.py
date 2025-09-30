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
    """
    Create a filesystem-safe single path component from an input string.
    
    Replaces characters that are not allowed in the module's sanitized name set with underscores, collapses runs of three or more underscores to two underscores, and converts a string composed solely of dots to a single underscore. If the input is empty or the result is empty after sanitization, returns "unknown".
    
    Parameters:
        value (str): The input string to sanitize.
    
    Returns:
        str: A sanitized path component safe for use on filesystems.
    """
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
        """
        Download the provided recordings into a filesystem tree rooted at `target_dir`.
        
        Parameters:
            recordings (Sequence[Recording]): Iterable of recordings to persist; each recording's
                .recording_files sequence will be processed.
            target_dir (str | Path): Root directory under which files will be placed.
            dry_run (bool): If True, no files are written; progress events are logged for each
                would-be destination.
            overwrite (bool): If True, existing files at a destination will be replaced.
            post_download (PostDownloadHook | None): Optional callback invoked after each
                recording file is processed (including when a file is skipped due to
                existing content). The hook is called from worker threads and is not invoked
                during dry runs.
        
        Side effects:
            - Writes files to disk using atomic ".part" temporary files and then renames them
              into place.
            - Creates parent directories as needed.
            - Emits structured progress logs.
            - Executes `post_download` concurrently from worker threads when provided.
        
        Concurrency:
            Downloads are performed using a ThreadPoolExecutor with `self.max_workers` workers;
            failures cancel remaining in-flight tasks.
        
        Raises:
            DownloadError: If any recording file fails to download or persist; the exception
            message will include the failing recording_file.id.
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
        """
        Write a single recording file to `destination` using an atomic write (temporary “.part” file) and invoke an optional post-download hook.
        
        Parameters:
            recording (Recording): Recording metadata used for logging and hook context.
            recording_file (RecordingFile): File metadata and remote location used to fetch contents.
            destination (Path): Final on-disk path for the file; parent directories will be created if needed.
            overwrite (bool): If False and `destination` already exists, the file is skipped and treated as successful.
            post_download (PostDownloadHook | None): Optional callable invoked after the file is processed; exceptions raised by the hook are caught and not propagated.
        
        Returns:
            Path: The final destination path (existing or newly written).
        
        Side effects and behavior:
            - Creates destination.parent if it does not exist.
            - Writes to a temporary file (destination + ".part") and atomically replaces `destination` on success.
            - If `destination` exists and `overwrite` is False, the file is not downloaded; the post-download hook is still invoked.
            - On error during download or write, the temporary file is removed when possible and the original exception is re-raised.
            - The method logs progress events for skip, download, and overwrite.
        
        Preconditions and concurrency:
            - Caller should ensure the target filesystem is writable and has sufficient space.
            - Concurrent invocations targeting the same `destination` may race; atomic replacement avoids partial writes but callers should coordinate to avoid lost updates.
        """
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
        """
        Call the provided post-download hook for a recording file, logging and suppressing any exception it raises.
        
        If `hook` is None this is a no-op. If `hook` raises an exception, the exception is caught and an error is logged with contextual, redacted identifiers (destination path, recording id, recording_file id); the exception is not propagated.
        
        Parameters:
            hook (PostDownloadHook | None): Callable invoked with (destination, recording, recording_file) or None to skip.
            destination (Path): Final file path passed to the hook.
            recording (Recording): Recording metadata associated with the file.
            recording_file (RecordingFile): Metadata for the specific recording file.
        
        Side effects:
            - Executes `hook` synchronously on the current thread.
            - On hook failure, emits a structured log entry and continues without raising.
        """
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
        """
        Retrieve and yield binary chunks for a recording file's payload.
        
        Requests the recording payload from the configured client and yields normalized bytes chunks that represent the file content.
        
        Parameters:
            recording_file (RecordingFile): Metadata for the recording file used to locate and access the remote asset.
        
        Returns:
            Iterable[bytes]: An iterator that yields successive byte chunks of the recording file.
        """
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
