export interface RecordingSummary {
  uuid: string;
  meeting_id: string | null;
  topic: string;
  host_email: string | null;
  start_time: string;
  duration_minutes: number | null;
  asset_count: number;
  total_size_bytes: number;
}

export interface ApiErrorPayload {
  message: string;
  code?: string | null;
}

export interface RecordingsQuery {
  from?: string;
  to?: string;
  hostEmail?: string;
  meetingId?: string;
}

export interface DownloadTrigger {
  meeting_id_or_uuid: string;
  overwrite?: boolean;
  target_dir?: string;
}

export interface DownloadResponsePayload {
  ok: boolean;
  files_expected: number;
  note?: string | null;
}
