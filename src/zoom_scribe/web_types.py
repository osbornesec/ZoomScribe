"""Pydantic models shared by the web API and frontend clients."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from .models import Recording


class ApiError(BaseModel):
    """Serialised representation of an API error response."""

    model_config = ConfigDict(extra="forbid")

    message: str
    code: str | None = None


class DownloadRequest(BaseModel):
    """Request payload for triggering a download."""

    model_config = ConfigDict(extra="forbid")

    meeting_id_or_uuid: str
    overwrite: bool | None = None
    target_dir: str | None = None


class DownloadResponse(BaseModel):
    """Response describing the outcome of a download trigger."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    files_expected: int
    note: str | None = None


class RecordingSummary(BaseModel):
    """Lightweight view of a meeting recording for list views."""

    model_config = ConfigDict(extra="forbid")

    uuid: str
    meeting_id: str | None
    topic: str
    host_email: str | None
    start_time: datetime
    duration_minutes: int | None
    asset_count: int
    total_size_bytes: int

    @classmethod
    def from_recording(cls, recording: Recording) -> "RecordingSummary":
        """Translate a ``Recording`` domain model into a summary for the API."""
        total_size = sum(file.file_size or 0 for file in recording.files)
        asset_count = len(recording.files)
        return cls(
            uuid=recording.uuid,
            meeting_id=None,
            topic=recording.meeting_topic,
            host_email=recording.host_email or None,
            start_time=recording.start_time.astimezone(UTC),
            duration_minutes=recording.duration_minutes,
            asset_count=asset_count,
            total_size_bytes=total_size,
        )


__all__ = [
    "ApiError",
    "DownloadRequest",
    "DownloadResponse",
    "RecordingSummary",
]
