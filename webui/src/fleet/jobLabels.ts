import type { Job } from "../shared/contracts";
import type { BadgeTone } from "../shared/ui";
import { timeAgo } from "./formatters";

const ACTION_LABELS: Record<string, string> = {
  install_release: "Install release",
  mirror_now: "Mirror runs",
  restart_takt: "Restart TAKT",
  start_takt: "Start TAKT",
  stop_takt: "Stop TAKT",
  reboot_device: "Reboot device",
  shutdown_device: "Shut down device",
  collect_diagnostics: "Collect diagnostics",
  run_health_checks: "Health check",
  curate_run: "Adjust run",
  add_wifi_network: "Add Wi-Fi network",
};

export function jobActionLabel(action: string): string {
  return ACTION_LABELS[action] || action.replaceAll("_", " ");
}

const STATUS_LABELS: Record<string, string> = {
  queued: "Waiting",
  claimed: "Running",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  rolled_back: "Rolled back",
  cancelled: "Cancelled",
};

export function jobStatusLabel(status: string): string {
  return STATUS_LABELS[status] || status;
}

export function jobStatusTone(status: string): BadgeTone {
  switch (status) {
    case "succeeded":
      return "success";
    case "failed":
    case "rolled_back":
      return "danger";
    case "cancelled":
      return "neutral";
    default:
      return "accent";
  }
}

// Queued jobs wait for their device indefinitely (see the registry's job
// queue), so the operator's only signal something might be wrong is this
// detail line -- it distinguishes "device is offline" from "device is busy"
// from the ordinary case of just not having polled yet.
export function jobWaitingDetail(job: Job): string | null {
  if (job.status !== "queued") return null;
  if (job.stage === "waiting_for_safe_state") {
    return "Waiting for a safe state — the timer is busy";
  }
  if (job.device_online === false) {
    return `Device is offline (last seen ${timeAgo(job.device_last_seen_at)})`;
  }
  return "Waiting for the device to claim this job";
}
