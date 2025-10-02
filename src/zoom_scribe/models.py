"""Typed data models and validation helpers for Zoom API responses."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

JsonMapping = Mapping[str, Any]


class ModelValidationError(ValueError):
    """Raised when a Zoom API payload is missing required fields."""


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO 8601 timestamp string into a timezone-aware datetime.

    Args:
        value: Raw timestamp returned by the Zoom API. Expected to be either
            a ``YYYY-MM-DDTHH:MM:SSZ`` value or a full ISO 8601 string with
            an explicit offset.

    Returns:
        A ``datetime`` instance normalised to UTC.

    Raises:
        ModelValidationError: If the value is empty or cannot be parsed.

    Examples:
        >>> _parse_datetime("2025-03-01T12:30:00Z")
        datetime.datetime(2025, 3, 1, 12, 30, tzinfo=datetime.UTC)
    """
    if not value:
        raise ModelValidationError("Expected an ISO 8601 timestamp")
    normalised = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ModelValidationError(f"Invalid datetime string: {value}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalise_optional_str(value: Any) -> str | None:
    """Return a stripped string for ``value`` or ``None`` when empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ensure_required(payload: JsonMapping, key: str) -> Any:
    """Retrieve ``key`` from ``payload`` and raise if missing or empty."""
    if key not in payload or payload[key] in (None, ""):
        raise ModelValidationError(f"Missing required field '{key}' in payload")
    return payload[key]


@dataclass(frozen=True, slots=True)
class RecordingFile:
    """Representation of a single downloadable asset attached to a meeting.

    Attributes:
        id: Zoom-assigned identifier for the recording file.
        file_type: Media type label such as ``MP4`` or ``TRANSCRIPT``.
        file_extension: File extension emitted by Zoom, normalised to lower-case.
        download_url: Direct download URL for the asset.
        download_access_token: Optional access token required for download.
        status: Optional lifecycle status reported by the API.
        file_size: File size in bytes when provided by the API.
    """

    id: str
    file_type: str
    file_extension: str
    download_url: str
    download_access_token: str | None = None
    status: str | None = None
    file_size: int | None = None

    def __post_init__(self) -> None:
        """Validate required recording file attributes."""
        if not self.id:
            raise ModelValidationError("RecordingFile.id must be populated")
        if not self.file_type:
            raise ModelValidationError("RecordingFile.file_type must be populated")
        if not self.download_url:
            raise ModelValidationError("RecordingFile.download_url must be populated")

    def __str__(self) -> str:  # pragma: no cover - trivial representation  # noqa: D105
        return f"RecordingFile(id={self.id}, type={self.file_type})"

    @classmethod
    def from_api(cls, payload: JsonMapping) -> RecordingFile:
        """Create a ``RecordingFile`` instance from a Zoom API payload.

        Args:
            payload: JSON object returned by the Zoom API describing a file.

        Returns:
            Parsed ``RecordingFile`` model populated with validated fields.

        Raises:
            ModelValidationError: If required fields are missing.
        """
        file_extension = payload.get("file_extension") or payload.get("file_type") or ""
        download_access_token = _normalise_optional_str(payload.get("download_access_token"))
        status = _normalise_optional_str(payload.get("status"))
        file_size_raw = payload.get("file_size")
        file_size = int(file_size_raw) if isinstance(file_size_raw, int | float) else None
        return cls(
            id=str(_ensure_required(payload, "id")),
            file_type=str(_ensure_required(payload, "file_type")),
            file_extension=str(file_extension).lower().lstrip("."),
            download_url=str(_ensure_required(payload, "download_url")),
            download_access_token=download_access_token,
            status=status,
            file_size=file_size,
        )


@dataclass(frozen=True, slots=True)
class Recording:
    """Aggregate model describing a meeting recording and its downloadable files.

    Attributes:
        uuid: Canonical UUID identifying the meeting instance.
        meeting_topic: Human readable meeting title.
        host_email: Host account email address.
        start_time: UTC normalised start time for the meeting.
        duration_minutes: Optional meeting duration reported by Zoom.
        recording_files: Collection of ``RecordingFile`` assets associated with the
            meeting. Stored as an immutable tuple to guarantee stability.
    """

    uuid: str
    meeting_topic: str
    host_email: str
    start_time: datetime
    duration_minutes: int | None = None
    recording_files: tuple[RecordingFile, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate required recording metadata."""
        if not self.uuid:
            raise ModelValidationError("Recording.uuid must be populated")
        if self.start_time.tzinfo is None:
            raise ModelValidationError("Recording.start_time must be timezone aware")

    def __str__(self) -> str:  # pragma: no cover - trivial representation  # noqa: D105
        return f"Recording(uuid={self.uuid}, files={len(self.recording_files)})"

    @classmethod
    def from_api(cls, payload: JsonMapping) -> Recording:
        """Create a ``Recording`` model from a raw Zoom API meeting payload.

        Args:
            payload: JSON object returned by the Zoom recordings endpoints.

        Returns:
            Populated ``Recording`` instance with validated metadata and files.

        Raises:
            ModelValidationError: If required fields are missing or invalid.
        """
        files_payload = payload.get("recording_files") or []
        files = tuple(RecordingFile.from_api(file_payload) for file_payload in files_payload)
        topic_value = _normalise_optional_str(payload.get("topic"))
        meeting_topic = topic_value or _normalise_optional_str(payload.get("meeting_topic")) or ""
        host_email = _normalise_optional_str(payload.get("host_email")) or ""
        duration_raw = payload.get("duration")
        duration_minutes = int(duration_raw) if isinstance(duration_raw, int | float) else None
        return cls(
            uuid=str(_ensure_required(payload, "uuid")),
            meeting_topic=meeting_topic,
            host_email=host_email,
            start_time=_parse_datetime(str(_ensure_required(payload, "start_time"))),
            duration_minutes=duration_minutes,
            recording_files=files,
        )

    @property
    def files(self) -> tuple[RecordingFile, ...]:
        """Alias for :attr:`recording_files` to aid readability."""
        return self.recording_files


@dataclass(frozen=True, slots=True)
class RecordingPage:
    """Container describing a page of recordings returned by the Zoom API."""

    recordings: tuple[Recording, ...]
    next_page_token: str | None = None
    total_records: int | None = None

    @classmethod
    def from_api(cls, payload: JsonMapping) -> RecordingPage:
        """Parse a paginated recordings response into a ``RecordingPage``.

        Args:
            payload: JSON object returned from the ``users/*/recordings`` endpoint.

        Returns:
            Normalised ``RecordingPage`` encapsulating recordings and pagination token.
        """
        meetings_payload = payload.get("meetings") or []
        recordings = tuple(Recording.from_api(meeting) for meeting in meetings_payload)
        next_page_token = _normalise_optional_str(payload.get("next_page_token"))
        total_records_raw = payload.get("total_records")
        total_records = (
            int(total_records_raw) if isinstance(total_records_raw, int | float) else None
        )
        return cls(
            recordings=recordings,
            next_page_token=next_page_token,
            total_records=total_records,
        )

    def has_next_page(self) -> bool:
        """Return ``True`` when an additional page token is present."""
        return bool(self.next_page_token)

    def __iter__(self) -> Iterable[Recording]:  # pragma: no cover - simple delegation  # noqa: D105
        return iter(self.recordings)

    def __len__(self) -> int:  # pragma: no cover - simple delegation  # noqa: D105
        return len(self.recordings)


__all__ = [
    "ModelValidationError",
    "Recording",
    "RecordingFile",
    "RecordingPage",
]
