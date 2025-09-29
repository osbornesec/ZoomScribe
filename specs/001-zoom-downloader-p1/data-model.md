# Data Model: Zoom Cloud Recording Downloader

This document outlines the key data entities for the Zoom Cloud Recording Downloader, as derived from the feature specification.

## 1. Recording

Represents a single Zoom cloud recording session.

**Attributes**:

| Name | Type | Description | Example |
|---|---|---|---|
| `uuid` | String | The unique identifier for the meeting instance. | `abc123def456==` |
| `meeting_topic` | String | The topic or name of the meeting. | `My Important Meeting` |
| `host_email` | String | The email address of the meeting host. | `host@example.com` |
| `start_time` | DateTime (ISO 8601) | The start time of the recording. | `2025-09-28T10:00:00Z` |
| `recording_files` | Array<RecordingFile> | A list of associated recording file objects. | `[...]` |

## 2. Recording File

Represents a single downloadable file associated with a recording.

**Attributes**:

| Name | Type | Description | Example |
|---|---|---|---|
| `id` | String | The unique identifier for the recording file. | `zyx987wv_t-s` |
| `file_type` | String | The type of the recording file. | `MP4`, `M4A`, `VTT` |
| `file_extension` | String | The file extension of the recording file. | `MP4`, `M4A`, `VTT` |
| `download_url` | String (URL) | The URL to download the file. | `https://zoom.us/rec/download/...` |
| `download_access_token` | String | An optional access token for downloading the file. | `long_secure_token` |
