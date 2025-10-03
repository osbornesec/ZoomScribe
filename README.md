# ZoomScribe

ZoomScribe is a command-line tool that authenticates against the Zoom API to list and download cloud recording assets for archival or offline processing. Phase 1 focuses on resilient, script-friendly downloading with support for dry runs, basic filtering and rate-limit aware API access.

## Features
- OAuth-based authentication using Zoom's Server-to-Server credentials
- Automatic handling of pagination, rate limiting, and download access tokens
- Flexible filtering by date range, host email, or meeting identifier
- Dry-run mode for safe inspection of planned downloads
- Structured logging suitable for automation and monitoring

## Prerequisites
- Python 3.11+
- Access to Zoom Server-to-Server OAuth credentials (Account ID, Client ID, Client Secret)

## Project Layout
```text
src/
├── zoom_scribe/
│   ├── client.py        # Zoom API client logic
│   ├── downloader.py    # Filesystem download orchestration
│   ├── main.py          # CLI entry point
│   └── models.py        # Data models for recordings and assets
└── tests/               # pytest-based unit and integration tests
```

## Installation
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# for development and tests
pip install -r requirements-dev.txt
```

## Configuration
Create a `.env` file in the project root with your Zoom credentials:
```env
ZOOM_ACCOUNT_ID="your_account_id"
ZOOM_CLIENT_ID="your_client_id"
ZOOM_CLIENT_SECRET="your_client_secret"
```
The CLI loads these values automatically at startup.

## Usage
Run the CLI via `python -m zoom_scribe.main`:
```bash
python -m zoom_scribe.main --dry-run
python -m zoom_scribe.main --from 2025-09-01 --to 2025-09-28 --target-dir /path/to/downloads
python -m zoom_scribe.main --host-email host@example.com --target-dir /path/to/downloads
python -m zoom_scribe.main --from 2025-09-27 --overwrite --target-dir /path/to/downloads
```

### Options

| Option | Description | Default |
| --- | --- | --- |
| `--from` | Start date (YYYY-MM-DD) for searching recordings. | 30 days ago |
| `--to` | End date (YYYY-MM-DD) for searching recordings. | Today |
| `--target-dir` | Local directory for downloaded files. | `downloads/` |
| `--host-email` | Filter by host email. | None |
| `--meeting-id` | Filter by meeting ID or UUID. | None |
| `--dry-run` | List planned downloads without writing files. | `False` |
| `--overwrite` | Replace files that already exist on disk. | `False` |
| `--log-level` | Logging verbosity (`debug`, `info`, `warning`, `error`, `critical`). | `info` |
| `--log-format` | Logging format (`auto`, `json`, `text`). | `auto` |

### Logging

`zoom_scribe` emits structured logs under the `zoom_scribe` namespace. When the CLI detects a TTY (e.g., during interactive use), it prints human-readable messages. In non-interactive environments the logger switches to newline-delimited JSON, preserving all contextual `extra` fields (recording UUIDs, asset IDs, rate-limit metadata). You can override the behaviour explicitly with `--log-format json` or `--log-format text`.

Examples:
```bash
python -m zoom_scribe.main --dry-run --log-level debug
python -m zoom_scribe.main --log-format json | jq
```

## Web UI

ZoomScribe now ships with a lightweight web frontend that reuses the existing client and downloader logic. It exposes a FastAPI adapter at `/api/*` and a React single-page app for browsing and triggering downloads.

### Development workflow

```bash
# Start the FastAPI backend (reads the same ZOOM_* environment variables)
uvicorn zoom_scribe.web_api:app --reload

# In another terminal, run the Vite dev server (includes a proxy to the API)
cd web
pnpm install
pnpm dev

# Convenience wrapper
make serve-all
```

Visit `http://localhost:5173` in your browser; API calls are proxied to `http://localhost:8000`. Only `http://localhost:5173` is allowed via CORS during development. In production, run a frontend build and let FastAPI serve the static bundle:

```bash
cd web
pnpm install
pnpm build
uvicorn zoom_scribe.web_api:app
```

The built assets are emitted to `web/dist/` and mounted at `/`, so `GET /` responds with the web UI while `/api/*` continues to serve JSON. The browser never receives Zoom credentials, download URLs, or access tokens—only aggregate metadata. All download work still executes server-side through the existing downloader.

### API surface

- `GET /api/health` → `{"status": "ok"}` for readiness checks.
- `GET /api/recordings` with optional query params `from`, `to`, `host_email`, `meeting_id` returns summaries (UUID, topic, host, start time, duration, asset count, total size).
- `POST /api/download` body `{ "meeting_id_or_uuid": "<uuid>", "overwrite"?: bool, "target_dir"?: str }` triggers an immediate download using the standard CLI semantics and responds with `{ "ok": true, "files_expected": <int>, "note"?: str }`.

Errors are normalised to `{ "message": "…", "code": "…" }`. If Zoom credentials are missing, the API returns HTTP 503 with `"Missing Zoom OAuth credentials"`, mirroring the CLI behaviour.

## Testing
```bash
pytest
```

The test suite covers data models, client behavior (including pagination and rate limiting), downloader path logic, and CLI integration.

## Development
- Install dev dependencies via `pip install -r requirements-dev.txt` inside the virtualenv.
- Run `make lint` to execute ruff, black, isort, mypy, pytest, shellcheck, and shfmt.
- Optional: `pre-commit install` to enforce formatting and static analysis before each commit.

## Troubleshooting
- **Missing credentials**: the CLI exits with `Missing Zoom OAuth credentials` if any `ZOOM_*` variable is absent. Create a `.env` file or export environment variables prior to running the tool.
- **HTTP 429/5xx responses**: the client automatically retries with exponential backoff respecting `Retry-After`. If retries are exhausted, inspect the JSON log output for `request_id` to provide to Zoom support.
- **Permission errors writing to disk**: ensure `--target-dir` points to a writable directory. If the path exists as a file, the CLI aborts with a validation error before downloading.
