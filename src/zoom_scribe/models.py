from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO 8601 timestamp emitted by the Zoom API into an aware datetime."""
    if not value:
        raise ValueError("Expected an ISO 8601 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid datetime string: {value}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(frozen=True)
class RecordingFile:
    """Lightweight representation of a single downloadable recording asset."""

    id: str
    file_type: str
    file_extension: str
    download_url: str
    download_access_token: str | None = None

    @classmethod
    def from_api(cls, payload: Mapping[str, Any]) -> "RecordingFile":
        """Build a RecordingFile from the raw Zoom API payload."""
        file_extension = (
            payload.get("file_extension") or payload.get("file_type", "").lower()
        )
        return cls(
            id=payload["id"],
            file_type=payload["file_type"],
            file_extension=file_extension,
            download_url=payload["download_url"],
            download_access_token=payload.get("download_access_token"),
        )


@dataclass
class Recording:
    """Aggregate model describing a meeting recording and its assets."""

    uuid: str
    meeting_topic: str
    host_email: str
    start_time: datetime
    recording_files: list[RecordingFile] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: Mapping[str, Any]) -> "Recording":
        """Hydrate a Recording from the JSON object returned by Zoom."""
        files_payload = payload.get("recording_files") or []
        files = [RecordingFile.from_api(file_payload) for file_payload in files_payload]
        return cls(
            uuid=payload["uuid"],
            meeting_topic=payload.get("topic") or payload.get("meeting_topic", ""),
            host_email=payload.get("host_email", ""),
            start_time=_parse_datetime(payload["start_time"]),
            recording_files=files,
        )

    def iter_files(self) -> Iterable[RecordingFile]:
        """Return the associated recording files in iteration-friendly form."""
        return list(self.recording_files)


__all__ = ["Recording", "RecordingFile"]
