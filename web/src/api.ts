import type {
  ApiErrorPayload,
  DownloadResponsePayload,
  DownloadTrigger,
  RecordingSummary,
  RecordingsQuery
} from "./types";

export class ApiClientError extends Error {
  status: number;
  code?: string | null;

  constructor(message: string, status: number, code?: string | null) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
  }
}

async function request<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    ...init
  });

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const payload = data as ApiErrorPayload | null;
    const message = payload?.message ?? `Request failed with status ${response.status}`;
    throw new ApiClientError(message, response.status, payload?.code ?? null);
  }

  return data as T;
}

export async function fetchRecordings(params: RecordingsQuery): Promise<RecordingSummary[]> {
  const search = new URLSearchParams();
  if (params.from) {
    search.append("from", params.from);
  }
  if (params.to) {
    search.append("to", params.to);
  }
  if (params.hostEmail) {
    search.append("host_email", params.hostEmail);
  }
  if (params.meetingId) {
    search.append("meeting_id", params.meetingId);
  }

  const url = `/api/recordings${search.toString() ? `?${search.toString()}` : ""}`;
  return request<RecordingSummary[]>(url, { method: "GET" });
}

export async function triggerDownload(payload: DownloadTrigger): Promise<DownloadResponsePayload> {
  return request<DownloadResponsePayload>("/api/download", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}
