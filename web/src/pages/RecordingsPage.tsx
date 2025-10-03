import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { fetchRecordings, triggerDownload, ApiClientError } from "../api";
import RecordingTable from "../components/RecordingTable";
import type { RecordingSummary } from "../types";

interface BannerState {
  kind: "info" | "success";
  message: string;
}

const daysBetween = 30;

function formatDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function defaultFrom(): string {
  const now = new Date();
  const prior = new Date(now.getTime() - daysBetween * 24 * 60 * 60 * 1000);
  return formatDate(prior);
}

function defaultTo(): string {
  return formatDate(new Date());
}

const RecordingsPage = (): JSX.Element => {
  const [from, setFrom] = useState<string>(defaultFrom);
  const [to, setTo] = useState<string>(defaultTo);
  const [hostEmail, setHostEmail] = useState<string>("");
  const [meetingId, setMeetingId] = useState<string>("");
  const [records, setRecords] = useState<RecordingSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<BannerState | null>(null);
  const [pendingDownloads, setPendingDownloads] = useState<Set<string>>(new Set());

  const query = useMemo(
    () => ({
      from: from || undefined,
      to: to || undefined,
      hostEmail: hostEmail.trim() || undefined,
      meetingId: meetingId.trim() || undefined
    }),
    [from, to, hostEmail, meetingId]
  );

  const loadRecordings = useCallback(async () => {
    setLoading(true);
    setError(null);
    setBanner(null);
    try {
      const data = await fetchRecordings(query);
      setRecords(data);
      if (!data.length) {
        setBanner({ kind: "info", message: "No recordings matched your filters." });
      }
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.message);
      } else {
        setError("Failed to load recordings. Please retry.");
      }
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    void loadRecordings();
  }, [loadRecordings]);

  const resetFilters = () => {
    setFrom(defaultFrom());
    setTo(defaultTo());
    setHostEmail("");
    setMeetingId("");
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await loadRecordings();
  };

  const handleDownload = async (recording: RecordingSummary) => {
    const identifier = recording.uuid;
    setPendingDownloads((prev) => {
      const next = new Set(prev);
      next.add(identifier);
      return next;
    });
    setError(null);
    setBanner(null);
    try {
      const response = await triggerDownload({ meeting_id_or_uuid: identifier });
      const note = response.note ?? `Download triggered for ${recording.topic || recording.uuid}.`;
      setBanner({ kind: "success", message: note });
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.message);
      } else {
        setError("Download failed. Inspect server logs for details.");
      }
    } finally {
      setPendingDownloads((prev) => {
        const next = new Set(prev);
        next.delete(identifier);
        return next;
      });
    }
  };

  return (
    <div className="container">
      <div className="card">
        <div className="card-header">
          <h1>ZoomScribe Web</h1>
          <p className="tagline">Browse Zoom cloud recordings and trigger downloads on your server.</p>
        </div>
        <div className="card-body">
          {error && <div className="status-banner error">{error}</div>}
          {banner && <div className={`status-banner ${banner.kind}`}>{banner.message}</div>}

          <form className="filters" onSubmit={handleSubmit}>
            <div className="filters-grid">
              <label>
                From
                <input
                  type="date"
                  value={from}
                  onChange={(event) => setFrom(event.target.value)}
                  max={to || undefined}
                />
              </label>
              <label>
                To
                <input
                  type="date"
                  value={to}
                  onChange={(event) => setTo(event.target.value)}
                  min={from || undefined}
                />
              </label>
              <label>
                Host Email
                <input
                  type="email"
                  placeholder="host@example.com"
                  value={hostEmail}
                  onChange={(event) => setHostEmail(event.target.value)}
                />
              </label>
              <label>
                Meeting ID or UUID
                <input
                  type="text"
                  placeholder="Zoom meeting UUID"
                  value={meetingId}
                  onChange={(event) => setMeetingId(event.target.value)}
                />
              </label>
            </div>
            <div className="actions">
              <button className="primary-button" type="submit" disabled={loading}>
                {loading ? "Searching…" : "Search"}
              </button>
              <button className="secondary-button" type="button" onClick={resetFilters} disabled={loading}>
                Reset
              </button>
            </div>
          </form>

          <RecordingTable
            recordings={records}
            pendingDownloads={pendingDownloads}
            onDownload={handleDownload}
          />
        </div>
        <div className="card-footer">
          FastAPI enforces server-side OAuth. Secrets stay on the backend; the browser only sees summaries.
        </div>
      </div>
    </div>
  );
};

export default RecordingsPage;
