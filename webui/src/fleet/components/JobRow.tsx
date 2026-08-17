import { Check, RefreshCw, RotateCcw, Trash2, X } from "lucide-react";
import type { Job } from "../../shared/contracts";
import { Button } from "../../shared/ui";
import { bytes, timeAgo } from "../formatters";

interface JobRowProps {
  job: Job;
  onCancel: (job: Job) => void;
  onRetry: (job: Job) => void;
  onForceClear: (job: Job) => void;
  onDelete: (job: Job) => void;
}

export function JobRow({ job, onCancel, onRetry, onForceClear, onDelete }: JobRowProps) {
  const active = ["queued", "claimed", "running"].includes(job.status);
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  return (
    <div className="job-row">
      <div className={`job-icon status-${job.status}`}>{active ? <RefreshCw size={15} /> : job.status === "succeeded" ? <Check size={15} /> : <X size={15} />}</div>
      <div className="job-copy">
        <strong>{job.action.replaceAll("_", " ")}</strong>
        <span>{job.device_name} · {job.stage?.replaceAll("_", " ") || job.status} · {job.message || job.status}{job.bytes_total != null ? ` · ${bytes(job.bytes_downloaded || 0)} / ${bytes(job.bytes_total)}` : ""}{(job.attempt ?? 0) > 1 ? ` · attempt ${job.attempt}` : ""}</span>
      </div>
      <progress
        className="job-progress"
        max={100}
        value={progress}
        aria-label={`${job.action.replaceAll("_", " ")} progress`}
      />
      {job.action === "install_release" && active && !["activating", "restarting", "health_checking"].includes(job.stage ?? "") && (
        <Button variant="secondary" onClick={() => onCancel(job)}>CANCEL</Button>
      )}
      {active && <Button variant="danger" onClick={() => onForceClear(job)}>FORCE CLEAR</Button>}
      {job.action !== "add_wifi_network" && ["rolled_back", "failed", "cancelled"].includes(job.status) && (
        <Button variant="secondary" onClick={() => onRetry(job)}><RotateCcw size={14} /> RETRY</Button>
      )}
      {!active && <Button variant="secondary" onClick={() => onDelete(job)}><Trash2 size={14} /> REMOVE</Button>}
      <time>{timeAgo(job.updated_at)}</time>
    </div>
  );
}
