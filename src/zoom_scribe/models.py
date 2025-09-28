from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class RecordingFile:
    id: str
    file_type: str
    file_extension: str
    download_url: str
    download_access_token: str | None


@dataclass
class Recording:
    uuid: str
    topic: str
    host_email: str
    start_time: datetime
    files: list[RecordingFile]

    @classmethod
    def from_api(cls, payload: dict) -> Recording:
        files = [
            RecordingFile(
                id=f["id"],
                file_type=f["file_type"],
                file_extension=f["file_extension"],
                download_url=f["download_url"],
                download_access_token=f.get("download_access_token"),
            )
            for f in payload.get("recording_files", [])
        ]
        return cls(
            uuid=payload["uuid"],
            topic=payload["topic"],
            host_email=payload["host_email"],
            start_time=datetime.fromisoformat(payload["start_time"]),
            files=files,
        )