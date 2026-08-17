import { Ban, Check, RefreshCw, RotateCcw, Trash2, X } from "lucide-react";
import type { Job } from "../../shared/contracts";
import { Badge, IconButton } from "../../shared/ui";
import { bytes, timeAgo } from "../formatters";
import { jobActionLabel, jobStatusLabel, jobStatusTone, jobWaitingDetail } from "../jobLabels";

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
  const showProgress = active && job.status !== "queued" && progress > 0;
  const waitingDetail = jobWaitingDetail(job);
  const detail = waitingDetail || job.stage?.replaceAll("_", " ") || job.message || job.status;
  const canCancel =
    job.action === "install_release"
    && active
    && !["activating", "restarting", "health_checking"].includes(job.stage ?? "");
  const canRetry =
    job.action !== "add_wifi_network" && ["rolled_back", "failed", "cancelled"].includes(job.status);
  const actionLabel = jobActionLabel(job.action);

  return (
    <div className={`job-row status-${job.status}`}>
      <Badge tone={jobStatusTone(job.status)} className="job-status-badge">
        {active ? <RefreshCw size={13} /> : job.status === "succeeded" ? <Check size={13} /> : <X size={13} />}
        {jobStatusLabel(job.status)}
      </Badge>
      <div className="job-copy">
        <strong>{actionLabel}</strong>
        <span className="job-device">{job.device_name}</span>
        <span className="job-meta" title={job.message || undefined}>
          {detail}
          {job.bytes_total != null ? ` · ${bytes(job.bytes_downloaded || 0)} / ${bytes(job.bytes_total)}` : ""}
          {(job.attempt ?? 0) > 1 ? ` · attempt ${job.attempt}` : ""}
        </span>
        {showProgress && (
          <progress
            className="job-progress"
            max={100}
            value={progress}
            aria-label={`${actionLabel} progress`}
          />
        )}
      </div>
      <div className="job-actions">
        {canCancel && (
          <IconButton
            variant="secondary"
            icon={<X size={14} />}
            aria-label="Cancel"
            title="Cancel"
            onClick={() => onCancel(job)}
          />
        )}
        {active && (
          <IconButton
            variant="danger"
            icon={<Ban size={14} />}
            aria-label="Force clear"
            title="Force clear (device outcome unknown)"
            onClick={() => onForceClear(job)}
          />
        )}
        {canRetry && (
          <IconButton
            variant="secondary"
            icon={<RotateCcw size={14} />}
            aria-label="Retry"
            title="Retry"
            onClick={() => onRetry(job)}
          />
        )}
        {!active && (
          <IconButton
            variant="ghost"
            icon={<Trash2 size={14} />}
            aria-label="Remove"
            title="Remove"
            onClick={() => onDelete(job)}
          />
        )}
      </div>
      <time>{timeAgo(job.updated_at)}</time>
    </div>
  );
}
