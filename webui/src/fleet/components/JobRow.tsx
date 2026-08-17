// @ts-nocheck
import { Check, RefreshCw, RotateCcw, X } from "lucide-react";
import { bytes, timeAgo } from "../formatters";

export function JobRow({ job, onCancel, onRetry, onForceClear }) {
  const active = ["queued", "claimed", "running"].includes(job.status);
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  return (
    <div className="job-row">
      <div className={`job-icon status-${job.status}`}>{active ? <RefreshCw size={15} /> : job.status === "succeeded" ? <Check size={15} /> : <X size={15} />}</div>
      <div className="job-copy">
        <strong>{job.action.replaceAll("_", " ")}</strong>
        <span>{job.device_name} · {job.stage?.replaceAll("_", " ") || job.status} · {job.message || job.status}{job.bytes_total != null ? ` · ${bytes(job.bytes_downloaded || 0)} / ${bytes(job.bytes_total)}` : ""}{job.attempt > 1 ? ` · attempt ${job.attempt}` : ""}</span>
      </div>
      <progress
        className="job-progress"
        max="100"
        value={progress}
        aria-label={`${job.action.replaceAll("_", " ")} progress`}
      />
      {job.action === "install_release" && active && !["activating", "restarting", "health_checking"].includes(job.stage) && <button className="secondary-button" onClick={() => onCancel(job)}>CANCEL</button>}
      {active && <button className="secondary-button danger-action" onClick={() => onForceClear(job)}>FORCE CLEAR</button>}
      {job.action !== "add_wifi_network" && ["rolled_back", "failed", "cancelled"].includes(job.status) && <button className="secondary-button" onClick={() => onRetry(job)}><RotateCcw size={14} /> RETRY</button>}
      <time>{timeAgo(job.updated_at)}</time>
    </div>
  );
}
