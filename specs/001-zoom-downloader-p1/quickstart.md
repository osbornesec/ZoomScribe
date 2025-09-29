# Quickstart: Zoom Cloud Recording Downloader

This guide provides instructions on how to set up and run the Zoom Cloud Recording Downloader tool.

## 1. Prerequisites

- Python 3.11+
- `pip` for package installation

## 2. Installation

1.  **Clone the repository** (if you haven't already):
    ```bash
    git clone https://github.com/osbornesec/ZoomScribe.git
    cd ZoomScribe
    ```

2.  **Set up a virtual environment** (recommended):
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
    *(Note: The `requirements.txt` file will be created during the implementation phase.)*

## 3. Configuration

The tool requires Zoom API credentials, which should be provided via environment variables. Create a `.env` file in the project root:

```
ZOOM_ACCOUNT_ID="your_account_id"
ZOOM_CLIENT_ID="your_client_id"
ZOOM_CLIENT_SECRET="your_client_secret"
```

The tool will load these variables automatically.

## 4. Usage

The tool is run from the command line.

### Basic Commands

-   **List recordings from the last 30 days (dry run)**:
    ```bash
    python -m zoom_scribe.main --dry-run
    ```

-   **Download recordings from a specific date range**:
    ```bash
    python -m zoom_scribe.main --from 2025-09-01 --to 2025-09-28 --target-dir /path/to/downloads
    ```

-   **Download recordings for a specific host**:
    ```bash
    python -m zoom_scribe.main --host-email host@example.com --target-dir /path/to/downloads
    ```

-   **Force overwrite of existing files**:
    ```bash
    python -m zoom_scribe.main --from 2025-09-27 --overwrite --target-dir /path/to/downloads
    ```

### Command-Line Arguments

| Argument | Description | Default |
|---|---|---|
| `--from` | Start date (YYYY-MM-DD) for the recording search. | 30 days ago |
| `--to` | End date (YYYY-MM-DD) for the recording search. | Today |
| `--target-dir` | The local directory to save downloaded files. | `downloads/` |
| `--host-email` | Filter recordings by a specific host's email. | None |
| `--meeting-id` | Filter by a specific meeting ID or UUID. | None |
| `--dry-run` | List what would be downloaded without downloading. | `False` |
| `--overwrite` | Overwrite files if they already exist in the target directory. | `False` |
