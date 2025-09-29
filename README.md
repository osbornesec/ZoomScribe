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
```
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

## Testing
```bash
pytest
```

The test suite covers data models, client behavior (including pagination and rate limiting), downloader path logic, and CLI integration.
