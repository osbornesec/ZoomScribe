"""FastAPI application exposing a lightweight ZoomScribe web interface."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from threading import RLock
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ._datetime import ensure_utc
from .client import (
    MissingCredentialsError,
    ZoomAPIClient,
    ZoomAPIError,
    ZoomAuthError,
    ZoomNotFoundError,
    ZoomRateLimitError,
)
from .config import (
    Config,
    ConfigurationError,
    DownloaderConfig,
    LoggingConfig,
    load_oauth_credentials,
)
from .downloader import DownloadError
from .main import configure_logging, create_client, create_downloader
from .models import Recording
from .web_types import ApiError, DownloadRequest, DownloadResponse, RecordingSummary

_LOGGER = logging.getLogger(__name__)
_ALLOWED_ORIGINS = ["http://localhost:5173"]
_DEFAULT_RANGE_DAYS = 30
_DOWNLOAD_MEETING_LOOKBACK_DAYS = 365
_STATIC_ROOT = Path("web/dist")


@dataclass(frozen=True, slots=True)
class ApiContext:
    """Container bundling application config and a shared API client."""

    config: Config
    client: ZoomAPIClient


_CONTEXT_LOCK = RLock()
_CACHED_CONTEXT: ApiContext | None = None
_LOGGING_INITIALISED = False

app = FastAPI(title="ZoomScribe Web API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

if _STATIC_ROOT.exists():
    app.mount("/", StaticFiles(directory=_STATIC_ROOT, html=True), name="static")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Return a trivial health payload used by readiness checks."""
    return {"status": "ok"}


def _build_context() -> ApiContext:
    """Instantiate and cache the shared Config/Zoom client pair."""
    global _LOGGING_INITIALISED  # noqa: PLW0603
    credentials = load_oauth_credentials()
    config = Config(credentials=credentials, logging=LoggingConfig())
    if not _LOGGING_INITIALISED:
        configure_logging(config.logging)
        _LOGGING_INITIALISED = True
    client = create_client(config)
    return ApiContext(config, client)


def _http_error(status_code: int, message: str, *, code: str | None = None) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"message": message, "code": code})


def _resolve_date_range(
    from_date: date | None,
    to_date: date | None,
) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    end = datetime.combine(to_date, time.max, tzinfo=UTC) if to_date else now
    start_default = end - timedelta(days=_DEFAULT_RANGE_DAYS)
    start = datetime.combine(from_date, time.min, tzinfo=UTC) if from_date else start_default
    if start > end:
        raise _http_error(400, "Start date must be on or before end date")
    return ensure_utc(start, assume_utc_if_naive=True), ensure_utc(end, assume_utc_if_naive=True)


def _build_download_config(
    base: Config,
    *,
    target_dir_override: str | None,
    overwrite: bool | None,
) -> Config:
    target_dir = Path(target_dir_override) if target_dir_override else base.downloader.target_dir
    overwrite_value = base.downloader.overwrite if overwrite is None else overwrite
    downloader = DownloaderConfig(
        target_dir=target_dir,
        overwrite=overwrite_value,
        dry_run=base.downloader.dry_run,
    )
    return replace(base, downloader=downloader)


def _fetch_recordings(
    client: ZoomAPIClient,
    *,
    start: datetime,
    end: datetime,
    host_email: str | None,
    meeting_id: str | None,
) -> list[Recording]:
    try:
        recordings = client.list_recordings(
            start=start,
            end=end,
            host_email=host_email,
            meeting_id=meeting_id,
        )
    except MissingCredentialsError as exc:  # pragma: no cover - defensive, should be caught earlier
        raise _http_error(503, "Missing Zoom OAuth credentials") from exc
    except ZoomAuthError as exc:
        raise _http_error(401, "Zoom authentication failed", code="zoom_auth") from exc
    except ZoomRateLimitError as exc:
        raise _http_error(
            429,
            "Zoom rate limit exceeded; retry later",
            code="zoom_rate_limit",
        ) from exc
    except ZoomNotFoundError as exc:
        raise _http_error(404, "Recording not found", code="zoom_not_found") from exc
    except ZoomAPIError as exc:
        raise _http_error(502, "Zoom API request failed", code="zoom_api") from exc
    return recordings


def get_api_context() -> ApiContext:
    """Return a cached ApiContext instance, initialising it on first use."""
    global _CACHED_CONTEXT  # noqa: PLW0603
    with _CONTEXT_LOCK:
        if _CACHED_CONTEXT is None:
            try:
                _CACHED_CONTEXT = _build_context()
            except ConfigurationError as exc:
                raise _http_error(503, "Missing Zoom OAuth credentials") from exc
        return _CACHED_CONTEXT


@app.exception_handler(HTTPException)
async def handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
    """Normalise HTTPException responses so they always match ApiError."""
    detail = exc.detail
    if isinstance(detail, dict):
        message = str(detail.get("message", "")) or "Unknown error"
        code = detail.get("code")
    else:
        message = str(detail) or "Unknown error"
        code = None
    payload = ApiError(message=message, code=code).model_dump()
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, _exc: Exception) -> JSONResponse:
    """Catch-all exception handler that redacts details from clients."""
    _LOGGER.exception("web_api.unhandled_exception", extra={"path": request.url.path})
    payload = ApiError(message="Internal server error").model_dump()
    return JSONResponse(status_code=500, content=payload)


@app.get("/api/recordings", response_model=list[RecordingSummary])
def list_recordings(
    context: Annotated[ApiContext, Depends(get_api_context)],
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    host_email: str | None = None,
    meeting_id: str | None = None,
) -> list[RecordingSummary]:
    """Return a filtered list of recording summaries."""
    host_email_normalised = host_email.strip() if host_email else None
    meeting_id_normalised = meeting_id.strip() if meeting_id else None
    start, end = _resolve_date_range(from_date, to_date)
    recordings = _fetch_recordings(
        context.client,
        start=start,
        end=end,
        host_email=host_email_normalised,
        meeting_id=meeting_id_normalised,
    )
    return [RecordingSummary.from_recording(recording) for recording in recordings]


@app.post("/api/download", response_model=DownloadResponse)
def trigger_download(
    context: Annotated[ApiContext, Depends(get_api_context)],
    request: DownloadRequest,
) -> DownloadResponse:
    """Trigger a synchronous download of the requested meeting recordings."""
    meeting_identifier = request.meeting_id_or_uuid.strip()
    if not meeting_identifier:
        raise _http_error(400, "meeting_id_or_uuid is required")

    now = datetime.now(UTC)
    start = now - timedelta(days=_DOWNLOAD_MEETING_LOOKBACK_DAYS)
    recordings = _fetch_recordings(
        context.client,
        start=start,
        end=now,
        host_email=None,
        meeting_id=meeting_identifier,
    )

    if not recordings:
        raise _http_error(404, "Recording not found", code="zoom_not_found")

    files_expected = sum(len(recording.files) for recording in recordings)
    effective_config = _build_download_config(
        context.config,
        target_dir_override=request.target_dir,
        overwrite=request.overwrite,
    )

    downloader = create_downloader(effective_config, context.client)

    try:
        downloader.download(recordings)
    except DownloadError as exc:
        raise _http_error(
            500,
            "Failed to download one or more files",
            code="download_failed",
        ) from exc

    notes: list[str] = []
    if downloader.config.dry_run:
        notes.append("Dry run enabled; no files written.")
    if files_expected == 0:
        notes.append("Recording does not include downloadable files.")

    note = " ".join(notes) if notes else None
    return DownloadResponse(ok=True, files_expected=files_expected, note=note)


__all__ = ["app", "get_api_context"]
