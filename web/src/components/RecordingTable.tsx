import type { RecordingSummary } from "../types";

interface RecordingTableProps {
  recordings: RecordingSummary[];
  pendingDownloads: Set<string>;
  onDownload: (recording: RecordingSummary) => void;
}

function formatBytes(size: number): string {
  if (!size) {
    return "—";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = size;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value < 10 && unitIndex > 0 ? 1 : 0)} ${units[unitIndex]}`;
}

function formatDuration(minutes: number | null): string {
  if (minutes == null) {
    return "—";
  }
  if (minutes < 60) {
    return `${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
}

const RecordingTable = ({ recordings, pendingDownloads, onDownload }: RecordingTableProps): JSX.Element => {
  if (!recordings.length) {
    return (
      <div className="empty-state">
        <strong>No recordings yet.</strong>
        Adjust your filters and try again.
      </div>
    );
  }

  return (
    <div className="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Topic</th>
            <th>Host</th>
            <th>Start</th>
            <th>Duration</th>
            <th>Assets</th>
            <th>Total Size</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {recordings.map((recording) => {
            const pending = pendingDownloads.has(recording.uuid);
            const host = recording.host_email ?? "—";
            const startDate = new Date(recording.start_time);
            return (
              <tr key={recording.uuid}>
                <td>
                  <div>{recording.topic || "Untitled"}</div>
                  <div className="badge muted">UUID {recording.uuid}</div>
                </td>
                <td>{host}</td>
                <td>{startDate.toLocaleString()}</td>
                <td>{formatDuration(recording.duration_minutes)}</td>
                <td>
                  <span className="badge">{recording.asset_count}</span>
                </td>
                <td>{formatBytes(recording.total_size_bytes)}</td>
                <td>
                  <div className="recording-actions">
                    <button
                      type="button"
                      onClick={() => onDownload(recording)}
                      disabled={pending}
                    >
                      {pending ? "Downloading…" : "Download"}
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="table-footer">
        <span>{recordings.length} result{recordings.length === 1 ? "" : "s"}</span>
        <span>Downloads run through the server; credentials stay on the backend.</span>
      </div>
    </div>
  );
};

export default RecordingTable;
