# Tasks: Zoom Cloud Recording Downloader

**Input**: Design documents from `/home/michael/dev/ZoomScribe/specs/001-zoom-downloader-p1/`

## Format: `[ID] [P?] Description`
- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions

## Path Conventions
- Source code: `src/zoom_scribe/`
- Tests: `src/tests/`

## Phase 3.1: Setup
- [X] T001 Create the project directory structure in `src/` as defined in `plan.md`.
- [X] T002 [P] Create a `requirements.txt` file with `requests`, `click`, `pytest`, and `python-dotenv`.
- [X] T003 [P] Create a `pyproject.toml` file and configure `ruff` and `black` for code linting and formatting.

## Phase 3.2: Tests First (TDD) ⚠️ MUST COMPLETE BEFORE 3.3
**CRITICAL: These tests MUST be written and MUST FAIL before ANY implementation**
- [X] T004 [P] Write failing unit tests in `src/tests/test_models.py` for the `Recording` and `RecordingFile` data models to ensure correct initialization.
- [X] T005 [P] Write failing unit tests in `src/tests/test_client.py` for the `ZoomAPIClient`. Mock the `requests` library to simulate successful API responses, paginated responses, and HTTP 429 rate limit errors.
- [X] T006 [P] Write failing unit tests in `src/tests/test_downloader.py` for the download logic. Mock filesystem interactions to verify directory creation and file sanitization logic.
- [X] T007 [P] Write failing integration tests in `src/tests/test_main.py` for the CLI. Use `click.testing.CliRunner` to test commands like `--dry-run`, date filtering, and `--overwrite` as described in `quickstart.md`.

## Phase 3.3: Core Implementation (ONLY after tests are failing)
- [X] T008 Implement the `Recording` and `RecordingFile` data classes in `src/zoom_scribe/models.py` to make T004 pass.
- [X] T009 Implement the `ZoomAPIClient` class in `src/zoom_scribe/client.py`. It should handle authentication, request signing, pagination, and the retry logic for rate limiting to make T005 pass.
- [X] T010 Implement the download and file-saving logic in `src/zoom_scribe/downloader.py`, including directory creation and filename sanitization, to make T006 pass.
- [X] T011 Implement the main CLI command group and subcommands in `src/zoom_scribe/main.py` using `click`. Connect the client and downloader modules to make T007 pass.

## Phase 3.4: Integration
- [X] T012 Implement the logic in `src/zoom_scribe/client.py` to load API credentials from a `.env` file using `python-dotenv`.
- [X] T013 Add structured logging to all modules (`main.py`, `client.py`, `downloader.py`) to provide clear, actionable output.

## Phase 3.5: Polish
- [X] T014 [P] Add comprehensive docstrings to all public classes and functions in the `src/zoom_scribe/` directory.
- [X] T015 [P] Create a `README.md` in the root directory, incorporating content from `quickstart.md` to provide a complete overview and usage instructions.
- [X] T016 Run `ruff check . --fix` and `black .` to ensure all code conforms to the project's style guide and fix any remaining issues.

## Dependencies
- **Setup** (T001-T003) must be done first.
- **Tests** (T004-T007) must be written before Core Implementation (T008-T011).
- T008 (`models.py`) is a dependency for T009, T010, and T011.
- T009 (`client.py`) is a dependency for T011.
- T010 (`downloader.py`) is a dependency for T011.

## Parallel Example
```bash
# The following test creation tasks can be run in parallel:
# Task T004: Write failing unit tests in src/tests/test_models.py...
# Task T005: Write failing unit tests in src/tests/test_client.py...
# Task T006: Write failing unit tests in src/tests/test_downloader.py...
# Task T007: Write failing integration tests in src/tests/test_main.py...
```
