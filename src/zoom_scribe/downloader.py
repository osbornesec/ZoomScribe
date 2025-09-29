from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from .models import Recording, RecordingFile

_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9._@-]")


@runtime_checkable
class _Readable(Protocol):
    def read(self) -> bytes:
        """Return the bytes content for a stream-like object."""
        ...


def _sanitize(value: str) -> str:
    """Sanitize a path component so it is safe to use on local filesystems."""
    sanitized = _SANITIZE_PATTERN.sub("_", value or "")
    if sanitized and set(sanitized) <= {"."}:
        sanitized = "_"
    sanitized = re.sub(r"_{3,}", "__", sanitized)
    return sanitized or "unknown"


class RecordingDownloader:
    """Coordinate on-disk storage for Zoom cloud recording assets."""

    def __init__(self, client, logger: logging.Logger | None = None) -> None:
        """Initialise the downloader with a client capable of fetching bytes."""
        self.client = client
        self.logger = logger or logging.getLogger(__name__)

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
    ) -> None:
        """Download the supplied recordings into ``target_dir`` respecting flags."""
        for recording in recordings:
            for recording_file in recording.recording_files:
                destination = self.build_file_path(
                    recording, recording_file, target_dir
                )
                if dry_run:
                    self.logger.info(
                        "downloader.dry_run", extra={"path": str(destination)}
                    )
                    continue
                if Path.exists(destination) and not overwrite:
                    self.logger.info(
                        "downloader.skip_existing",
                        extra={"path": str(destination)},
                    )
                    continue
                Path.mkdir(destination.parent, parents=True, exist_ok=True)
                content = self._download_contents(recording_file)
                Path.write_bytes(destination, content)
                self.logger.info(
                    "downloader.downloaded", extra={"path": str(destination)}
                )

    def _download_contents(self, recording_file: RecordingFile) -> bytes:
        """Fetch the binary payload for ``recording_file`` using the backing client."""
        download_file = getattr(self.client, "download_file", None)
        if callable(download_file):
            data = download_file(
                url=recording_file.download_url,
                access_token=recording_file.download_access_token,
            )
        else:
            data = self.client.download_recording_file(recording_file)
        if isinstance(data, bytes):
            return data
        if isinstance(data, _Readable):
            return data.read()
        if isinstance(data, Iterable):
            chunks = cast(Iterable[bytes], data)
            return b"".join(chunks)
        raise TypeError("Expected bytes or iterable of bytes from client download")
