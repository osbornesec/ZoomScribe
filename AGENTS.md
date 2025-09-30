# Repository Guidelines

## Project Structure & Module Organization
- `src/zoom_scribe/` contains the client, downloader, and models for interacting with Zoom cloud recordings.
- `src/tests/` holds pytest suites mirroring the package layout; add new tests beside related modules.
- Specs live under `specs/`; keep product documentation edits separate from code changes.
- Virtual environment artifacts sit in `.venv/` (ignored by Git); activate it during local development.

## Build, Test, and Development Commands
- `.venv/bin/python -m zoom_scribe.main --dry-run` — smoke-test listing logic without writing files. Set `ZOOM_*` env vars or update `.env` first.
- `.venv/bin/pytest` — run the full unit-test suite. Use `-k <pattern>` for targeted runs.
- `.venv/bin/pip install -r requirements.txt` — install runtime and test dependencies inside the venv.

## Coding Style & Naming Conventions
- Python code targets 3.12, formatted to 88 columns with Ruff + Black defaults.
- Use descriptive snake_case for functions/vars; PascalCase for classes (e.g., `ZoomAPIClient`).
- Prefer small, focused modules and keep comments minimal—only when logic isn’t self-evident.

## Testing Guidelines
- Tests use pytest; name files `test_<module>.py` and functions `test_<behavior>()`.
- Cover API edge cases (pagination, retries, double-encoded UUIDs) when modifying client logic.
- CI expects all tests to pass locally before pushing; include new tests for bug fixes or features.
- After finishing code changes, run formatting, linting, type checking, and the full test suite locally to catch issues early.

## Commit & Pull Request Guidelines
- Follow concise commits (imperative mood, e.g., “Add recurring meeting lookup”); group related changes together.
- PRs should summarize scope, cite relevant specs/issues, and list manual/automated checks run.
- Include screenshots or logs when behavior changes (e.g., new CLI output).

## Security & Configuration Tips
- Keep Zoom credentials in `.env` only; never commit secrets.
- Server-to-server OAuth scopes needed: `recording:read:admin` and `meeting:read:admin`.
- Use dry-run mode to validate filters before downloading sensitive assets.

## Automation & Review
- At the end of each change cycle, run `coderabbit review --base master --prompt-only -t all` (allow up to 15 minutes) and address the reported findings.
