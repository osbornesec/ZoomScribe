# Feature Specification: Zoom Cloud Recording Downloader — Phase 1

**Feature Branch**: `feat/zoom-downloader-p1`
**Created**: 2025-09-28
**Status**: Draft
**Input**: User description: "Zoom Cloud Recording Downloader — Phase 1 (download only) WHAT & WHY Build a small, reliable tool that downloads Zoom **cloud recordings** for the authorized account so we can back them up and process them offline. This first phase focuses only on listing and downloading recordings (no UI, no editing, no transcripts). It must be resilient, secure, and easy to run on a schedule. SCOPE (Phase 1) - Let me fetch recordings by: - time range: YYYY‑MM‑DD → YYYY‑MM‑DD (default: last 30 days) - optional host filter (email) OR meeting ID/UUID when I know it - List recordings and download each selected `recording_file` to a target directory on disk using a clean path scheme: `{host_email}/{YYYY}/{MM}/{DD}/{meeting_topic}-{meeting_uuid}/{file_type}-{start_time}.{ext}` - Respect Zoom’s API behavior: - “List recordings for a user” for the authorized user (`/users/me/recordings`) or account‑wide listing when we have admin scopes (Phase 1 should support either). - If given a meeting ID (not UUID), only the **latest** instance’s recordings are returned; to fetch other instances, enumerate `/past_meetings/{meetingId}/instances` and then call `/meetings/{uuid}/recordings` for each. - If a meeting UUID begins with “/” or contains “//”, **double‑URL‑encode** it when used in path params. - To download a file, use the `recording_files[].download_url`. Handle passcode‑protected or restricted downloads by appending the OAuth **access token** as `?access_token=...` or use `download_access_token` when available. - Support `include_fields=download_access_token&ttl=<seconds>` when calling `/meetings/{id|uuid}/recordings` so downloads can work without interactive passcode prompts. - Pagination & limits: - Implement pagination (`page_size`, `next_page_token`) and graceful handling of HTTP 429 (per‑second or daily) with backoff and retry. - Security & Ops: - Use OAuth 2.0. Prefer **Server‑to‑Server OAuth** for account‑wide use; JWT is deprecated. Never log tokens or embed them in files or URLs we store. - Honor org settings that may restrict downloads (e.g., “only host can download”); surface a clear error when policy prevents download. - Provide a dry‑run mode that lists exactly what would be downloaded. - Provide idempotency: skip files that already exist unless `--overwrite` is set. - Out of scope for Phase 1: deletion, S3 uploads, transcripts/CC parsing, thumbnails, share‑link management, or UI. ACCEPTANCE CRITERIA - Given valid OAuth credentials and a time range with at least one cloud recording, running the tool: - Lists found meetings and the number of `recording_files` detected. - Downloads MP4/M4A/VTT/TXT `recording_files` to the target directory using the naming scheme above. - Handles a passcode‑protected recording by successfully downloading it using the access token (or `download_access_token` when requested). - Demonstrates correct handling of pagination (>= 50 files) and a simulated 429 by backing off and succeeding on retry. - Demonstrates correct handling of a UUID that requires **double‑encoding**. - Produces logs without secrets and exits non‑zero on any skipped download due to permissions, with actionable messages. NON‑NEGOTIABLES - Follow the project constitution’s quality gates for tests, linting, types, and security. - No stack choice is implied here; choose a simple, maintainable approach. Keep diffs small and reversible. FUTURE PHASES (not now) - Push to S3 with server‑side encryption, retention tags. - Automatic transcript and chat extraction. - Policy‑driven cleanup/archiving."

## Clarifications
### Session 2025-09-28
- Q: How should the tool handle a `meeting_topic` that contains characters that are invalid for a directory or file name (e.g., `/`, `\`, `?`)? → A: Sanitize the topic by replacing all invalid characters with an underscore (`_`).
- Q: What is the retry policy for API failures like HTTP 429 (rate limiting)? → A: A fixed number of retries (e.g., 3) with exponential backoff.
- Q: If the specified target directory for downloads does not exist, how should the tool behave? → A: Automatically create the entire directory path.
- Q: If the OAuth 2.0 access token is invalid or expired during an API call, how should the tool respond? → A: Prompt the user for new credentials.
- Q: If a download of a `recording_file` fails for reasons other than permissions (e.g., network error, file corruption), how should the tool behave? → A: Retry the download a fixed number of times (e.g., 3) before failing.

---

## ⚡ Quick Guidelines
- ✅ Focus on WHAT users need and WHY
- ❌ Avoid HOW to implement (no tech stack, APIs, code structure)
- 👥 Written for business stakeholders, not developers

---

## User Scenarios & Testing *(mandatory)*

### Primary User Story
Build a small, reliable tool that downloads Zoom cloud recordings for the authorized account so we can back them up and process them offline. This first phase focuses only on listing and downloading recordings. It must be resilient, secure, and easy to run on a schedule.

### Acceptance Scenarios
1.  **Given** valid OAuth credentials and a time range with at least one cloud recording, **When** the tool is run, **Then** it lists the found meetings and the number of `recording_files` detected.
2.  **Given** the tool has listed recordings, **When** the download process is initiated, **Then** it downloads all MP4, M4A, VTT, and TXT `recording_files` to the target directory using the specified naming scheme.
3.  **Given** a recording is passcode-protected, **When** the tool attempts to download it, **Then** it successfully downloads the file by using the `access_token` or `download_access_token`.
4.  **Given** an API response contains more results than the page size (e.g., >50), **When** the tool processes the response, **Then** it correctly handles pagination to retrieve all recordings.
5.  **Given** the Zoom API returns an HTTP 429 (rate limit) error, **When** the tool receives the error, **Then** it backs off exponentially before retrying the request up to 3 times.
6.  **Given** a meeting UUID requires double-encoding (contains "//" or starts with "/"), **When** the tool makes an API call with that UUID, **Then** it correctly double-encodes the UUID in the URL path.
7.  **Given** a download is skipped due to permissions, **When** the tool finishes, **Then** it exits with a non-zero status code and provides a clear, actionable error message in the logs.

### Edge Cases
- What happens when the API returns a 429 rate limit error? -> The tool must back off and retry up to 3 times.
- What happens if a file already exists in the target directory? -> The tool must skip the file unless an `--overwrite` flag is set.
- How does the system handle a download restricted by organization policy? -> It must surface a clear error and exit with a non-zero status code.
- How does the system handle meeting UUIDs that need special URL encoding? -> It must double-URL-encode them correctly.
- What happens if an invalid meeting ID or host email is provided? -> The tool should report that no recordings were found.

---

## Requirements *(mandatory)*

### Functional Requirements
- **FR-001**: System MUST fetch recordings within a user-specified time range, defaulting to the last 30 days.
- **FR-002**: System MUST allow filtering recordings by an optional host filter (email), a meeting ID, or a meeting UUID.
- **FR-003**: System MUST download recording files to a target directory using the path scheme: `{host_email}/{YYYY}/{MM}/{DD}/{meeting_topic}-{meeting_uuid}/{file_type}-{start_time}.{ext}`.
- **FR-004**: System MUST handle Zoom API pagination for lists of recordings using `page_size` and `next_page_token`.
- **FR-005**: System MUST implement a backoff-and-retry mechanism for HTTP 429 rate-limiting responses, attempting a maximum of 3 retries with an exponential backoff strategy.
- **FR-006**: System MUST authenticate using Server-to-Server OAuth 2.0.
- **FR-007**: System MUST NOT log sensitive information, including OAuth tokens.
- **FR-008**: System MUST provide a `--dry-run` mode that lists files to be downloaded without downloading them.
- **FR-009**: System MUST provide idempotency by skipping existing files unless an `--overwrite` flag is specified.
- **FR-010**: System MUST correctly double-URL-encode meeting UUIDs that start with `/` or contain `//`.
- **FR-011**: System MUST use the `download_access_token` or an OAuth `access_token` to download passcode-protected or restricted recordings.
- **FR-012**: System MUST support both user-level (`/users/me/recordings`) and account-level (admin) listing of recordings.
- **FR-013**: System MUST, if given a meeting ID (not UUID), enumerate all past instances of the meeting to find all possible recordings.
- **FR-014**: System MUST sanitize the `meeting_topic` for use in file and directory paths by replacing any invalid characters with an underscore (`_`).
- **FR-015**: System MUST automatically create the target directory path if it does not already exist.
- **FR-016**: System MUST prompt the user for new credentials if the OAuth 2.0 access token is invalid or expired.
- **FR-017**: System MUST retry a failed download of a `recording_file` (for reasons other than permissions) a fixed number of times (e.g., 3) before marking it as a permanent failure.

### Key Entities *(include if feature involves data)*
- **Recording**: Represents a single Zoom cloud recording session. Key attributes include meeting topic, meeting UUID, host email, start time, and a list of associated recording files.
- **Recording File**: Represents a single downloadable file associated with a recording. Key attributes include file type (e.g., MP4, M4A), file extension, and a unique download URL.

---

## Review & Acceptance Checklist
*GATE: Automated checks run during main() execution*

### Content Quality
- [ ] No implementation details (languages, frameworks, APIs)
- [ ] Focused on user value and business needs
- [ ] Written for non-technical stakeholders
- [ ] All mandatory sections completed

### Requirement Completeness
- [ ] No [NEEDS CLARIFICATION] markers remain
- [ ] Requirements are testable and unambiguous
- [ ] Success criteria are measurable
- [ ] Scope is clearly bounded
- [ ] Dependencies and assumptions identified

---